from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Routing(ModelOutput):
    needs_documents: bool
    document_specific: bool
    needs_web: bool
    public_query: str = Field(max_length=160)


class Action(ModelOutput):
    action: Literal["search_documents", "search_web", "answer"]
    query: str = Field(default="", max_length=500)


class EvidenceJudge(ModelOutput):
    sufficient: bool
    relevant_ids: list[str] = Field(max_length=8)


class Draft(ModelOutput):
    answer: str = Field(min_length=1, max_length=6000)
    citations: list[str] = Field(max_length=8)


class AnswerJudge(ModelOutput):
    passed: bool
    issue: Literal["none", "irrelevant", "unsupported", "citation", "contradiction"]
