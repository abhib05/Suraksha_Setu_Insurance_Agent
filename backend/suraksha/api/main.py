"""HTTP API for the console and channels. R0 routes land here milestone by milestone."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from suraksha import __version__

app = FastAPI(title="Suraksha Setu API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
