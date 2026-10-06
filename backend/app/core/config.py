from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_", env_file=ROOT / ".env", extra="ignore"
    )

    chroma_path: Path = ROOT / "data/chroma"
    collection: str = "legal_documents"
    model_cache: Path = ROOT / "data/models"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    chunk_size: int = Field(default=800, ge=100, le=4000)
    chunk_overlap: int = Field(default=100, ge=0)
    max_file_mb: int = Field(default=20, ge=1, le=100)
    max_files: int = Field(default=10, ge=1, le=100)
    max_pages: int = Field(default=300, ge=1)
    batch_size: int = Field(default=32, ge=1, le=256)

    @model_validator(mode="after")
    def validate_settings(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        for field in ("chroma_path", "model_cache"):
            path = getattr(self, field)
            setattr(self, field, (ROOT / path).resolve())
        return self
