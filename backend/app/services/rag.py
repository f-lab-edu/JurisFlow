import hashlib
import logging
import threading
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import chromadb
import pymupdf
import pymupdf4llm
from filelock import FileLock

from backend.app.core.config import Settings
from backend.app.schemas.rag import IngestResult, SearchHit
from backend.app.services.chunking import chunk_markdown
from backend.app.services.embedding import Embedder

logger = logging.getLogger(__name__)
# PyMuPDF does not support concurrent use from multiple threads.
PDF_LOCK = threading.Lock()


def safe_name(filename: str) -> str:
    name = unicodedata.normalize("NFC", filename.replace("\\", "/").split("/")[-1])
    return "".join(c for c in name if c.isprintable())[:240] or "upload.pdf"


class RagService:
    def __init__(self, settings: Settings, embedder=None):
        self.settings = settings
        self._embedder = embedder
        self._client = None
        self._collection = None
        self._lock = threading.RLock()

    def _initialize(self):
        if self._collection is not None:
            return
        s = self.settings
        self._embedder = self._embedder or Embedder(s)
        self._client = chromadb.PersistentClient(path=str(s.chroma_path))
        expected = {
            "embedding_model": s.embedding_model,
            "embedding_dimension": self._embedder.dimension,
            "pipeline": "markdown-page-v1",
            "chunk_size": s.chunk_size,
            "chunk_overlap": s.chunk_overlap,
            "hnsw:space": "cosine",
        }
        collection = self._client.get_or_create_collection(
            name=s.collection,
            metadata=expected,
            embedding_function=None,
        )
        if collection.metadata != expected:
            raise ValueError(
                "Collection 설정이 다릅니다. 새로운 RAG_COLLECTION 이름을 사용하세요."
            )
        self._collection = collection

    def ingest(self, path: Path, filename: str | None = None) -> IngestResult:
        name = safe_name(filename or path.name)
        result = IngestResult(filename=name, status="failed")
        try:
            if Path(name).suffix.lower() != ".pdf":
                raise ValueError("PDF 확장자만 허용됩니다.")
            if path.stat().st_size > self.settings.max_file_mb * 1024 * 1024:
                raise ValueError("파일 크기 제한을 초과했습니다.")
            with path.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise ValueError("PDF 파일 시그니처가 올바르지 않습니다.")
                stream.seek(0)
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            result.document_id = digest
            with PDF_LOCK, pymupdf.open(path) as doc:
                if doc.needs_pass:
                    raise ValueError("암호화된 PDF는 지원하지 않습니다.")
                result.pages = len(doc)
                if not 0 < len(doc) <= self.settings.max_pages:
                    raise ValueError("PDF 페이지 수 제한을 초과했거나 빈 문서입니다.")
                pages = pymupdf4llm.to_markdown(doc, page_chunks=True, use_ocr=False)
            empty = [i for i, page in enumerate(pages, 1) if not page["text"].strip()]
            if len(empty) == len(pages):
                raise ValueError(
                    "추출 가능한 텍스트가 없습니다. OCR은 지원하지 않습니다."
                )
            if empty:
                result.warnings.append(
                    f"텍스트가 없는 페이지를 건너뜀(OCR 미지원): {empty}"
                )
            # Serialize writes across processes and threads; Chroma is embedded.
            self.settings.chroma_path.mkdir(parents=True, exist_ok=True)
            with (
                self._lock,
                FileLock(str(self.settings.chroma_path / "ingest.lock"), timeout=120),
            ):
                self._initialize()
                collection = self._collection
                texts, metadata, ids = [], [], []
                now = datetime.now(UTC).isoformat()
                for page_number, page in enumerate(pages, 1):
                    for chunk in chunk_markdown(
                        page["text"],
                        self.settings.chunk_size,
                        self.settings.chunk_overlap,
                        self._embedder.fits,
                    ):
                        index = len(texts)
                        chunk_id = f"{digest}:{index}"
                        texts.append(chunk)
                        ids.append(chunk_id)
                        metadata.append(
                            {
                                "document_id": digest,
                                "file_hash": digest,
                                "filename": name,
                                "page": page_number,
                                "chunk_index": index,
                                "chunk_id": chunk_id,
                                "processed_at": now,
                                "embedding_model": self.settings.embedding_model,
                            }
                        )
                result.chunks_created = len(texts)
                existing = collection.get(where={"document_id": digest}, include=[])[
                    "ids"
                ]
                if set(existing) == set(ids):
                    result.status = "skipped"
                    return result
                # Embed everything before writing. A retry repairs any interrupted partial write.
                vectors = self._embedder.documents(texts)
                if len(vectors) != len(texts) or any(
                    len(v) != self._embedder.dimension for v in vectors
                ):
                    raise ValueError("임베딩 개수 또는 차원이 올바르지 않습니다.")
                batch = min(self.settings.batch_size, self._client.get_max_batch_size())
                try:
                    for start in range(0, len(ids), batch):
                        end = start + batch
                        collection.upsert(
                            ids=ids[start:end],
                            documents=texts[start:end],
                            embeddings=vectors[start:end],
                            metadatas=metadata[start:end],
                        )
                except Exception:
                    collection.delete(where={"document_id": digest})
                    raise
                result.status = "stored"
                result.chunks_stored = len(ids)
        except Exception as exc:
            logger.exception("PDF ingestion failed: %s", name)
            result.error = (
                str(exc)
                if isinstance(exc, ValueError)
                else "PDF 처리 또는 저장 실패. 서버 로그를 확인하세요."
            )
        return result

    def search(self, query: str, limit: int = 5, document_id: str | None = None):
        if not query.strip():
            raise ValueError("검색어를 입력하세요.")
        with self._lock:
            self._initialize()
            if not self._collection.count():
                return []
            result = self._collection.query(
                query_embeddings=[self._embedder.query(query)],
                n_results=limit,
                where={"document_id": document_id} if document_id else None,
                include=["documents", "metadatas", "distances"],
            )
            return [
                SearchHit(chunk_id=cid, text=text, metadata=meta, distance=distance)
                for cid, text, meta, distance in zip(
                    result["ids"][0],
                    result["documents"][0],
                    result["metadatas"][0],
                    result["distances"][0],
                )
            ]
