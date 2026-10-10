"""Application entry point."""

import hmac
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.database import initialize_database
from app.routes import router

initialize_database()

app = FastAPI(
    title="Bulk Certificate Generator API",
    description="Generate and retrieve PDF certificates for a batch of recipients.",
    version="1.0.0",
)
app.include_router(router)


@app.middleware("http")
async def optional_api_key_authentication(request, call_next):
    """Protect private API operations when a deployment key is configured."""
    configured_key = os.getenv("CERTIFICATE_API_KEY")
    path = request.url.path
    is_public_verification = path.startswith("/api/certificates/") and path.endswith("/verify")
    if configured_key and path.startswith("/api/") and not is_public_verification:
        supplied_key = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(supplied_key, configured_key):
            return JSONResponse(
                status_code=401,
                content={"detail": "A valid X-API-Key header is required"},
                headers={"WWW-Authenticate": "ApiKey"},
            )
    return await call_next(request)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
