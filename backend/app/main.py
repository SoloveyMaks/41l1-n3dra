from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.api.router import api_router, scheduler, db_repo

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background scheduler
    scheduler.start()
    yield
    # Stop background scheduler and release DB connections
    scheduler.stop()
    db_repo.close()

app = FastAPI(
    title="Cyber Garden 2026 — AI Security Workflow API",
    version="1.0.0",
    description="Визуальный конструктор проверок безопасности кода: секреты, SCA, SAST, AI Trust Layer и отчеты",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router)

# Mount frontend build if available
frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)
