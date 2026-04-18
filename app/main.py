from fastapi import FastAPI

from app.api.routers.files import router as files_router
from app.api.routers.messages import router as messages_router
from app.api.routers.openai_compat import router as openai_compat_router
from app.api.routers.reports import router as reports_router
from app.api.routers.sessions import router as sessions_router
from app.db.base import Base
from app.db import evidence_models as _evidence_models
from app.db.session import engine

app = FastAPI(title="DS-160 Visa Simulator", version="0.1.0")
Base.metadata.create_all(bind=engine)
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(messages_router)
app.include_router(reports_router)
app.include_router(openai_compat_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
