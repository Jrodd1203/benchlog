"""Local FastAPI service used by the web UI. Endpoints are agreed at kickoff."""

from fastapi import FastAPI

app = FastAPI(title="benchlog")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
