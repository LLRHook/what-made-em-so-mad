from __future__ import annotations

from fastapi import APIRouter

from app.domain.models import CapabilitiesResponse, SetupResponse, StorageUsageResponse
from app.services.capabilities import build_setup_response, probe_capabilities
from app.services.storage import storage_usage

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/capabilities", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse:
    return probe_capabilities()


@router.get("/setup", response_model=SetupResponse)
def get_setup() -> SetupResponse:
    return build_setup_response(probe_capabilities())


@router.get("/storage", response_model=StorageUsageResponse)
def get_storage() -> StorageUsageResponse:
    return storage_usage()
