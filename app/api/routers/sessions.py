from collections.abc import Iterator
import json
from queue import Empty, Queue
from threading import Thread

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import settings as settings_module
from app.core.visa_families import validate_declared_family
from app.db.session import get_db, session_factory_from_session
from app.core.dependencies import get_session_repo
from app.repositories.session_repo import SessionRepository
from app.services.debug_fill_service import DebugFillService
from app.services.debug_material_bundle_service import DebugMaterialBundleService
from app.services.gate_service import GateService
from app.services.runtime_errors import ModelRuntimeError
from app.services.runtime_debug_snapshot_service import RuntimeDebugSnapshotService
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SessionTurnRecord

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    declared_family: str | None = None


class DebugFillCurrentGapRequest(BaseModel):
    scenario: str = "normal"


class DebugMaterialBundleRequest(BaseModel):
    scenario: str = "normal_f1_bundle"
    include_synthetic_user_turns: bool = True
    seed_text: str | None = None
    generation_mode: str = "ai_if_available"


def _runtime_debug_enabled() -> bool:
    return RuntimeDebugSnapshotService.debug_enabled()


@router.post("", status_code=201)
def create_session(
    payload: CreateSessionRequest,
    repo: SessionRepository = Depends(get_session_repo),
) -> dict:
    try:
        declared_family = validate_declared_family(payload.declared_family)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    gate_status = GateService().initial_gate_status(declared_family)
    record = repo.create(
        declared_family=declared_family,
        gate_status_json=gate_status,
    )
    return {
        "session_id": record.session_id,
        "phase_state": record.phase_state,
        "current_governor_decision": record.current_governor_decision,
        "gate_status": record.gate_status_json,
    }


@router.get("/{session_id}/required-package")
def get_required_package(
    session_id: str,
    repo: SessionRepository = Depends(get_session_repo),
) -> dict:
    record = repo.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="session not found")
    if record.declared_family is None:
        raise HTTPException(status_code=409, detail="declared_family not locked")
    try:
        declared_family = validate_declared_family(record.declared_family)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return GateService().required_package_detail(
        declared_family,
        scenario_key=record.gate_status_json.get("scenario_key"),
    )


@router.post("/{session_id}/debug/fill-current-gap")
def debug_fill_current_gap(
    session_id: str,
    payload: DebugFillCurrentGapRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    if not settings_module.settings.allow_debug_fill:
        raise HTTPException(status_code=403, detail="debug fill is disabled")
    try:
        scenario = payload.scenario if payload is not None else "normal"
        return DebugFillService(db).fill_current_gap(session_id, scenario=scenario)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelRuntimeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.to_public_payload(),
        ) from exc


@router.post("/{session_id}/debug/material-bundles")
def debug_create_material_bundle(
    session_id: str,
    payload: DebugMaterialBundleRequest,
    db: Session = Depends(get_db),
) -> dict:
    if not settings_module.settings.allow_debug_fill:
        raise HTTPException(status_code=403, detail="debug fill is disabled")
    try:
        return DebugMaterialBundleService(db).create_bundle(
            session_id,
            scenario=payload.scenario,
            include_synthetic_user_turns=payload.include_synthetic_user_turns,
            seed_text=payload.seed_text,
            generation_mode=payload.generation_mode,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelRuntimeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.to_public_payload(),
        ) from exc


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/{session_id}/debug/material-bundles/stream")
def debug_create_material_bundle_stream(
    session_id: str,
    payload: DebugMaterialBundleRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    if not settings_module.settings.allow_debug_fill:
        raise HTTPException(status_code=403, detail="debug fill is disabled")
    stream_session_factory = session_factory_from_session(db)

    def event_stream() -> Iterator[str]:
        yield _sse_event("accepted", {"session_id": session_id})
        event_queue: Queue[tuple[str, dict]] = Queue()

        def run_bundle_generation() -> None:
            worker_db = stream_session_factory()
            try:
                for event in DebugMaterialBundleService(
                    worker_db,
                ).create_bundle_events(
                    session_id,
                    scenario=payload.scenario,
                    include_synthetic_user_turns=payload.include_synthetic_user_turns,
                    seed_text=payload.seed_text,
                    generation_mode=payload.generation_mode,
                    include_accepted=False,
                ):
                    event_queue.put((event.event, event.data))
            except LookupError as exc:
                event_queue.put(("error", {"status": 404, "detail": str(exc)}))
            except ValueError as exc:
                event_queue.put(("error", {"status": 422, "detail": str(exc)}))
            except ModelRuntimeError as exc:
                event_queue.put(("error", exc.to_public_payload()))
            except Exception as exc:
                event_queue.put(
                    (
                        "error",
                        {
                            "status": 500,
                            "detail": f"debug material bundle failed: {exc}",
                        },
                    )
                )
            finally:
                worker_db.close()

        Thread(target=run_bundle_generation, daemon=True).start()

        while True:
            try:
                event, data = event_queue.get(timeout=15)
            except Empty:
                yield _sse_event(
                    "progress",
                    {
                        "stage": "debug_material_bundle",
                        "message": "材料包仍在生成或核对中。",
                    },
                )
                continue

            yield _sse_event(event, data)
            if event in {"final", "error"}:
                return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{session_id}/debug/runtime")
def get_runtime_debug_snapshot(
    session_id: str,
    db: Session = Depends(get_db),
) -> dict:
    if not _runtime_debug_enabled():
        raise HTTPException(status_code=403, detail="runtime debug is disabled")
    try:
        return RuntimeDebugSnapshotService(db).build(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}/runtime-traces/{run_id}")
def get_runtime_trace(
    session_id: str,
    run_id: str,
    db: Session = Depends(get_db),
) -> dict:
    statement = (
        select(SessionTurnRecord)
        .where(
            SessionTurnRecord.session_id == session_id,
            SessionTurnRecord.role == "assistant",
        )
        .order_by(SessionTurnRecord.turn_index.desc())
    )
    for turn in db.scalars(statement):
        metadata = dict(turn.metadata_json or {})
        if metadata.get("graph_run_id") != run_id and metadata.get("native_run_id") != run_id:
            continue
        graph_trace = dict(metadata.get("graph_trace", {}) or {})
        graph_events = list(metadata.get("graph_events", []) or [])
        return {
            "session_id": session_id,
            "run_id": run_id,
            "turn_id": turn.turn_id,
            "turn_index": turn.turn_index,
            "agent_runtime": metadata.get("agent_runtime"),
            "selected_public_runtime": metadata.get("selected_public_runtime"),
            "runtime_execution": metadata.get("runtime_execution"),
            "native_run_id": metadata.get("native_run_id"),
            "graph_run_id": metadata.get("graph_run_id"),
            "graph_trace": graph_trace,
            "graph_events": graph_events,
            "graph_runtime_error": metadata.get("graph_runtime_error"),
        }
    raise HTTPException(status_code=404, detail="runtime trace not found")
