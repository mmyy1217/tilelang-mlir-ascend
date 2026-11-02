from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .db import ConflictError, NotFoundError, ValidationError, WorkbenchStorage
from .service import WorkbenchService
from .workspace import Workspace
from .simulator import run_two_agent_simulation

AGENT_TOKEN = os.environ.get("WORKBENCH_AGENT_TOKEN", "agent-secret-token")
OPERATOR_TOKEN = os.environ.get("WORKBENCH_OPERATOR_TOKEN", "operator-secret-token")


def create_app(
    data_dir: Optional[str | Path] = None, auto_seed: bool = True
) -> FastAPI:
    storage = WorkbenchStorage(data_dir=data_dir)
    service = WorkbenchService(storage=storage)
    workspace = Workspace(storage)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        if auto_seed and not storage.list_explorations():
            await run_two_agent_simulation(service=service, delay_sec=0.0)
        yield

    app = FastAPI(
        title="TileLang-Ascend Operator Knowledge & Experiment Workbench API",
        version="0.5.0",
        lifespan=lifespan,
    )
    app.state.storage = storage
    app.state.service = service
    app.state.sim_task = None

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_Methods=["*"],
        allow_headers=["*"],
    ) if False else app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Exception Handlers
    # ------------------------------------------------------------------
    @app.exception_handler(ConflictError)
    async def handle_conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"error": "conflict", "detail": str(exc)}
        )

    @app.exception_handler(ValidationError)
    async def handle_validation(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"error": "validation_error", "detail": str(exc)}
        )

    @app.exception_handler(NotFoundError)
    async def handle_not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404, content={"error": "not_found", "detail": str(exc)}
        )

    def verify_agent_credential(x_agent_token: Optional[str]) -> None:
        if x_agent_token != AGENT_TOKEN and x_agent_token != OPERATOR_TOKEN:
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid X-Agent-Token write credential",
            )

    def verify_operator_credential(x_operator_token: Optional[str]) -> None:
        if x_operator_token != OPERATOR_TOKEN:
            raise HTTPException(
                status_code=403,
                detail="Stop/resume operations require valid X-Operator-Token",
            )

    # ------------------------------------------------------------------
    # Exploration Endpoints
    # ------------------------------------------------------------------
    @app.post("/api/projects/import")
    async def import_project(
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ):
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(workspace.import_snapshot, payload, x_request_id)

    @app.post(
        "/api/explorations/{exp_id}/nodes/{node_id}/attempts/{attempt_id}/reconcile"
    )
    async def reconcile(
        exp_id: str,
        node_id: str,
        attempt_id: str,
        payload: Dict[str, Any],
        x_operator_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ):
        verify_operator_credential(x_operator_token)
        if (
            payload.get("status") not in ("stopped", "failed")
            or not payload.get("failureOrSkipReason")
            or payload.get("expectedVersion") is None
        ):
            raise HTTPException(
                422,
                "Reconciliation requires stopped/failed, evidence in failureOrSkipReason and expectedVersion",
            )
        body = {
            k: payload[k] for k in ("status", "failureOrSkipReason", "expectedVersion")
        }
        return await asyncio.to_thread(
            service.finish_stage_attempt,
            exp_id,
            node_id,
            attempt_id,
            body,
            x_request_id,
        )

    @app.get("/api/capabilities")
    async def capabilities():
        return {
            "protocolVersion": 1,
            "capabilities": [
                "graph",
                "context",
                "checkpoint",
                "fork",
                "handoff",
                "session-close",
                "request-status",
                "snapshot-import",
            ],
            "unsupported": ["project-configure", "automatic-dispatch"],
        }

    @app.get("/api/explorations/{exp_id}/context")
    async def get_context(exp_id: str):
        return await asyncio.to_thread(workspace.context, exp_id)

    @app.get("/api/explorations/{exp_id}/requests/{request_id}")
    async def request_status(
        exp_id: str,
        request_id: str,
        x_agent_token: Optional[str] = Header(default=None),
    ):
        verify_agent_credential(x_agent_token)

        def read():
            with storage.read_conn() as c:
                row = c.execute(
                    "SELECT response_json FROM idempotency_keys WHERE exploration_id=? AND request_id=?",
                    (exp_id, request_id),
                ).fetchone()
                return (
                    {"state": "committed", "result": json.loads(row[0])}
                    if row
                    else {"state": "unknown"}
                )

        return await asyncio.to_thread(read)

    @app.post("/api/explorations/{exp_id}/workspace/{operation}")
    async def workspace_action(
        exp_id: str,
        operation: str,
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ):
        verify_agent_credential(x_agent_token)
        actions = {
            "checkpoint": workspace.checkpoint,
            "fork": workspace.fork,
            "handoff": workspace.handoff,
            "session-close": workspace.close,
        }
        if operation not in actions:
            raise HTTPException(404, "Unknown workspace operation")
        return await asyncio.to_thread(
            actions[operation], exp_id, payload, x_request_id
        )

    @app.get("/api/health")
    async def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "storage": "sqlite+wal",
            "explorations": len(storage.list_explorations()),
        }

    @app.get("/api/explorations")
    async def list_explorations() -> Dict[str, Any]:
        return {"items": storage.list_explorations()}

    @app.post("/api/explorations")
    async def create_exploration(
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(
            service.create_or_reset_exploration, payload, x_request_id
        )

    @app.get("/api/explorations/{exp_id}/snapshot")
    async def get_snapshot(exp_id: str) -> Dict[str, Any]:
        return await asyncio.to_thread(storage.get_snapshot, exp_id)

    @app.post("/api/explorations/{exp_id}/stop")
    async def stop_exploration(
        exp_id: str,
        payload: Dict[str, Any],
        x_operator_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_operator_credential(x_operator_token)
        return await asyncio.to_thread(
            service.request_stop, exp_id, payload, x_request_id
        )

    @app.post("/api/explorations/{exp_id}/resume")
    async def resume_exploration(
        exp_id: str,
        payload: Dict[str, Any],
        x_operator_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_operator_credential(x_operator_token)
        return await asyncio.to_thread(
            service.resume_exploration, exp_id, payload, x_request_id
        )

    @app.post("/api/explorations/{exp_id}/simulate")
    async def trigger_parallel_simulation(
        exp_id: str,
        payload: Optional[Dict[str, Any]] = None,
        x_operator_token: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_operator_credential(x_operator_token)
        if not (exp_id.startswith("demo-") or exp_id.endswith("-demo")):
            raise HTTPException(
                status_code=403,
                detail="Simulation reset is restricted to isolated demo explorations (prefix 'demo-' or suffix '-demo')",
            )
        body = payload or {}
        delay_sec = float(body.get("delaySec", 0.08))
        mode = body.get("mode", "background")

        if mode == "sync":
            res = await run_two_agent_simulation(
                service=service, delay_sec=delay_sec, exp_id=exp_id
            )
            snap = await asyncio.to_thread(storage.get_snapshot, exp_id)
            return {"status": "completed", "lastEventSeq": snap["lastEventSeq"], **res}

        if app.state.sim_task and not app.state.sim_task.done():
            app.state.sim_task.cancel()

        app.state.sim_task = asyncio.create_task(
            run_two_agent_simulation(
                service=service, delay_sec=delay_sec, exp_id=exp_id
            )
        )
        return {
            "status": "started",
            "explorationId": exp_id,
            "delaySec": delay_sec,
            "agents": ["agent-cube", "agent-vec", "agent-eval", "agent-coord"],
        }

    # ------------------------------------------------------------------
    # Agent, Node, and StageAttempt Endpoints
    # ------------------------------------------------------------------
    @app.post("/api/explorations/{exp_id}/agents/heartbeat")
    async def agent_heartbeat(
        exp_id: str,
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(
            service.upsert_agent_heartbeat, exp_id, payload, x_request_id
        )

    @app.post("/api/explorations/{exp_id}/nodes")
    async def create_node(
        exp_id: str,
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(
            service.create_node, exp_id, payload, x_request_id
        )

    @app.post("/api/explorations/{exp_id}/nodes/{node_id}/attempts")
    async def start_attempt(
        exp_id: str,
        node_id: str,
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(
            service.start_stage_attempt, exp_id, node_id, payload, x_request_id
        )

    @app.patch("/api/explorations/{exp_id}/nodes/{node_id}/attempts/{attempt_id}")
    async def finish_attempt(
        exp_id: str,
        node_id: str,
        attempt_id: str,
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(
            service.finish_stage_attempt,
            exp_id,
            node_id,
            attempt_id,
            payload,
            x_request_id,
        )

    # ------------------------------------------------------------------
    # Resource, Link, Reference & Knowledge Endpoints
    # ------------------------------------------------------------------
    @app.post("/api/explorations/{exp_id}/resources")
    async def upload_resource(
        exp_id: str,
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(
            service.upload_resource_version, exp_id, payload, x_request_id
        )

    @app.get("/api/explorations/{exp_id}/resources/{version_id}/content")
    async def get_resource_content(exp_id: str, version_id: str) -> Dict[str, Any]:
        with storage.read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM resource_versions WHERE exploration_id = ? AND id = ?",
                (exp_id, version_id),
            ).fetchone()
            if not row:
                raise NotFoundError(f"ResourceVersion '{version_id}' not found")
            blob_rel = row["blob_rel_path"]
            is_bin = bool(row["is_binary"])

        raw_bytes = storage.read_blob_bytes(blob_rel) if blob_rel else b""
        return {
            "id": row["id"],
            "resourceId": row["resource_id"],
            "version": row["version"],
            "title": row["title"],
            "filename": row["filename"],
            "category": row["category"],
            "sha256": row["sha256"],
            "ownerNodeId": row["owner_node_id"],
            "contentStatus": row["content_status"],
            "sizeBytes": int(row["size_bytes"]),
            "isBinary": is_bin,
            "content": row["preview_content"]
            if is_bin
            else raw_bytes.decode("utf-8", errors="replace"),
            "experimentData": (
                json.loads(row["experiment_data_json"])
                if row["experiment_data_json"]
                else None
            ),
            "profileData": (
                json.loads(row["profile_data_json"])
                if row["profile_data_json"]
                else None
            ),
        }

    @app.get("/api/explorations/{exp_id}/resources/{version_id}/download")
    async def download_resource_blob(exp_id: str, version_id: str) -> Response:
        with storage.read_conn() as conn:
            row = conn.execute(
                "SELECT filename, blob_rel_path, is_binary FROM resource_versions WHERE exploration_id = ? AND id = ?",
                (exp_id, version_id),
            ).fetchone()
            if not row or not row["blob_rel_path"]:
                raise NotFoundError(f"Resource blob for '{version_id}' not found")
            filename = row["filename"]
            blob_rel = row["blob_rel_path"]
            is_bin = bool(row["is_binary"])

        raw_bytes = storage.read_blob_bytes(blob_rel)
        media_type = (
            "application/octet-stream" if is_bin else "text/plain; charset=utf-8"
        )
        return Response(
            content=raw_bytes,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/explorations/{exp_id}/resource-links")
    async def create_resource_link(
        exp_id: str,
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(
            service.create_resource_link, exp_id, payload, x_request_id
        )

    @app.post("/api/explorations/{exp_id}/relations")
    async def create_graph_relation(
        exp_id: str,
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ):
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(
            service.create_graph_relation, exp_id, payload, x_request_id
        )

    @app.post("/api/explorations/{exp_id}/references")
    async def create_reference(
        exp_id: str,
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(
            service.create_reference, exp_id, payload, x_request_id
        )

    @app.post("/api/explorations/{exp_id}/knowledge")
    async def upsert_knowledge(
        exp_id: str,
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(
            service.upsert_knowledge_item, exp_id, payload, x_request_id
        )

    @app.post("/api/explorations/{exp_id}/candidates/mark")
    async def mark_ready_for_cool(
        exp_id: str,
        payload: Dict[str, Any],
        x_agent_token: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        verify_agent_credential(x_agent_token)
        return await asyncio.to_thread(
            service.mark_ready_for_cool, exp_id, payload, x_request_id
        )

    # ------------------------------------------------------------------
    # Resumable SSE Event Stream (Batched Replay, No Long Read Transactions)
    # ------------------------------------------------------------------
    @app.get("/api/explorations/{exp_id}/events")
    async def stream_events(
        request: Request,
        exp_id: str,
        after_seq: Optional[int] = Query(default=None),
        max_events: Optional[int] = Query(default=None),
        last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        cursor = 0
        if last_event_id and last_event_id.isdigit():
            cursor = int(last_event_id)
        elif after_seq is not None:
            cursor = int(after_seq)

        async def event_generator() -> AsyncGenerator[str, None]:
            nonlocal cursor
            sent_count = 0
            min_seq, max_seq = await asyncio.to_thread(
                storage.get_event_watermarkBounds, exp_id
            )
            # If client asks for an event seq that was truncated or lies in the future (after reset)
            if (
                cursor < 0
                or (min_seq > 1 and 0 < cursor < min_seq - 1)
                or (max_seq > 0 and cursor > max_seq)
            ):
                resync_payload = json.dumps(
                    {
                        "reason": "watermark_out_of_range",
                        "requestedAfterSeq": cursor,
                        "minAvailableSeq": min_seq,
                        "maxAvailableSeq": max_seq,
                    },
                    ensure_ascii=False,
                )
                yield f"event: resync_required\ndata: {resync_payload}\n\n"
                cursor = max_seq

            while True:
                if await request.is_disconnected():
                    break

                # Fetch one batch at a time; read connection is closed immediately inside fetch_events_batch
                batch = await asyncio.to_thread(
                    storage.fetch_events_batch, exp_id, cursor, 50
                )
                if batch:
                    for evt in batch:
                        cursor = evt["seq"]
                        data_str = json.dumps(evt, ensure_ascii=False)
                        yield f"id: {cursor}\nevent: domain_event\ndata: {data_str}\n\n"
                        sent_count += 1
                        if max_events is not None and sent_count >= max_events:
                            return
                    continue

                if max_events is not None and sent_count >= max_events:
                    return

                # Wait for new events without holding any SQLite connection
                await asyncio.to_thread(storage.wait_for_new_events, 2.0)
                yield f": heartbeat seq={cursor}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------
    # Same-Origin Static Hosting for Built Frontend (web/dist)
    # ------------------------------------------------------------------
    dist_dir = Path(__file__).resolve().parent.parent / "web" / "dist"
    if dist_dir.exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str) -> Response:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found")
            index_file = dist_dir / "index.html"
            if index_file.exists():
                return FileResponse(str(index_file))
            return JSONResponse({"status": "frontend_not_built"}, status_code=404)

    return app


app = create_app()
