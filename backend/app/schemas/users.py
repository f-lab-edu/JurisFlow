from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator
from pydantic.alias_generators import to_camel


class SignUpRequest(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=100)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class ResponseModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class UserResponse(ResponseModel):
    user_id: UUID
    email: EmailStr
    name: str
    status: Literal["active"]
    created_at: datetime


class TokensResponse(ResponseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    refresh_token: str
    refresh_token_expires_in: int


class SignUpData(ResponseModel):
    user: UserResponse
    tokens: TokensResponse


class ResponseMeta(ResponseModel):
    request_id: str


class SignUpResponse(ResponseModel):
    success: Literal[True] = True
    data: SignUpData
    meta: ResponseMeta


class SignInRequest(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class SignInResponse(ResponseModel):
    success: Literal[True] = True
    data: TokensResponse
    meta: ResponseMeta
