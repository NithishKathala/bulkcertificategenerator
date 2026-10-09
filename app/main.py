"""Application entry point."""

from fastapi import FastAPI

from app.database import initialize_database
from app.routes import router

initialize_database()

app = FastAPI(
    title="Bulk Certificate Generator API",
    description="Generate and retrieve PDF certificates for a batch of recipients.",
    version="1.0.0",
)
app.include_router(router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
