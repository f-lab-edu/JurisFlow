import logging
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from backend.app.core.config import Settings
from backend.app.schemas.rag import IngestResult, SearchHit, SearchRequest
from backend.app.services.rag import RagService, safe_name

router = APIRouter(prefix="/api/rag", tags=["RAG"])
logger = logging.getLogger(__name__)


@lru_cache
def get_service():
    return RagService(Settings())


def ingest_upload(upload: UploadFile, service: RagService):
    name = safe_name(upload.filename or "upload.pdf")
    if upload.content_type not in ("application/pdf", "application/octet-stream", None):
        return IngestResult(
            filename=name, status="failed", error="PDF MIME 타입만 허용됩니다."
        )
    with TemporaryDirectory(prefix="jurisflow-") as directory:
        path = Path(directory) / "upload.pdf"
        size = 0
        with path.open("wb") as target:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > service.settings.max_file_mb * 1024 * 1024:
                    return IngestResult(
                        filename=name,
                        status="failed",
                        error="파일 크기 제한을 초과했습니다.",
                    )
                target.write(chunk)
        return service.ingest(path, name)


@router.post("/documents", response_model=list[IngestResult])
async def upload_documents(
    files: Annotated[list[UploadFile], File()],
    service: Annotated[RagService, Depends(get_service)],
):
    try:
        if len(files) > service.settings.max_files:
            raise HTTPException(413, "한 번에 업로드할 수 있는 파일 수를 초과했습니다.")
        return [await run_in_threadpool(ingest_upload, file, service) for file in files]
    finally:
        for file in files:
            await file.close()


@router.post("/search", response_model=list[SearchHit])
async def search(
    request: SearchRequest, service: Annotated[RagService, Depends(get_service)]
):
    try:
        return await run_in_threadpool(
            service.search, request.query, request.limit, request.document_id
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("RAG search failed")
        raise HTTPException(
            503, "검색 서비스를 사용할 수 없습니다. 서버 로그를 확인하세요."
        ) from exc
