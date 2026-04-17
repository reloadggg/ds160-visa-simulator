from fastapi import FastAPI

app = FastAPI(title="DS-160 Visa Simulator", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
