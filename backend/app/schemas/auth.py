from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ResponseModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ResponseMeta(ResponseModel):
    request_id: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokensResponse(ResponseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    refresh_token: str
    refresh_token_expires_in: int

class RefreshTokenResponse(ResponseModel):
    success: Literal[True] = True
    data: TokensResponse
    meta: ResponseMeta