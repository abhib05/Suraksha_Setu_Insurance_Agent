"""HTTP API for the console and channels. R0 routes land here milestone by milestone."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from suraksha import __version__
from suraksha.api.cases import audit_router
from suraksha.api.cases import router as cases_router
from suraksha.api.intake import router as intake_router
from suraksha.api.review import router as review_router
from suraksha.api.workflows import router as workflows_router

app = FastAPI(title="Suraksha Setu API", version=__version__)
app.include_router(cases_router)
app.include_router(audit_router)
app.include_router(workflows_router)
app.include_router(intake_router)
app.include_router(review_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
