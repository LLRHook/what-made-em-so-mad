from __future__ import annotations

from fastapi import APIRouter

from app.domain.models import EvaluationRunRequest, EvaluationRunResponse
from app.services.evaluation import get_evaluation_run, run_evaluation

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


@router.post("/run", response_model=EvaluationRunResponse, status_code=201)
def create_evaluation_run(body: EvaluationRunRequest | None = None) -> EvaluationRunResponse:
    return run_evaluation((body or EvaluationRunRequest()).fixture_set_path)


@router.get("/runs/{run_id}", response_model=EvaluationRunResponse)
def read_evaluation_run(run_id: str) -> EvaluationRunResponse:
    return get_evaluation_run(run_id)
