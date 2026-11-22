from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel

from app.modules.m7_batches.export import export_bundle
from app.modules.m7_batches.service import commit_sample, replay_sample
from app.modules.m8_settlement.export import export_shift
from app.sources import read_sources
from app.modules.m8_settlement.pipeline import run_shift_pipeline
from app.modules.m7_batches.pipeline import run_pipeline

router = APIRouter(prefix="/api/batches", tags=["称重批次"])


class RunInput(BaseModel):
    sample: str = "dirty"


@router.get("")
def list_batches(request: Request):
    return {"batches": request.app.state.store.history()}


@router.get("/events")
def logs(request: Request, after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
         limit: Annotated[int, Query(ge=1, le=250)] = 100):
    rows = request.app.state.store.events(after, limit)
    return {"events": rows, "cursor": rows[-1]["seq"] if rows else after}


@router.post("", status_code=201)
def create_batch(payload: RunInput, request: Request, response: Response):
    manifest, reused = commit_sample(request.app.state.store, request.app.state.settings.source_dir, payload.sample)
    if reused:
        response.status_code = 200
    return {"manifest": manifest, "reused": reused}


@router.get("/export/shift")
def export_live_shift(request: Request, sample: str = "shift"):
    sources = read_sources(request.app.state.settings.source_dir, sample)
    results = run_shift_pipeline(sources) if sample == "shift" else run_pipeline(sources)
    manifest = {"sample": sample, "note": "未归档的本班预览导出"}
    payload = export_shift(manifest, results)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="shift-preview.zip"'},
    )


@router.get("/{run_id}")
def show_batch(run_id: str, request: Request):
    manifest, _ = request.app.state.store.read(run_id)
    return {"manifest": manifest}


@router.post("/{run_id}/rerun")
def rerun_batch(run_id: str, request: Request):
    manifest = replay_sample(request.app.state.store, request.app.state.settings.source_dir, run_id)
    return {"manifest": manifest, "reused": True, "replayed": True}


@router.get("/{run_id}/export")
def download(run_id: str, request: Request):
    manifest, results = request.app.state.store.read(run_id)
    if manifest["sample"] == "shift":
        media, filename, payload = (
            "application/zip", f"shift-{run_id[:12]}.zip", export_shift(manifest, results),
        )
    else:
        media, filename, payload = (
            "application/zip", f"axle-{run_id[:12]}.zip", export_bundle(manifest, results),
        )
    return Response(
        content=payload,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
