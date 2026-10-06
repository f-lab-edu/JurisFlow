from fastembed import TextEmbedding
from tokenizers import Tokenizer

from backend.app.core.config import Settings


class Embedder:
    def __init__(self, settings: Settings):
        self.model = TextEmbedding(
            model_name=settings.embedding_model,
            cache_dir=str(settings.model_cache),
            threads=2,
        )
        # Clone the tokenizer with truncation disabled so chunks never silently lose text.
        self.tokenizer = Tokenizer.from_str(self.model.model.tokenizer.to_str())
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.limit = self.model.model.tokenizer.truncation["max_length"]
        self.dimension = len(next(self.model.embed(["dimension probe"])))
        self.batch_size = settings.batch_size
        self.e5 = "e5" in settings.embedding_model.lower()

    def fits(self, text: str) -> bool:
        prefix = "passage: " if self.e5 else ""
        return len(self.tokenizer.encode(prefix + text).ids) <= self.limit

    def documents(self, texts: list[str]) -> list[list[float]]:
        return [
            v.tolist()
            for v in self.model.passage_embed(texts, batch_size=self.batch_size)
        ]

    def query(self, text: str) -> list[float]:
        prefix = "query: " if self.e5 else ""
        if len(self.tokenizer.encode(prefix + text).ids) > self.limit:
            raise ValueError("검색어가 임베딩 모델의 토큰 한도를 초과했습니다.")
        return next(self.model.query_embed(text)).tolist()
