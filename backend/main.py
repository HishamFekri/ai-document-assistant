from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.services.resource_admission import ResourceRejected, resource_error_response
from app.services.document_resource_errors import DocumentResourceError, resource_validation_response
from app.services.upload_ingress import UploadBodyLimitMiddleware
from app.services.auth_config import auth_settings

from app.routes.documents import router as documents_router
from app.routes.chats import router as chats_router
from app.routes.auth import router as auth_router
from app.routes.summaries import router as summaries_router
from app.routes.document_assets import router as document_assets_router
from app.routes.summary_assistant import router as summary_assistant_router


app = FastAPI(
    title="AI Document Assistant"
)

app.add_exception_handler(ResourceRejected, resource_error_response)
app.add_exception_handler(DocumentResourceError, resource_validation_response)


allowed_origins = list(auth_settings().allowed_origins)


app.add_middleware(UploadBodyLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "X-Resource-Error", "X-Next-Cursor"],
)


ALLOWED_ORIGINS = set(allowed_origins)


@app.middleware("http")
async def validate_request_origin(
    request: Request,
    call_next,
):
    if request.method in {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }:
        origin = request.headers.get("origin")

        if origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Request origin is not allowed"
                },
            )

    return await call_next(request)


app.include_router(documents_router)
app.include_router(chats_router)
app.include_router(auth_router)
app.include_router(summaries_router)
app.include_router(document_assets_router)
app.include_router(summary_assistant_router)


@app.get("/health")
def health_check():
    return {
        "status": "ok"
    }
