from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
)
from pydantic.alias_generators import to_camel

from backend.app.schemas.auth import ResponseMeta, TokensResponse


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


class SignUpData(ResponseModel):
    user: UserResponse
    tokens: TokensResponse


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


class MyProfile(ResponseModel):
    user_id: UUID
    email: EmailStr
    name: str
    status: str
    terms_agreed: bool | None
    privacy_agreed: bool | None
    created_at: datetime
    updated_at: datetime | None

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return (
            value.replace(tzinfo=value.tzinfo or UTC)
            .astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )


class MyProfileResponse(ResponseModel):
    success: Literal[True] = True
    data: MyProfile
    meta: ResponseMeta


class UpdateMyProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class UpdatedMyProfile(ResponseModel):
    user_id: UUID
    email: EmailStr
    name: str
    status: Literal["active"]
    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        return (
            value.replace(tzinfo=value.tzinfo or UTC)
            .astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )


class UpdateMyProfileResponse(ResponseModel):
    success: Literal[True] = True
    data: UpdatedMyProfile
    meta: ResponseMeta


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    password: SecretStr = Field(min_length=1, max_length=128)
    confirmation: Literal["DELETE"]
