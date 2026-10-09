"""Application entry point."""

from fastapi import FastAPI

from app.database import Base, engine
from app.routes import router

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Bulk Certificate Generator API",
    description="Generate and retrieve PDF certificates for a batch of recipients.",
    version="1.0.0",
)
app.include_router(router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
