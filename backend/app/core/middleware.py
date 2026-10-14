from uuid import UUID, uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestIdMiddleware:

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = MutableHeaders(scope=scope)
        value = headers.get("X-Request-Id")
        try:
            request_id = str(UUID(value)) if value else "req_" + uuid4().hex
        except ValueError:
            request_id = "req_" + uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_response(message: Message) -> None:
            if (
                message["type"] == "http.response.start"
                and scope["method"] == "PATCH"
                and scope.get("route") is not None
                and scope["route"].name == "update_my_profile"
            ):
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
                response_headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_response)
