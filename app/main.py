from fastapi import FastAPI

from app.api.routers.files import router as files_router

app = FastAPI(title="DS-160 Visa Simulator", version="0.1.0")
app.include_router(files_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
