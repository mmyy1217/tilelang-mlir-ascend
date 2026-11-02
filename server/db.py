from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

STAGE_TYPES: List[str] = [
    "read_info",
    "plan_idea",
    "gen_kernel",
    "compile",
    "verify_correctness",
    "bench_perf",
    "profiling",
    "summarize",
]

EVAL_BUDGET_STAGES = {"verify_correctness", "bench_perf", "profiling"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConflictError(Exception):
    """Raised when optimistic concurrency or idempotency payload hash conflicts."""


class ValidationError(Exception):
    """Raised when domain invariants (tree acyclic, stage dependencies, sha256) fail."""


class NotFoundError(Exception):
    """Raised when a requested entity does not exist."""


class WorkbenchStorage:
    """SQLite WAL + Content-Addressable File Artifact Storage."""

    def __init__(self, data_dir: Optional[str | Path] = None) -> None:
        base_dir = Path(
            data_dir
            or os.environ.get(
                "WORKBENCH_DATA_DIR",
                Path(__file__).resolve().parent.parent / "data",
            )
        )
        self.data_dir = base_dir.resolve()
        self.db_path = self.data_dir / "workbench.db"
        self.blobs_dir = self.data_dir / "blobs"
        self.tmp_dir = self.data_dir / "tmp"
        self._write_lock = threading.RLock()
        self._event_cond = threading.Condition()

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_tmp_files()
        self._init_schema()

    def _cleanup_stale_tmp_files(self) -> None:
        for p in self.tmp_dir.glob("*.tmp"):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    @contextmanager
    def read_conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Short-lived read connection; always closed promptly to avoid blocking WAL checkpoints."""
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write_tx(self) -> Generator[sqlite3.Connection, None, None]:
        """Serialized short write transaction."""
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        with self._event_cond:
            self._event_cond.notify_all()

    def wait_for_new_events(self, timeout: float = 2.0) -> None:
        with self._event_cond:
            self._event_cond.wait(timeout=timeout)

    def _init_schema(self) -> None:
        with self.write_tx() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS explorations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    operator_name TEXT NOT NULL,
                    target_goal TEXT NOT NULL,
                    input_domain TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    baseline_ref_json TEXT NOT NULL,
                    budget_json TEXT NOT NULL,
                    coordinator_status TEXT NOT NULL DEFAULT 'running',
                    stop_reason TEXT,
                    best_valid_node_id TEXT NOT NULL,
                    unfinished_directions_json TEXT NOT NULL DEFAULT '[]',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT NOT NULL,
                    exploration_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'idle',
                    last_heartbeat TEXT NOT NULL,
                    current_node_id TEXT,
                    current_stage TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (exploration_id, id),
                    FOREIGN KEY (exploration_id) REFERENCES explorations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT NOT NULL,
                    exploration_id TEXT NOT NULL,
                    parent_id TEXT,
                    depth INTEGER NOT NULL DEFAULT 0,
                    branch_label TEXT NOT NULL,
                    title TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    change_summary TEXT NOT NULL,
                    derivation_note TEXT NOT NULL DEFAULT '',
                    primary_kernel_version_id TEXT,
                    agent_ids_json TEXT NOT NULL DEFAULT '[]',
                    execution_status TEXT NOT NULL DEFAULT 'pending',
                    content_status TEXT NOT NULL DEFAULT 'hot',
                    current_stage TEXT NOT NULL DEFAULT 'read_info',
                    latency_us REAL,
                    speedup_vs_baseline REAL,
                    correctness_passed INTEGER NOT NULL DEFAULT 0,
                    stop_or_fail_reason TEXT,
                    pipeline_metrics_json TEXT,
                    missing_stage_reasons_json TEXT NOT NULL DEFAULT '{}',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (exploration_id, id),
                    FOREIGN KEY (exploration_id) REFERENCES explorations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS stage_attempts (
                    id TEXT NOT NULL,
                    exploration_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    stage_type TEXT NOT NULL,
                    code TEXT NOT NULL,
                    round INTEGER NOT NULL DEFAULT 1,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    depends_on_attempt_ids_json TEXT NOT NULL DEFAULT '[]',
                    kernel_version_label TEXT,
                    input_resource_version_ids_json TEXT NOT NULL DEFAULT '[]',
                    output_resource_version_ids_json TEXT NOT NULL DEFAULT '[]',
                    summary TEXT NOT NULL DEFAULT '',
                    failure_or_skip_reason TEXT,
                    reused_from_attempt_id TEXT,
                    reserved_eval_slot INTEGER NOT NULL DEFAULT 0,
                    seq_order INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (exploration_id, id),
                    FOREIGN KEY (exploration_id, node_id) REFERENCES nodes(exploration_id, id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT NOT NULL,
                    exploration_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    category TEXT NOT NULL,
                    owner_node_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (exploration_id, id),
                    FOREIGN KEY (exploration_id) REFERENCES explorations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS resource_versions (
                    id TEXT NOT NULL,
                    exploration_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    title TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    category TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    owner_node_id TEXT NOT NULL,
                    content_status TEXT NOT NULL DEFAULT 'hot',
                    created_at TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    is_binary INTEGER NOT NULL DEFAULT 0,
                    missing_reason TEXT,
                    blob_rel_path TEXT,
                    preview_content TEXT NOT NULL DEFAULT '',
                    experiment_data_json TEXT,
                    profile_data_json TEXT,
                    PRIMARY KEY (exploration_id, id),
                    FOREIGN KEY (exploration_id, resource_id) REFERENCES resources(exploration_id, id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS resource_links (
                    id TEXT NOT NULL,
                    exploration_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    stage_type TEXT,
                    attempt_id TEXT,
                    resource_version_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    source_node_id TEXT NOT NULL,
                    is_shared_reference INTEGER NOT NULL DEFAULT 0,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (exploration_id, id),
                    FOREIGN KEY (exploration_id, resource_version_id) REFERENCES resource_versions(exploration_id, id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS references_table (
                    id TEXT NOT NULL,
                    exploration_id TEXT NOT NULL,
                    source_node_id TEXT NOT NULL,
                    target_node_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    referenced_node_id TEXT,
                    referenced_knowledge_id TEXT,
                    referenced_knowledge_version TEXT,
                    referenced_resource_version_id TEXT,
                    title TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    reverified_in_attempt_id TEXT,
                    reverified_status TEXT NOT NULL DEFAULT 'pending_reverify',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (exploration_id, id),
                    FOREIGN KEY (exploration_id) REFERENCES explorations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id TEXT NOT NULL,
                    exploration_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    version TEXT NOT NULL,
                    content_status TEXT NOT NULL DEFAULT 'hot',
                    applicability TEXT NOT NULL,
                    unverified_scope TEXT NOT NULL,
                    body TEXT NOT NULL,
                    supporting_node_ids_json TEXT NOT NULL DEFAULT '[]',
                    counterexample_node_ids_json TEXT NOT NULL DEFAULT '[]',
                    evidence_attempt_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (exploration_id, id),
                    FOREIGN KEY (exploration_id) REFERENCES explorations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    exploration_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    entity_version INTEGER NOT NULL DEFAULT 1,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_exp_seq ON events(exploration_id, seq);

                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    exploration_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (exploration_id, request_id)
                );
                """
            )

    # ------------------------------------------------------------------
    # Content-Addressable File Blob Helpers (temp write -> sha256 -> atomic move)
    # ------------------------------------------------------------------
    def write_verified_blob(
        self, raw_bytes: bytes, expected_sha256: Optional[str] = None
    ) -> Tuple[str, str, int]:
        """Write bytes to temp file, verify SHA256, and move atomically into blobs dir."""
        tmp_path = self.tmp_dir / f"upload-{uuid.uuid4().hex}.tmp"
        hasher = hashlib.sha256()
        try:
            with open(tmp_path, "wb") as f:
                f.write(raw_bytes)
                f.flush()
            hasher.update(raw_bytes)
            actual_sha256 = hasher.hexdigest()
            if expected_sha256 and expected_sha256.lower() != actual_sha256.lower():
                raise ValidationError(
                    f"SHA256 mismatch: expected {expected_sha256}, computed {actual_sha256}"
                )
            sub_dir = self.blobs_dir / actual_sha256[:2]
            sub_dir.mkdir(parents=True, exist_ok=True)
            dest_path = sub_dir / actual_sha256
            os.replace(tmp_path, dest_path)
            rel_path = f"{actual_sha256[:2]}/{actual_sha256}"
            return actual_sha256, rel_path, len(raw_bytes)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def read_blob_bytes(self, blob_rel_path: str) -> bytes:
        # Prevent path traversal outside self.blobs_dir
        candidate = (self.blobs_dir / blob_rel_path).resolve()
        if not str(candidate).startswith(str(self.blobs_dir.resolve())):
            raise ValidationError("Invalid blob path")
        if not candidate.exists():
            raise NotFoundError(f"Blob file not found: {blob_rel_path}")
        return candidate.read_bytes()

    # ------------------------------------------------------------------
    # Idempotency & Event Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def compute_payload_hash(payload: Any) -> str:
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def check_idempotency(
        self,
        conn: sqlite3.Connection,
        exploration_id: str,
        request_id: Optional[str],
        endpoint: str,
        payload_hash: str,
    ) -> Optional[Dict[str, Any]]:
        if not request_id:
            return None
        row = conn.execute(
            """
            SELECT endpoint, payload_hash, response_json
            FROM idempotency_keys
            WHERE exploration_id = ? AND request_id = ?
            """,
            (exploration_id, request_id),
        ).fetchone()
        if not row:
            return None
        if row["endpoint"] != endpoint or row["payload_hash"] != payload_hash:
            raise ConflictError(
                f"Idempotency key '{request_id}' already used with a different request payload."
            )
        return json.loads(row["response_json"])

    def save_idempotency(
        self,
        conn: sqlite3.Connection,
        exploration_id: str,
        request_id: Optional[str],
        endpoint: str,
        payload_hash: str,
        response_data: Dict[str, Any],
    ) -> None:
        if not request_id:
            return
        conn.execute(
            """
            INSERT INTO idempotency_keys
            (exploration_id, request_id, endpoint, payload_hash, status_code, response_json, created_at)
            VALUES (?, ?, ?, ?, 200, ?, ?)
            """,
            (
                exploration_id,
                request_id,
                endpoint,
                payload_hash,
                json.dumps(response_data, ensure_ascii=False),
                utc_now_iso(),
            ),
        )

    def append_event(
        self,
        conn: sqlite3.Connection,
        exploration_id: str,
        entity_type: str,
        entity_id: str,
        entity_version: int,
        event_type: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        cur = conn.execute(
            """
            INSERT INTO events
            (event_id, exploration_id, entity_type, entity_id, entity_version, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                exploration_id,
                entity_type,
                entity_id,
                entity_version,
                event_type,
                json.dumps(payload, ensure_ascii=False),
                now,
            ),
        )
        seq = cur.lastrowid
        return {
            "seq": seq,
            "eventId": event_id,
            "explorationId": exploration_id,
            "entityType": entity_type,
            "entityId": entity_id,
            "entityVersion": entity_version,
            "eventType": event_type,
            "payload": payload,
            "createdAt": now,
        }

    # ------------------------------------------------------------------
    # Batched Event Replay (No Long-Lived Read Transactions)
    # ------------------------------------------------------------------
    def get_event_watermarkBounds(self, exploration_id: str) -> Tuple[int, int]:
        """Return (min_seq, max_seq) for an exploration in a short read transaction."""
        with self.read_conn() as conn:
            row = conn.execute(
                "SELECT MIN(seq) AS min_seq, MAX(seq) AS max_seq FROM events WHERE exploration_id = ?",
                (exploration_id,),
            ).fetchone()
            if not row or row["max_seq"] is None:
                return (0, 0)
            return (int(row["min_seq"] or 1), int(row["max_seq"] or 0))

    def fetch_events_batch(
        self, exploration_id: str, after_seq: int, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Fetch a single batch of events and immediately release the SQLite read connection."""
        with self.read_conn() as conn:
            rows = conn.execute(
                """
                SELECT seq, event_id, exploration_id, entity_type, entity_id,
                       entity_version, event_type, payload_json, created_at
                FROM events
                WHERE exploration_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (exploration_id, after_seq, limit),
            ).fetchall()
            return [
                {
                    "seq": int(r["seq"]),
                    "eventId": r["event_id"],
                    "explorationId": r["exploration_id"],
                    "entityType": r["entity_type"],
                    "entityId": r["entity_id"],
                    "entityVersion": int(r["entity_version"]),
                    "eventType": r["event_type"],
                    "payload": json.loads(r["payload_json"]),
                    "createdAt": r["created_at"],
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # Normalized Snapshot Assembly
    # ------------------------------------------------------------------
    def list_explorations(self) -> List[Dict[str, Any]]:
        with self.read_conn() as conn:
            rows = conn.execute(
                "SELECT id, title, operator_name, coordinator_status, best_valid_node_id, version, updated_at FROM explorations ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_snapshot(self, exploration_id: str) -> Dict[str, Any]:
        with self.read_conn() as conn:
            exp_row = conn.execute(
                "SELECT * FROM explorations WHERE id = ?", (exploration_id,)
            ).fetchone()
            if not exp_row:
                raise NotFoundError(f"Exploration '{exploration_id}' not found")

            seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM events WHERE exploration_id = ?",
                (exploration_id,),
            ).fetchone()
            last_event_seq = int(seq_row["max_seq"]) if seq_row else 0

            agent_rows = conn.execute(
                "SELECT * FROM agents WHERE exploration_id = ? ORDER BY id ASC",
                (exploration_id,),
            ).fetchall()

            node_rows = conn.execute(
                "SELECT * FROM nodes WHERE exploration_id = ? ORDER BY depth ASC, id ASC",
                (exploration_id,),
            ).fetchall()

            attempt_rows = conn.execute(
                """
                SELECT * FROM stage_attempts
                WHERE exploration_id = ?
                ORDER BY seq_order ASC, started_at ASC, id ASC
                """,
                (exploration_id,),
            ).fetchall()

            version_rows = conn.execute(
                "SELECT * FROM resource_versions WHERE exploration_id = ? ORDER BY created_at ASC, id ASC",
                (exploration_id,),
            ).fetchall()

            link_rows = conn.execute(
                "SELECT * FROM resource_links WHERE exploration_id = ? ORDER BY created_at ASC, id ASC",
                (exploration_id,),
            ).fetchall()

            ref_rows = conn.execute(
                "SELECT * FROM references_table WHERE exploration_id = ? ORDER BY created_at ASC, id ASC",
                (exploration_id,),
            ).fetchall()

            know_rows = conn.execute(
                "SELECT * FROM knowledge_items WHERE exploration_id = ? ORDER BY id ASC",
                (exploration_id,),
            ).fetchall()

        # Group attempts by (node_id, stage_type)
        attempts_by_node_stage: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for ar in attempt_rows:
            att = {
                "id": ar["id"],
                "code": ar["code"],
                "nodeId": ar["node_id"],
                "stageType": ar["stage_type"],
                "round": int(ar["round"]),
                "agentId": ar["agent_id"],
                "status": ar["status"],
                "startedAt": ar["started_at"],
                "endedAt": ar["ended_at"],
                "dependsOnAttemptIds": json.loads(ar["depends_on_attempt_ids_json"]),
                "kernelVersionLabel": ar["kernel_version_label"],
                "inputResourceVersionIds": json.loads(ar["input_resource_version_ids_json"]),
                "outputResourceVersionIds": json.loads(ar["output_resource_version_ids_json"]),
                "summary": ar["summary"],
                "failureOrSkipReason": ar["failure_or_skip_reason"],
                "reusedFromAttemptId": ar["reused_from_attempt_id"],
            }
            attempts_by_node_stage.setdefault((ar["node_id"], ar["stage_type"]), []).append(att)

        nodes: List[Dict[str, Any]] = []
        for nr in node_rows:
            missing_reasons: Dict[str, str] = json.loads(nr["missing_stage_reasons_json"] or "{}")
            stages_dict: Dict[str, Any] = {}
            for st in STAGE_TYPES:
                st_attempts = attempts_by_node_stage.get((nr["id"], st), [])
                if st_attempts:
                    last_att = st_attempts[-1]
                    st_status = last_att["status"]
                    curr_att_id = last_att["id"]
                else:
                    st_status = "stopped" if nr["execution_status"] == "stopped" else "pending"
                    curr_att_id = None
                stage_rec: Dict[str, Any] = {
                    "stageType": st,
                    "status": st_status,
                    "attempts": st_attempts,
                }
                if curr_att_id:
                    stage_rec["currentAttemptId"] = curr_att_id
                if st in missing_reasons:
                    stage_rec["missingOutputReason"] = missing_reasons[st]
                stages_dict[st] = stage_rec

            node_obj: Dict[str, Any] = {
                "id": nr["id"],
                "explorationId": nr["exploration_id"],
                "parentId": nr["parent_id"],
                "depth": int(nr["depth"]),
                "branchLabel": nr["branch_label"],
                "title": nr["title"],
                "hypothesis": nr["hypothesis"],
                "changeSummary": nr["change_summary"],
                "derivationNote": nr["derivation_note"],
                "primaryKernelVersionId": nr["primary_kernel_version_id"],
                "agentIds": json.loads(nr["agent_ids_json"]),
                "executionStatus": nr["execution_status"],
                "contentStatus": nr["content_status"],
                "currentStage": nr["current_stage"],
                "latencyUs": nr["latency_us"],
                "speedupVsBaseline": nr["speedup_vs_baseline"],
                "correctnessPassed": bool(nr["correctness_passed"]),
                "stopOrFailReason": nr["stop_or_fail_reason"],
                "pipelineMetrics": (
                    json.loads(nr["pipeline_metrics_json"])
                    if nr["pipeline_metrics_json"]
                    else None
                ),
                "stages": stages_dict,
                "version": int(nr["version"]),
            }
            nodes.append(node_obj)

        resources_map: Dict[str, Any] = {}
        for vr in version_rows:
            res_obj: Dict[str, Any] = {
                "id": vr["id"],
                "resourceId": vr["resource_id"],
                "version": vr["version"],
                "title": vr["title"],
                "filename": vr["filename"],
                "category": vr["category"],
                "sha256": vr["sha256"],
                "ownerNodeId": vr["owner_node_id"],
                "contentStatus": vr["content_status"],
                "createdAt": vr["created_at"],
                "sizeBytes": int(vr["size_bytes"]),
                "isBinary": bool(vr["is_binary"]),
                "missingReason": vr["missing_reason"],
                "content": vr["preview_content"],
                "experimentData": (
                    json.loads(vr["experiment_data_json"])
                    if vr["experiment_data_json"]
                    else None
                ),
                "profileData": (
                    json.loads(vr["profile_data_json"])
                    if vr["profile_data_json"]
                    else None
                ),
            }
            resources_map[vr["id"]] = res_obj

        resource_links: List[Dict[str, Any]] = [
            {
                "id": lr["id"],
                "nodeId": lr["node_id"],
                "stageType": lr["stage_type"],
                "attemptId": lr["attempt_id"],
                "resourceVersionId": lr["resource_version_id"],
                "role": lr["role"],
                "sourceNodeId": lr["source_node_id"],
                "isSharedReference": bool(lr["is_shared_reference"]),
                "note": lr["note"],
            }
            for lr in link_rows
        ]

        references: List[Dict[str, Any]] = [
            {
                "id": rr["id"],
                "explorationId": rr["exploration_id"],
                "sourceNodeId": rr["source_node_id"],
                "targetNodeId": rr["target_node_id"],
                "purpose": rr["purpose"],
                "referencedNodeId": rr["referenced_node_id"],
                "referencedKnowledgeId": rr["referenced_knowledge_id"],
                "referencedKnowledgeVersion": rr["referenced_knowledge_version"],
                "referencedResourceVersionId": rr["referenced_resource_version_id"],
                "title": rr["title"],
                "rationale": rr["rationale"],
                "reverifiedInAttemptId": rr["reverified_in_attempt_id"],
                "reverifiedStatus": rr["reverified_status"],
            }
            for rr in ref_rows
        ]

        agents: List[Dict[str, Any]] = [
            {
                "id": ar["id"],
                "name": ar["name"],
                "role": ar["role"],
                "status": ar["status"],
                "lastHeartbeat": ar["last_heartbeat"],
                "currentNodeId": ar["current_node_id"],
                "currentStage": ar["current_stage"],
            }
            for ar in agent_rows
        ]

        knowledge_items: List[Dict[str, Any]] = [
            {
                "id": kr["id"],
                "explorationId": kr["exploration_id"],
                "category": kr["category"],
                "claimType": kr["claim_type"],
                "title": kr["title"],
                "version": kr["version"],
                "contentStatus": kr["content_status"],
                "applicability": kr["applicability"],
                "unverifiedScope": kr["unverified_scope"],
                "body": kr["body"],
                "supportingNodeIds": json.loads(kr["supporting_node_ids_json"]),
                "counterexampleNodeIds": json.loads(kr["counterexample_node_ids_json"]),
                "evidenceAttemptIds": json.loads(kr["evidence_attempt_ids_json"]),
            }
            for kr in know_rows
        ]

        return {
            "id": exp_row["id"],
            "title": exp_row["title"],
            "operatorName": exp_row["operator_name"],
            "targetGoal": exp_row["target_goal"],
            "inputDomain": exp_row["input_domain"],
            "environment": exp_row["environment"],
            "baselineRef": json.loads(exp_row["baseline_ref_json"]),
            "budget": json.loads(exp_row["budget_json"]),
            "coordinatorStatus": exp_row["coordinator_status"],
            "stopReason": exp_row["stop_reason"],
            "bestValidNodeId": exp_row["best_valid_node_id"],
            "unfinishedDirections": json.loads(exp_row["unfinished_directions_json"]),
            "version": int(exp_row["version"]),
            "lastEventSeq": last_event_seq,
            "nodes": nodes,
            "agents": agents,
            "references": references,
            "resources": resources_map,
            "resourceLinks": resource_links,
            "knowledgeItems": knowledge_items,
        }
