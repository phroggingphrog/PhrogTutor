"""Manage pi packages (extensions, skills, prompts) for the pi engine.

Mounted at ``/api/settings/pi-packages``. Admin-gated: a package runs code in
the engine process as the app user.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deeptutor.api.routers.auth import require_admin
from deeptutor.services import pi_packages

router = APIRouter(dependencies=[Depends(require_admin)])


class PackagePayload(BaseModel):
    source: str


@router.get("")
async def list_pi_packages() -> dict[str, Any]:
    return {"packages": pi_packages.list_packages()}


@router.post("")
async def install_pi_package(payload: PackagePayload) -> dict[str, Any]:
    try:
        log = await pi_packages.install_package(payload.source)
    except pi_packages.PiPackageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"packages": pi_packages.list_packages(), "log": log}


@router.post("/remove")
async def remove_pi_package(payload: PackagePayload) -> dict[str, Any]:
    try:
        log = await pi_packages.remove_package(payload.source)
    except pi_packages.PiPackageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"packages": pi_packages.list_packages(), "log": log}
