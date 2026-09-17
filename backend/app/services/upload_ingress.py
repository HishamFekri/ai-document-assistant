"""Bound bytes before multipart consumption; transport/edge limits are separate."""

from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from app.services.document_resource_errors import DocumentResourceError
from app.services.resource_limits import upload_limits


class UploadBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "").removeprefix(scope.get("root_path", "")).rstrip("/")
        if scope["type"] != "http" or scope.get("method") != "POST" or path != "/documents":
            return await self.app(scope, receive, send)
        limit = upload_limits().request_bytes
        lengths = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
        if lengths:
            try:
                if len(lengths) != 1 or not lengths[0].isdigit():
                    raise ValueError()
                declared = int(lengths[0])
            except ValueError:
                return await DocumentResourceError("upload_form", 400).response()(scope, receive, send)
            if declared > limit:
                return await DocumentResourceError("request_size").response()(scope, receive, send)
        total = 0
        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    # Starlette closes partial multipart files on stream errors.
                    raise DocumentResourceError("request_size")
            return message
        await self.app(scope, bounded_receive, send)


class UploadRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        if self.name != "upload_document":
            return handler
        async def bounded_form(request):
            try:
                async with request.form(max_files=1, max_fields=0,
                                        max_part_size=upload_limits().multipart_overhead_bytes):
                    return await handler(request)
            except DocumentResourceError:
                raise
            except HTTPException as error:
                if error.status_code == 400 and request._form is None:
                    raise DocumentResourceError("upload_form", 400) from None
                raise
        return bounded_form
