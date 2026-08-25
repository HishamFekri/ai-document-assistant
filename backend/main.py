import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routes.documents import router as documents_router
from app.routes.chats import router as chats_router
from app.routes.auth import router as auth_router
from app.routes.summaries import router as summaries_router
from app.routes.document_assets import router as document_assets_router
from app.routes.summary_assistant import router as summary_assistant_router


app = FastAPI(
    title="AI Document Assistant"
)


default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


configured_origins = os.getenv(
    "FRONTEND_URLS",
    ",".join(default_origins),
)


allowed_origins = [
    origin.strip()
    for origin in configured_origins.split(",")
    if origin.strip()
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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