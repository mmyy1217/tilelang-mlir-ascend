from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from .db import (
    EVAL_BUDGET_STAGES,
    STAGE_TYPES,
    ConflictError,
    NotFoundError,
    ValidationError,
    WorkbenchStorage,
    utc_now_iso,
)


class WorkbenchService:
    """Domain service implementing Exploration Graph -> Node -> StageAttempt rules & SSE events."""

    def __init__(self, storage: WorkbenchStorage) -> None:
        self.storage = storage

    @staticmethod
    def _require_kernel(conn: Any, exploration_id: str, version_id: str) -> None:
        row = conn.execute(
            "SELECT category FROM resource_versions WHERE exploration_id = ? AND id = ?",
            (exploration_id, version_id),
        ).fetchone()
        if not row or row["category"] != "kernel":
            raise ValidationError(
                f"Kernel resource version '{version_id}' is missing or not kernel code"
            )

    @staticmethod
    def _reject_internal_flags(payload: Dict[str, Any], _internal_seed: bool) -> None:
        if _internal_seed:
            return
        forbidden = (
            "resetIfExists",
            "allowDuringSeed",
            "skipBudgetReservation",
            "allowPinnedCooledBaseline",
        )
        for key in forbidden:
            if payload.get(key):
                raise ValidationError(f"禁止通过外部 API 请求传入内部控制开关 '{key}'")

    # ------------------------------------------------------------------
    # Exploration Creation & Snapshot
    # ------------------------------------------------------------------
    def create_or_reset_exploration(
        self,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
        *,
        _internal_seed: bool = False,
    ) -> Dict[str, Any]:
        self._reject_internal_flags(payload, _internal_seed)
        exp_id = payload["id"]
        payload_hash = self.storage.compute_payload_hash(payload)

        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exp_id, request_id, "create_exploration", payload_hash
            )
            if cached is not None:
                return cached

            if _internal_seed and payload.get("resetIfExists", False):
                conn.execute("DELETE FROM explorations WHERE id = ?", (exp_id,))
                conn.execute("DELETE FROM events WHERE exploration_id = ?", (exp_id,))
                conn.execute(
                    "DELETE FROM idempotency_keys WHERE exploration_id = ?", (exp_id,)
                )
            else:
                existing = conn.execute(
                    "SELECT id FROM explorations WHERE id = ?", (exp_id,)
                ).fetchone()
                if existing:
                    raise ConflictError(f"Exploration '{exp_id}' already exists")

            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO explorations (
                    id, title, operator_name, target_goal, input_domain, environment,
                    baseline_ref_json, budget_json, coordinator_status, stop_reason,
                    best_valid_node_id, unfinished_directions_json, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    exp_id,
                    payload["title"],
                    payload["operatorName"],
                    payload["targetGoal"],
                    payload["inputDomain"],
                    payload["environment"],
                    json.dumps(payload["baselineRef"], ensure_ascii=False),
                    json.dumps(payload["budget"], ensure_ascii=False),
                    payload.get("coordinatorStatus", "running"),
                    payload.get("stopReason"),
                    payload.get("bestValidNodeId", "N0"),
                    json.dumps(
                        payload.get("unfinishedDirections", []), ensure_ascii=False
                    ),
                    now,
                    now,
                ),
            )

            evt = self.storage.append_event(
                conn,
                exploration_id=exp_id,
                entity_type="exploration",
                entity_id=exp_id,
                entity_version=1,
                event_type="exploration.created",
                payload={"id": exp_id, "title": payload["title"]},
            )
            result = {"explorationId": exp_id, "version": 1, "event": evt}
            self.storage.save_idempotency(
                conn, exp_id, request_id, "create_exploration", payload_hash, result
            )
            return result

    # ------------------------------------------------------------------
    # Agent Registration & Heartbeat
    # ------------------------------------------------------------------
    def upsert_agent_heartbeat(
        self,
        exploration_id: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload_hash = self.storage.compute_payload_hash(payload)
        agent_id = payload["id"]
        now = payload.get("lastHeartbeat") or utc_now_iso()

        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "agent_heartbeat", payload_hash
            )
            if cached is not None:
                return cached

            exp = conn.execute(
                "SELECT coordinator_status, stop_reason FROM explorations WHERE id = ?",
                (exploration_id,),
            ).fetchone()
            if not exp:
                raise NotFoundError(f"Exploration '{exploration_id}' not found")

            if conn.execute(
                "SELECT 1 FROM closed_sessions WHERE exploration_id=? AND session_id=?",
                (exploration_id, agent_id),
            ).fetchone():
                raise ConflictError("Session was closed; register a new session")
            existing = conn.execute(
                "SELECT * FROM agents WHERE exploration_id = ? AND id = ?",
                (exploration_id, agent_id),
            ).fetchone()

            if existing:
                new_ver = int(existing["version"]) + 1
                conn.execute(
                    """
                    UPDATE agents
                    SET name = COALESCE(?, name),
                        role = COALESCE(?, role),
                        status = ?,
                        last_heartbeat = ?,
                        current_node_id = ?,
                        current_stage = ?,
                        version = ?
                    WHERE exploration_id = ? AND id = ?
                    """,
                    (
                        payload.get("name"),
                        payload.get("role"),
                        payload.get("status", "active"),
                        now,
                        payload.get("currentNodeId", existing["current_node_id"]),
                        payload.get("currentStage", existing["current_stage"]),
                        new_ver,
                        exploration_id,
                        agent_id,
                    ),
                )
            else:
                new_ver = 1
                conn.execute(
                    """
                    INSERT INTO agents
                    (id, exploration_id, name, role, status, last_heartbeat, current_node_id, current_stage, version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        agent_id,
                        exploration_id,
                        payload.get("name", agent_id),
                        payload.get("role", "Optimization Agent"),
                        payload.get("status", "active"),
                        now,
                        payload.get("currentNodeId"),
                        payload.get("currentStage"),
                    ),
                )

            directive = (
                "stop"
                if exp["coordinator_status"] in ("stopping", "stopped")
                else "continue"
            )
            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="agent",
                entity_id=agent_id,
                entity_version=new_ver,
                event_type="agent.heartbeat",
                payload={
                    "agentId": agent_id,
                    "status": payload.get("status", "active"),
                    "currentNodeId": payload.get("currentNodeId"),
                    "currentStage": payload.get("currentStage"),
                    "lastHeartbeat": now,
                    "directive": directive,
                },
            )
            result = {
                "agentId": agent_id,
                "version": new_ver,
                "directive": directive,
                "coordinatorStatus": exp["coordinator_status"],
                "stopReason": exp["stop_reason"],
                "event": evt,
            }
            self.storage.save_idempotency(
                conn,
                exploration_id,
                request_id,
                "agent_heartbeat",
                payload_hash,
                result,
            )
            return result

    # ------------------------------------------------------------------
    # Resource Upload (Temp File -> SHA256 Check -> Immutable Version)
    # ------------------------------------------------------------------
    def upload_resource_version(
        self,
        exploration_id: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
        *,
        _internal_seed: bool = False,
    ) -> Dict[str, Any]:
        self._reject_internal_flags(payload, _internal_seed)
        payload_hash = self.storage.compute_payload_hash(payload)
        version_id = payload["id"]
        resource_id = payload["resourceId"]
        content_status = payload.get("contentStatus", "hot")

        # Validate content_status rule: external agents can only create hot or ready-for-cool,
        # only internal root baseline initialization may register pinned cooled@v1.4 artifacts.
        if content_status == "cooled" and not _internal_seed:
            raise ValidationError(
                "hot -> cooled 必须经过人工审批并合入远端主线；当前接口禁止直接写入 cooled 状态"
            )

        is_binary = bool(payload.get("isBinary", False))
        content_str = payload.get("content", "")
        if payload.get("binaryBase64"):
            raw_bytes = base64.b64decode(payload["binaryBase64"])
        else:
            raw_bytes = content_str.encode("utf-8")

        expected_sha256 = payload.get("expectedSha256")
        actual_sha256, blob_rel_path, computed_size = self.storage.write_verified_blob(
            raw_bytes, expected_sha256=expected_sha256
        )
        size_bytes = int(payload.get("sizeBytes") or computed_size)

        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "upload_resource", payload_hash
            )
            if cached is not None:
                return cached

            existing_ver = conn.execute(
                "SELECT sha256 FROM resource_versions WHERE exploration_id = ? AND id = ?",
                (exploration_id, version_id),
            ).fetchone()
            if existing_ver:
                if existing_ver["sha256"].lower() != actual_sha256.lower():
                    raise ConflictError(
                        f"ResourceVersion '{version_id}' is immutable and already exists with a different SHA256."
                    )
                return {
                    "resourceVersionId": version_id,
                    "sha256": actual_sha256,
                    "sizeBytes": size_bytes,
                    "deduplicated": True,
                }

            now = payload.get("createdAt") or utc_now_iso()
            conn.execute(
                """
                INSERT OR IGNORE INTO resources (id, exploration_id, filename, category, owner_node_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    resource_id,
                    exploration_id,
                    payload["filename"],
                    payload["category"],
                    payload["ownerNodeId"],
                    now,
                ),
            )

            conn.execute(
                """
                INSERT INTO resource_versions (
                    id, exploration_id, resource_id, version, title, filename, category,
                    sha256, owner_node_id, content_status, created_at, size_bytes,
                    is_binary, missing_reason, blob_rel_path, preview_content,
                    experiment_data_json, profile_data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    exploration_id,
                    resource_id,
                    payload["version"],
                    payload["title"],
                    payload["filename"],
                    payload["category"],
                    actual_sha256,
                    payload["ownerNodeId"],
                    content_status,
                    now,
                    size_bytes,
                    1 if is_binary else 0,
                    payload.get("missingReason"),
                    blob_rel_path,
                    content_str,
                    (
                        json.dumps(payload["experimentData"], ensure_ascii=False)
                        if payload.get("experimentData")
                        else None
                    ),
                    (
                        json.dumps(payload["profileData"], ensure_ascii=False)
                        if payload.get("profileData")
                        else None
                    ),
                ),
            )

            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="resource_version",
                entity_id=version_id,
                entity_version=1,
                event_type="resource.uploaded",
                payload={
                    "resourceVersionId": version_id,
                    "resourceId": resource_id,
                    "version": payload["version"],
                    "filename": payload["filename"],
                    "category": payload["category"],
                    "ownerNodeId": payload["ownerNodeId"],
                    "sha256": actual_sha256,
                    "contentStatus": content_status,
                },
            )
            result = {
                "resourceVersionId": version_id,
                "sha256": actual_sha256,
                "sizeBytes": size_bytes,
                "event": evt,
            }
            self.storage.save_idempotency(
                conn,
                exploration_id,
                request_id,
                "upload_resource",
                payload_hash,
                result,
            )
            return result

    def create_resource_link(
        self,
        exploration_id: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload_hash = self.storage.compute_payload_hash(payload)
        link_id = (
            payload.get("id")
            or f"link-{payload['nodeId']}-{payload['resourceVersionId']}-{payload.get('role', 'input')}"
        )

        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "create_resource_link", payload_hash
            )
            if cached is not None:
                return cached

            ver_row = conn.execute(
                "SELECT owner_node_id FROM resource_versions WHERE exploration_id = ? AND id = ?",
                (exploration_id, payload["resourceVersionId"]),
            ).fetchone()
            if not ver_row:
                raise NotFoundError(
                    f"ResourceVersion '{payload['resourceVersionId']}' not found"
                )

            source_node_id = payload.get("sourceNodeId") or ver_row["owner_node_id"]
            is_shared = bool(
                payload.get("isSharedReference", source_node_id != payload["nodeId"])
            )

            conn.execute(
                """
                INSERT OR REPLACE INTO resource_links (
                    id, exploration_id, node_id, stage_type, attempt_id,
                    resource_version_id, role, source_node_id, is_shared_reference, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link_id,
                    exploration_id,
                    payload["nodeId"],
                    payload.get("stageType"),
                    payload.get("attemptId"),
                    payload["resourceVersionId"],
                    payload["role"],
                    source_node_id,
                    1 if is_shared else 0,
                    payload.get("note"),
                    utc_now_iso(),
                ),
            )

            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="resource_link",
                entity_id=link_id,
                entity_version=1,
                event_type="resource.linked",
                payload={
                    "linkId": link_id,
                    "nodeId": payload["nodeId"],
                    "stageType": payload.get("stageType"),
                    "attemptId": payload.get("attemptId"),
                    "resourceVersionId": payload["resourceVersionId"],
                    "role": payload["role"],
                    "sourceNodeId": source_node_id,
                    "isSharedReference": is_shared,
                },
            )
            result = {"linkId": link_id, "event": evt}
            self.storage.save_idempotency(
                conn,
                exploration_id,
                request_id,
                "create_resource_link",
                payload_hash,
                result,
            )
            return result

    # ------------------------------------------------------------------
    # Node Creation (Single-Parent Acyclic Validation)
    # ------------------------------------------------------------------
    def create_node(
        self,
        exploration_id: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
        *,
        _internal_seed: bool = False,
    ) -> Dict[str, Any]:
        self._reject_internal_flags(payload, _internal_seed)
        payload_hash = self.storage.compute_payload_hash(payload)
        node_id = payload["id"]
        parents = payload.get(
            "parentIds", [payload["parentId"]] if payload.get("parentId") else []
        )
        if (
            not isinstance(parents, list)
            or not all(isinstance(p, str) for p in parents)
            or len(set(parents)) != len(parents)
        ):
            raise ValidationError("parentIds must contain distinct node IDs")
        if payload.get("parentId") and parents and payload["parentId"] != parents[0]:
            raise ValidationError(
                "Legacy parentId must agree with first parentIds entry"
            )
        contributions = payload.get("sourceContributions", {})
        if not isinstance(contributions, dict) or not all(
            k in parents and isinstance(v, str) for k, v in contributions.items()
        ):
            raise ValidationError(
                "sourceContributions maps source node IDs to contribution descriptions"
            )
        parent_id = parents[0] if parents else None
        content_status = payload.get("contentStatus", "hot")

        if content_status == "cooled" and not _internal_seed:
            raise ValidationError("禁止直接创建 cooled 状态的节点")
        if not _internal_seed and (
            payload.get("correctnessPassed")
            or payload.get("latencyUs") is not None
            or payload.get("speedupVsBaseline") is not None
        ):
            raise ValidationError(
                "创建节点时禁止直接写入验证通过状态或实测耗时；必须通过节点内 8 阶段状态机产生"
            )

        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "create_node", payload_hash
            )
            if cached is not None:
                return cached

            exp = conn.execute(
                "SELECT coordinator_status FROM explorations WHERE id = ?",
                (exploration_id,),
            ).fetchone()
            if not exp:
                raise NotFoundError(f"Exploration '{exploration_id}' not found")
            if (
                exp["coordinator_status"] in ("stopping", "stopped")
                and not _internal_seed
            ):
                raise ValidationError(
                    f"Exploration is '{exp['coordinator_status']}'; creating new nodes is forbidden."
                )

            existing = conn.execute(
                "SELECT id FROM nodes WHERE exploration_id = ? AND id = ?",
                (exploration_id, node_id),
            ).fetchone()
            if existing:
                raise ConflictError(f"Node '{node_id}' already exists")

            if payload.get("primaryKernelVersionId"):
                self._require_kernel(
                    conn, exploration_id, payload["primaryKernelVersionId"]
                )

            depth = 0
            for source in parents:
                if source == node_id:
                    raise ValidationError("Node cannot derive from itself")
                row = conn.execute(
                    "SELECT depth FROM nodes WHERE exploration_id=? AND id=?",
                    (exploration_id, source),
                ).fetchone()
                if not row:
                    raise NotFoundError(f"Source node '{source}' not found")
                depth = max(depth, int(row["depth"]) + 1)

            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO nodes (
                    id, exploration_id, parent_id, depth, branch_label, title,
                    hypothesis, change_summary, derivation_note, primary_kernel_version_id,
                    agent_ids_json, execution_status, content_status, current_stage,
                    latency_us, speedup_vs_baseline, correctness_passed,
                    stop_or_fail_reason, pipeline_metrics_json, missing_stage_reasons_json,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    node_id,
                    exploration_id,
                    parent_id,
                    depth,
                    payload.get("branchLabel", ""),
                    payload["title"],
                    payload.get("hypothesis", ""),
                    payload.get("changeSummary", ""),
                    payload.get("derivationNote", ""),
                    payload.get("primaryKernelVersionId"),
                    json.dumps(payload.get("agentIds", []), ensure_ascii=False),
                    payload.get("executionStatus", "pending"),
                    content_status,
                    payload.get("currentStage", "read_info"),
                    None,
                    None,
                    0,
                    payload.get("stopOrFailReason"),
                    (
                        json.dumps(payload["pipelineMetrics"], ensure_ascii=False)
                        if payload.get("pipelineMetrics")
                        else None
                    ),
                    json.dumps(
                        payload.get("missingStageReasons", {}), ensure_ascii=False
                    ),
                    now,
                    now,
                ),
            )

            for source in parents:
                if source == parent_id and payload.get("derivationReference"):
                    continue
                relation = dict(
                    id=f"derive:{source}:{node_id}",
                    kind="derivation",
                    sourceType="node",
                    sourceId=source,
                    targetType="node",
                    targetId=node_id,
                    rationale=payload.get("sourceContributions", {}).get(source)
                    or payload.get("derivationNote")
                    or payload.get("changeSummary")
                    or "方案派生（尚未补充贡献说明）",
                )
                conn.execute(
                    "INSERT INTO graph_relations VALUES (?,?,?)",
                    (
                        relation["id"],
                        exploration_id,
                        json.dumps(relation, ensure_ascii=False),
                    ),
                )

            # Automatically register derivation ReferenceRecord if parent_id is present and derivationReference is provided
            derivation_ref = payload.get("derivationReference")
            if parent_id and derivation_ref:
                ref_id = (
                    derivation_ref.get("id")
                    or f"ref-{parent_id.lower()}-{node_id.lower()}-deriv"
                )
                conn.execute(
                    """
                    INSERT INTO references_table (
                        id, exploration_id, source_node_id, target_node_id, purpose,
                        referenced_node_id, referenced_knowledge_id, referenced_knowledge_version,
                        referenced_resource_version_id, title, rationale,
                        reverified_in_attempt_id, reverified_status, created_at
                    ) VALUES (?, ?, ?, ?, 'derivation', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ref_id,
                        exploration_id,
                        parent_id,
                        node_id,
                        derivation_ref.get("referencedNodeId", parent_id),
                        derivation_ref.get("referencedKnowledgeId"),
                        derivation_ref.get("referencedKnowledgeVersion"),
                        derivation_ref.get("referencedResourceVersionId"),
                        derivation_ref.get("title", f"派生: {parent_id} → {node_id}"),
                        derivation_ref.get(
                            "rationale", payload.get("derivationNote", "")
                        ),
                        derivation_ref.get("reverifiedInAttemptId"),
                        derivation_ref.get("reverifiedStatus", "pending_reverify"),
                        now,
                    ),
                )

            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="node",
                entity_id=node_id,
                entity_version=1,
                event_type="node.created",
                payload={
                    "nodeId": node_id,
                    "parentId": parent_id,
                    "depth": depth,
                    "branchLabel": payload.get("branchLabel", ""),
                    "title": payload["title"],
                },
            )
            result = {"nodeId": node_id, "depth": depth, "version": 1, "event": evt}
            self.storage.save_idempotency(
                conn, exploration_id, request_id, "create_node", payload_hash, result
            )
            return result

    # ------------------------------------------------------------------
    # Reference Creation (Derivation vs Cross-Branch Reference)
    # ------------------------------------------------------------------
    def create_reference(
        self,
        exploration_id: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload_hash = self.storage.compute_payload_hash(payload)
        ref_id = payload["id"]
        source_node_id = payload["sourceNodeId"]
        target_node_id = payload["targetNodeId"]
        purpose = payload["purpose"]

        if source_node_id == target_node_id:
            raise ValidationError(
                "Reference sourceNodeId and targetNodeId cannot be identical"
            )
        if purpose not in ("derivation", "cross_branch"):
            raise ValidationError("purpose must be 'derivation' or 'cross_branch'")

        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "create_reference", payload_hash
            )
            if cached is not None:
                return cached

            src_node = conn.execute(
                "SELECT id, parent_id FROM nodes WHERE exploration_id = ? AND id = ?",
                (exploration_id, source_node_id),
            ).fetchone()
            tgt_node = conn.execute(
                "SELECT id, parent_id, correctness_passed FROM nodes WHERE exploration_id = ? AND id = ?",
                (exploration_id, target_node_id),
            ).fetchone()
            if not src_node or not tgt_node:
                raise NotFoundError("Source or target node for reference not found")

            if purpose == "derivation":
                from .graph import validate_relation

                validate_relation(
                    conn,
                    exploration_id,
                    dict(
                        kind="derivation",
                        sourceType="node",
                        sourceId=source_node_id,
                        targetType="node",
                        targetId=target_node_id,
                        rationale=payload.get("rationale") or "legacy derivation",
                    ),
                )

            # Enforce re-verification rule for cross_branch references:
            # Cannot inherit verified status from source node unless target node has its own verified attempt.
            reverified_in = payload.get("reverifiedInAttemptId")
            if purpose == "cross_branch" and not reverified_in:
                reverified_status = "pending_reverify"
            else:
                reverified_status = payload.get("reverifiedStatus", "pending_reverify")

            conn.execute(
                """
                INSERT OR REPLACE INTO references_table (
                    id, exploration_id, source_node_id, target_node_id, purpose,
                    referenced_node_id, referenced_knowledge_id, referenced_knowledge_version,
                    referenced_resource_version_id, title, rationale,
                    reverified_in_attempt_id, reverified_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ref_id,
                    exploration_id,
                    source_node_id,
                    target_node_id,
                    purpose,
                    payload.get("referencedNodeId", source_node_id),
                    payload.get("referencedKnowledgeId"),
                    payload.get("referencedKnowledgeVersion"),
                    payload.get("referencedResourceVersionId"),
                    payload["title"],
                    payload["rationale"],
                    reverified_in,
                    reverified_status,
                    utc_now_iso(),
                ),
            )

            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="reference",
                entity_id=ref_id,
                entity_version=1,
                event_type="reference.created",
                payload={
                    "referenceId": ref_id,
                    "sourceNodeId": source_node_id,
                    "targetNodeId": target_node_id,
                    "purpose": purpose,
                    "title": payload["title"],
                    "reverifiedStatus": reverified_status,
                },
            )
            result = {
                "referenceId": ref_id,
                "reverifiedStatus": reverified_status,
                "event": evt,
            }
            self.storage.save_idempotency(
                conn,
                exploration_id,
                request_id,
                "create_reference",
                payload_hash,
                result,
            )
            return result

    # ------------------------------------------------------------------
    # Stage Attempt Permit & Execution (Rollback / Dependency Validation)
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_stage_preconditions(
        stage_type: str,
        kernel_ver_label: Optional[str],
        upstream_rows: List[Any],
    ) -> None:
        """Enforce mandatory upstream stage preconditions and kernel version lineage."""
        if stage_type == "read_info":
            return

        if not upstream_rows:
            raise ValidationError(
                f"Stage '{stage_type}' requires non-empty dependsOnAttemptIds referencing valid upstream attempt(s) in the same node."
            )

        if stage_type == "plan_idea":
            if not any(
                r["stage_type"] == "read_info" and r["status"] == "completed"
                for r in upstream_rows
            ):
                raise ValidationError("plan_idea 阶段必须依赖已完成的 read_info 尝试")
            return

        if stage_type == "gen_kernel":
            if not kernel_ver_label:
                raise ValidationError(
                    "gen_kernel 阶段必须明确指定 kernelVersionLabel (如 v1, v2)"
                )
            has_plan = any(
                r["stage_type"] == "plan_idea" and r["status"] == "completed"
                for r in upstream_rows
            )
            has_rollback_trigger = any(
                r["stage_type"] in ("compile", "verify_correctness")
                and r["status"] in ("failed", "skipped")
                for r in upstream_rows
            )
            if not (has_plan or has_rollback_trigger):
                raise ValidationError(
                    "gen_kernel 阶段必须依赖已完成的 plan_idea 尝试，或依赖失败的下游 compile/verify_correctness 尝试以执行回退修复"
                )
            return

        if stage_type in ("compile", "verify_correctness", "bench_perf", "profiling"):
            if not kernel_ver_label:
                raise ValidationError(
                    f"Stage '{stage_type}' 必须明确指定 kernelVersionLabel 并与上游保持一致"
                )
            for r in upstream_rows:
                dep_kv = r["kernel_version_label"]
                if dep_kv != kernel_ver_label:
                    raise ValidationError(
                        f"Kernel version mismatch: stage '{stage_type}' ({kernel_ver_label}) cannot depend on upstream attempt '{r['id']}' ({dep_kv})."
                    )

        if stage_type == "compile":
            if not any(
                r["stage_type"] == "gen_kernel"
                and r["status"] == "completed"
                and r["kernel_version_label"] == kernel_ver_label
                for r in upstream_rows
            ):
                raise ValidationError(
                    f"compile ({kernel_ver_label}) 必须依赖已完成且同版本的 gen_kernel 尝试"
                )
        elif stage_type == "verify_correctness":
            if not any(
                r["stage_type"] == "compile"
                and r["status"] in ("completed", "failed")
                and r["kernel_version_label"] == kernel_ver_label
                for r in upstream_rows
            ):
                raise ValidationError(
                    f"verify_correctness ({kernel_ver_label}) 必须依赖同版本的 compile 尝试"
                )
        elif stage_type == "bench_perf":
            if not any(
                r["stage_type"] == "verify_correctness"
                and r["status"] == "completed"
                and int(r["correctness_passed"] or 0) == 1
                and r["kernel_version_label"] == kernel_ver_label
                for r in upstream_rows
            ):
                raise ValidationError(
                    f"bench_perf ({kernel_ver_label}) 必须依赖已完成且精度通过的同版本 verify_correctness 尝试"
                )
        elif stage_type == "profiling":
            if not any(
                r["stage_type"] == "bench_perf"
                and r["status"] == "completed"
                and r["latency_us"] is not None
                and r["kernel_version_label"] == kernel_ver_label
                for r in upstream_rows
            ):
                raise ValidationError(
                    f"profiling ({kernel_ver_label}) 必须依赖已完成且记录耗时的同版本 bench_perf 尝试"
                )
        elif stage_type == "summarize":
            if not any(
                r["status"] in ("completed", "failed", "stopped") for r in upstream_rows
            ):
                raise ValidationError("summarize 阶段必须依赖已结束的上游阶段尝试")

    @staticmethod
    def _evaluate_node_active_chain(
        conn: Any, exploration_id: str, node_id: str
    ) -> Tuple[bool, Optional[float]]:
        """
        Evaluate whether the node's latest kernel version has a complete, version-matched
        chain: gen_kernel(kv, completed) -> compile(kv, completed) -> verify_correctness(kv, completed, pass)
        and optional bench_perf(kv, completed, latency_us > 0).
        """
        rows = conn.execute(
            """
            SELECT id, stage_type, status, kernel_version_label,
                   depends_on_attempt_ids_json, output_resource_version_ids_json,
                   correctness_passed, latency_us, seq_order
            FROM stage_attempts
            WHERE exploration_id = ? AND node_id = ?
            ORDER BY seq_order ASC, id ASC
            """,
            (exploration_id, node_id),
        ).fetchall()

        gen_attempts = [r for r in rows if r["stage_type"] == "gen_kernel"]
        if not gen_attempts:
            return (False, None)

        latest_gen = gen_attempts[-1]
        if (
            latest_gen["status"] != "completed"
            or not latest_gen["kernel_version_label"]
        ):
            return (False, None)

        active_kv = latest_gen["kernel_version_label"]
        gen_id = latest_gen["id"]
        gen_seq = int(latest_gen["seq_order"])

        # Require completed compile attempt for active_kv depending on latest_gen
        valid_compile_ids = set()
        for r in rows:
            if (
                r["stage_type"] == "compile"
                and r["status"] == "completed"
                and r["kernel_version_label"] == active_kv
                and int(r["seq_order"]) > gen_seq
            ):
                deps = json.loads(r["depends_on_attempt_ids_json"] or "[]")
                if gen_id in deps:
                    valid_compile_ids.add(r["id"])

        if not valid_compile_ids:
            return (False, None)

        # Require completed verify_correctness attempt with correctness_passed == 1 depending on valid compile
        valid_verify_ids = set()
        for r in rows:
            if (
                r["stage_type"] == "verify_correctness"
                and r["status"] == "completed"
                and int(r["correctness_passed"] or 0) == 1
                and r["kernel_version_label"] == active_kv
                and int(r["seq_order"]) > gen_seq
            ):
                deps = set(json.loads(r["depends_on_attempt_ids_json"] or "[]"))
                if deps & valid_compile_ids:
                    valid_verify_ids.add(r["id"])

        if not valid_verify_ids:
            return (False, None)

        # Find latest completed bench_perf attempt for active_kv depending on valid verify_correctness
        valid_latency: Optional[float] = None
        for r in rows:
            if (
                r["stage_type"] == "bench_perf"
                and r["status"] == "completed"
                and r["latency_us"] is not None
                and float(r["latency_us"]) > 0
                and r["kernel_version_label"] == active_kv
                and int(r["seq_order"]) > gen_seq
            ):
                deps = set(json.loads(r["depends_on_attempt_ids_json"] or "[]"))
                if deps & valid_verify_ids:
                    valid_latency = float(r["latency_us"])

        return (True, valid_latency)

    def start_stage_attempt(
        self,
        exploration_id: str,
        node_id: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
        *,
        _internal_seed: bool = False,
    ) -> Dict[str, Any]:
        self._reject_internal_flags(payload, _internal_seed)
        payload_hash = self.storage.compute_payload_hash(payload)
        attempt_id = payload["id"]
        stage_type = payload["stageType"]
        agent_id = payload["agentId"]
        depends_on: List[str] = payload.get("dependsOnAttemptIds", [])
        kernel_ver_label = payload.get("kernelVersionLabel")
        reused_from = payload.get("reusedFromAttemptId")

        if stage_type not in STAGE_TYPES:
            raise ValidationError(f"Invalid stageType '{stage_type}'")

        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "start_stage_attempt", payload_hash
            )
            if cached is not None:
                return cached

            exp = conn.execute(
                "SELECT coordinator_status, budget_json, version FROM explorations WHERE id = ?",
                (exploration_id,),
            ).fetchone()
            if not exp:
                raise NotFoundError(f"Exploration '{exploration_id}' not found")
            if (
                exp["coordinator_status"] in ("stopping", "stopped")
                and not _internal_seed
            ):
                raise ValidationError(
                    f"Exploration is '{exp['coordinator_status']}'; new stage permits are rejected."
                )

            node = conn.execute(
                "SELECT * FROM nodes WHERE exploration_id = ? AND id = ?",
                (exploration_id, node_id),
            ).fetchone()
            if not node:
                raise NotFoundError(f"Node '{node_id}' not found")

            if not conn.execute(
                "SELECT 1 FROM agents WHERE exploration_id = ? AND id = ?",
                (exploration_id, agent_id),
            ).fetchone():
                raise ValidationError(
                    "Register the agent session through heartbeat before starting an attempt"
                )

            if conn.execute(
                "SELECT 1 FROM closed_sessions WHERE exploration_id=? AND session_id=?",
                (exploration_id, agent_id),
            ).fetchone():
                raise ConflictError("Session was closed; register a new session")
            # Single active executor check on this attempt_id
            existing_att = conn.execute(
                "SELECT id, agent_id FROM stage_attempts WHERE exploration_id = ? AND id = ?",
                (exploration_id, attempt_id),
            ).fetchone()
            if existing_att:
                raise ConflictError(
                    f"StageAttempt '{attempt_id}' already exists (claimed by '{existing_att['agent_id']}')"
                )

            # Validate upstream attempt dependencies belong to the same node
            upstream_rows = []
            for dep_id in depends_on:
                dep_row = conn.execute(
                    """
                    SELECT id, stage_type, status, kernel_version_label, correctness_passed, latency_us
                    FROM stage_attempts
                    WHERE exploration_id = ? AND node_id = ? AND id = ?
                    """,
                    (exploration_id, node_id, dep_id),
                ).fetchone()
                if not dep_row:
                    raise ValidationError(
                        f"Upstream dependency attempt '{dep_id}' not found in node '{node_id}'"
                    )
                upstream_rows.append(dep_row)

            # Enforce mandatory stage preconditions & version consistency
            self._validate_stage_preconditions(
                stage_type, kernel_ver_label, upstream_rows
            )

            input_ids = list(payload.get("inputResourceVersionIds", []))
            if stage_type in (
                "compile",
                "verify_correctness",
                "bench_perf",
                "profiling",
            ):
                inherited = set()
                for dep_id in depends_on:
                    dep = conn.execute(
                        "SELECT input_resource_version_ids_json, output_resource_version_ids_json, stage_type FROM stage_attempts WHERE exploration_id = ? AND node_id = ? AND id = ?",
                        (exploration_id, node_id, dep_id),
                    ).fetchone()
                    for rid in (
                        json.loads(dep[1])
                        if dep[2] == "gen_kernel"
                        else json.loads(dep[0])
                    ):
                        res = conn.execute(
                            "SELECT category FROM resource_versions WHERE exploration_id = ? AND id = ?",
                            (exploration_id, rid),
                        ).fetchone()
                        if res and res[0] == "kernel":
                            inherited.add(rid)
                if len(inherited) != 1:
                    raise ValidationError(
                        "Stage dependencies must identify exactly one kernel resource version"
                    )
                for rid in input_ids:
                    res = conn.execute(
                        "SELECT category FROM resource_versions WHERE exploration_id = ? AND id = ?",
                        (exploration_id, rid),
                    ).fetchone()
                    if not res or (res[0] == "kernel" and rid not in inherited):
                        raise ValidationError(
                            "Input resource is missing or mismatches upstream kernel"
                        )
                input_ids = list(dict.fromkeys([*input_ids, *sorted(inherited)]))

            if reused_from:
                reused_row = conn.execute(
                    """
                    SELECT id, status, kernel_version_label, correctness_passed
                    FROM stage_attempts
                    WHERE exploration_id = ? AND node_id = ? AND id = ?
                    """,
                    (exploration_id, node_id, reused_from),
                ).fetchone()
                if not reused_row or reused_row["status"] != "completed":
                    raise ValidationError(
                        f"Cannot reuse incomplete attempt '{reused_from}'"
                    )
                if (
                    kernel_ver_label
                    and reused_row["kernel_version_label"]
                    and reused_row["kernel_version_label"] != kernel_ver_label
                ):
                    raise ValidationError(
                        f"Cannot reuse attempt '{reused_from}' ({reused_row['kernel_version_label']}) for new kernel version '{kernel_ver_label}'"
                    )

            # Reserve evaluation budget if this is a hardware evaluation stage
            budget = json.loads(exp["budget_json"])
            reserve_slot = 0
            if stage_type in EVAL_BUDGET_STAGES:
                used = int(budget.get("usedEvaluations", 0))
                reserved = int(budget.get("reservedEvaluations", 0))
                max_evals = int(budget.get("maxEvaluations", 24))
                if used + reserved >= max_evals:
                    conn.execute(
                        """
                        UPDATE explorations
                        SET coordinator_status = 'stopping',
                            stop_reason = '全局设备评估预算已达上限（含在途预留），禁止发放新许可',
                            version = version + 1,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (utc_now_iso(), exploration_id),
                    )
                    self.storage.append_event(
                        conn,
                        exploration_id,
                        "exploration",
                        exploration_id,
                        int(exp["version"]) + 1,
                        "exploration.stopping",
                        {
                            "coordinatorStatus": "stopping",
                            "reason": "evaluation_budget_exhausted",
                        },
                    )
                    # This is a persisted coordinator decision, not a failed mutation.
                    # Commit before rejecting the permit; write_tx's rollback is then a no-op.
                    conn.commit()
                    with self.storage._event_cond:
                        self.storage._event_cond.notify_all()
                    raise ValidationError(
                        f"Evaluation budget exhausted ({used} used + {reserved} reserved / {max_evals})"
                    )
                budget["reservedEvaluations"] = reserved + 1
                reserve_slot = 1
                conn.execute(
                    "UPDATE explorations SET budget_json = ?, version = version + 1, updated_at = ? WHERE id = ?",
                    (
                        json.dumps(budget, ensure_ascii=False),
                        utc_now_iso(),
                        exploration_id,
                    ),
                )

            seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq_order), 0) + 1 AS next_seq FROM stage_attempts WHERE exploration_id = ?",
                (exploration_id,),
            ).fetchone()
            next_seq = int(seq_row["next_seq"]) if seq_row else 1

            started_at = payload.get("startedAt") or utc_now_iso()
            conn.execute(
                """
                INSERT INTO stage_attempts (
                    id, exploration_id, node_id, stage_type, code, round, agent_id,
                    status, started_at, ended_at, depends_on_attempt_ids_json,
                    kernel_version_label, input_resource_version_ids_json,
                    output_resource_version_ids_json, summary, failure_or_skip_reason,
                    reused_from_attempt_id, correctness_passed, latency_us,
                    reserved_eval_slot, seq_order, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, NULL, ?, ?, ?, '[]', ?, NULL, ?, NULL, NULL, ?, ?, 1)
                """,
                (
                    attempt_id,
                    exploration_id,
                    node_id,
                    stage_type,
                    payload["code"],
                    int(payload.get("round", 1)),
                    agent_id,
                    started_at,
                    json.dumps(depends_on, ensure_ascii=False),
                    kernel_ver_label,
                    json.dumps(input_ids, ensure_ascii=False),
                    payload.get(
                        "summary",
                        f"{agent_id} 正在执行 {stage_type} ({payload['code']})...",
                    ),
                    reused_from,
                    reserve_slot,
                    next_seq,
                ),
            )

            # Register input resource links
            for in_ver_id in input_ids:
                ver_row = conn.execute(
                    "SELECT owner_node_id FROM resource_versions WHERE exploration_id = ? AND id = ?",
                    (exploration_id, in_ver_id),
                ).fetchone()
                if ver_row:
                    src_node = ver_row["owner_node_id"]
                    link_id = f"link-{node_id}-{attempt_id}-in-{in_ver_id}"
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO resource_links (
                            id, exploration_id, node_id, stage_type, attempt_id,
                            resource_version_id, role, source_node_id, is_shared_reference, note, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'input', ?, ?, ?, ?)
                        """,
                        (
                            link_id,
                            exploration_id,
                            node_id,
                            stage_type,
                            attempt_id,
                            in_ver_id,
                            src_node,
                            1 if src_node != node_id else 0,
                            f"{payload['code']} 输入",
                            started_at,
                        ),
                    )

            # Update node participating agents & current_stage
            agent_ids: List[str] = json.loads(node["agent_ids_json"])
            if agent_id not in agent_ids:
                agent_ids.append(agent_id)
            new_node_ver = int(node["version"]) + 1

            # When starting gen_kernel for a new kernel version, clear node correctness_passed AND old latency/speedup
            # so previous @v1 metrics can never stand in for @v2!
            if stage_type == "gen_kernel":
                reset_correctness = 0
                next_latency = None
                next_speedup = None
                next_pipeline = None
            else:
                reset_correctness = int(node["correctness_passed"])
                next_latency = node["latency_us"]
                next_speedup = node["speedup_vs_baseline"]
                next_pipeline = node["pipeline_metrics_json"]

            conn.execute(
                """
                UPDATE nodes
                SET agent_ids_json = ?,
                    execution_status = 'running',
                    current_stage = ?,
                    correctness_passed = ?,
                    latency_us = ?,
                    speedup_vs_baseline = ?,
                    pipeline_metrics_json = ?,
                    version = ?,
                    updated_at = ?
                WHERE exploration_id = ? AND id = ?
                """,
                (
                    json.dumps(agent_ids, ensure_ascii=False),
                    stage_type,
                    reset_correctness,
                    next_latency,
                    next_speedup,
                    next_pipeline,
                    new_node_ver,
                    utc_now_iso(),
                    exploration_id,
                    node_id,
                ),
            )

            # Recompute best_valid_node_id in case this node was previously best_valid_node_id and just started a new kernel version
            best_row = conn.execute(
                """
                SELECT id, latency_us FROM nodes
                WHERE exploration_id = ? AND correctness_passed = 1 AND latency_us IS NOT NULL
                ORDER BY latency_us ASC
                LIMIT 1
                """,
                (exploration_id,),
            ).fetchone()
            if best_row:
                conn.execute(
                    "UPDATE explorations SET best_valid_node_id = ?, updated_at = ? WHERE id = ?",
                    (best_row["id"], utc_now_iso(), exploration_id),
                )

            # Update agent current position
            conn.execute(
                """
                UPDATE agents
                SET status = 'active',
                    current_node_id = ?,
                    current_stage = ?,
                    last_heartbeat = ?,
                    version = version + 1
                WHERE exploration_id = ? AND id = ?
                """,
                (node_id, stage_type, started_at, exploration_id, agent_id),
            )

            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="stage_attempt",
                entity_id=attempt_id,
                entity_version=1,
                event_type="attempt.started",
                payload={
                    "attemptId": attempt_id,
                    "nodeId": node_id,
                    "stageType": stage_type,
                    "code": payload["code"],
                    "agentId": agent_id,
                    "dependsOnAttemptIds": depends_on,
                    "kernelVersionLabel": kernel_ver_label,
                    "budget": budget,
                },
            )
            result = {
                "attemptId": attempt_id,
                "nodeId": node_id,
                "stageType": stage_type,
                "status": "running",
                "budget": budget,
                "event": evt,
            }
            self.storage.save_idempotency(
                conn,
                exploration_id,
                request_id,
                "start_stage_attempt",
                payload_hash,
                result,
            )
            return result

    def finish_stage_attempt(
        self,
        exploration_id: str,
        node_id: str,
        attempt_id: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
        *,
        _internal_seed: bool = False,
    ) -> Dict[str, Any]:
        self._reject_internal_flags(payload, _internal_seed)
        payload_hash = self.storage.compute_payload_hash(payload)
        status = payload["status"]
        if status not in ("completed", "failed", "skipped", "stopped"):
            raise ValidationError(f"Invalid terminal attempt status '{status}'")
        if status == "skipped" and not payload.get("failureOrSkipReason"):
            raise ValidationError("跳过阶段必须提供明确原因 (failureOrSkipReason)")

        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "finish_stage_attempt", payload_hash
            )
            if cached is not None:
                return cached

            att = conn.execute(
                """
                SELECT * FROM stage_attempts
                WHERE exploration_id = ? AND node_id = ? AND id = ?
                """,
                (exploration_id, node_id, attempt_id),
            ).fetchone()
            if not att:
                raise NotFoundError(f"StageAttempt '{attempt_id}' not found")

            if att["status"] != "running":
                raise ConflictError(
                    "Terminal attempts are immutable; retry with a new attempt ID"
                )

            expected_ver = payload.get("expectedVersion")
            if expected_ver is not None and int(att["version"]) != int(expected_ver):
                raise ConflictError(
                    f"Version conflict on attempt '{attempt_id}': expected {expected_ver}, actual {att['version']}"
                )

            exp = conn.execute(
                "SELECT * FROM explorations WHERE id = ?", (exploration_id,)
            ).fetchone()
            node = conn.execute(
                "SELECT * FROM nodes WHERE exploration_id = ? AND id = ?",
                (exploration_id, node_id),
            ).fetchone()
            if not exp or not node:
                raise NotFoundError("Exploration or Node not found")

            if (
                payload.get("primaryKernelVersionId")
                and payload["primaryKernelVersionId"]
                != node["primary_kernel_version_id"]
            ):
                raise ValidationError(
                    "Kernel changes must be recorded as gen_kernel outputs"
                )
            stage_type = att["stage_type"]
            out_ver_ids: List[str] = payload.get("outputResourceVersionIds", [])

            # Stage-specific completion validations
            if stage_type != "bench_perf" and (
                payload.get("latencyUs") is not None
                or payload.get("speedupVsBaseline") is not None
            ):
                raise ValidationError(
                    f"Stage '{stage_type}' 禁止直接上报实测耗时 latencyUs；耗时仅能由 bench_perf 阶段产生"
                )

            att_correctness: Optional[int] = None
            att_latency: Optional[float] = None

            if stage_type == "gen_kernel" and status == "completed":
                kernel_ids = []
                for version_id in out_ver_ids:
                    resource = conn.execute(
                        "SELECT category FROM resource_versions WHERE exploration_id = ? AND id = ?",
                        (exploration_id, version_id),
                    ).fetchone()
                    if not resource:
                        raise ValidationError(f"Unknown output resource '{version_id}'")
                    if resource["category"] == "kernel":
                        kernel_ids.append(version_id)
                if not kernel_ids and node["primary_kernel_version_id"]:
                    kernel_ids = [node["primary_kernel_version_id"]]
                if len(kernel_ids) != 1:
                    raise ValidationError(
                        "gen_kernel must identify exactly one kernel resource version"
                    )
                self._require_kernel(conn, exploration_id, kernel_ids[0])
                if kernel_ids[0] not in out_ver_ids:
                    out_ver_ids = [*out_ver_ids, kernel_ids[0]]

            if stage_type == "verify_correctness":
                if status == "completed":
                    if not isinstance(payload.get("correctnessPassed"), bool):
                        raise ValidationError(
                            "完成 verify_correctness 阶段必须显式提供布尔值 correctnessPassed"
                        )
                    # Ensure upstream compile attempt actually completed
                    deps = json.loads(att["depends_on_attempt_ids_json"] or "[]")
                    comp_ok = False
                    for dep_id in deps:
                        c_row = conn.execute(
                            "SELECT stage_type, status, kernel_version_label FROM stage_attempts WHERE exploration_id = ? AND node_id = ? AND id = ?",
                            (exploration_id, node_id, dep_id),
                        ).fetchone()
                        if (
                            c_row
                            and c_row["stage_type"] == "compile"
                            and c_row["status"] == "completed"
                            and c_row["kernel_version_label"]
                            == att["kernel_version_label"]
                        ):
                            comp_ok = True
                    if not comp_ok:
                        raise ValidationError(
                            "上游 compile 阶段未成功完成，verify_correctness 禁止标记为 completed"
                        )
                    att_correctness = 1 if payload["correctnessPassed"] else 0
                elif status == "failed":
                    att_correctness = 0

            if stage_type == "bench_perf" and status == "completed":
                raw_lat = payload.get("latencyUs")
                if raw_lat is None or float(raw_lat) <= 0:
                    raise ValidationError(
                        "完成 bench_perf 阶段必须提供正数实测耗时 latencyUs"
                    )
                att_latency = float(raw_lat)

            ended_at = payload.get("endedAt") or utc_now_iso()
            new_att_ver = int(att["version"]) + 1

            conn.execute(
                """
                UPDATE stage_attempts
                SET status = ?,
                    ended_at = ?,
                    output_resource_version_ids_json = ?,
                    summary = ?,
                    failure_or_skip_reason = ?,
                    correctness_passed = ?,
                    latency_us = ?,
                    reserved_eval_slot = 0,
                    version = ?
                WHERE exploration_id = ? AND id = ?
                """,
                (
                    status,
                    ended_at,
                    json.dumps(out_ver_ids, ensure_ascii=False),
                    payload.get("summary", att["summary"]),
                    payload.get("failureOrSkipReason"),
                    att_correctness,
                    att_latency,
                    new_att_ver,
                    exploration_id,
                    attempt_id,
                ),
            )

            # Register output resource links
            for out_ver_id in out_ver_ids:
                ver_row = conn.execute(
                    "SELECT owner_node_id FROM resource_versions WHERE exploration_id = ? AND id = ?",
                    (exploration_id, out_ver_id),
                ).fetchone()
                src_node = ver_row["owner_node_id"] if ver_row else node_id
                link_id = f"link-{node_id}-{attempt_id}-out-{out_ver_id}"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO resource_links (
                        id, exploration_id, node_id, stage_type, attempt_id,
                        resource_version_id, role, source_node_id, is_shared_reference, note, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'output', ?, ?, ?, ?)
                    """,
                    (
                        link_id,
                        exploration_id,
                        node_id,
                        stage_type,
                        attempt_id,
                        out_ver_id,
                        src_node,
                        1 if src_node != node_id else 0,
                        f"{att['code']} 产出",
                        ended_at,
                    ),
                )

            # Settle evaluation budget if reserved
            budget = json.loads(exp["budget_json"])
            if int(att["reserved_eval_slot"]) == 1:
                budget["reservedEvaluations"] = max(
                    0, int(budget.get("reservedEvaluations", 1)) - 1
                )
                budget["usedEvaluations"] = int(budget.get("usedEvaluations", 0)) + 1

            # Update node fields based on strict version-bound attempt chain evaluation
            primary_kernel = (
                payload.get("primaryKernelVersionId")
                or node["primary_kernel_version_id"]
            )
            if stage_type == "gen_kernel" and status == "completed" and out_ver_ids:
                primary_kernel = kernel_ids[0]

            chain_verified, chain_latency = self._evaluate_node_active_chain(
                conn, exploration_id, node_id
            )
            if stage_type == "verify_correctness" and chain_verified:
                conn.execute(
                    """
                    UPDATE references_table
                    SET reverified_in_attempt_id = ?,
                        reverified_status = 'verified'
                    WHERE exploration_id = ? AND target_node_id = ?
                    """,
                    (attempt_id, exploration_id, node_id),
                )

            baseline_ref = json.loads(exp["baseline_ref_json"])
            base_lat = float(baseline_ref.get("latencyUs") or 0)
            if chain_latency is not None and chain_latency > 0 and base_lat > 0:
                speedup = round(base_lat / chain_latency, 2)
            else:
                speedup = None

            pipeline_metrics = (
                json.dumps(payload["pipelineMetrics"], ensure_ascii=False)
                if payload.get("pipelineMetrics")
                else (
                    node["pipeline_metrics_json"] if chain_latency is not None else None
                )
            )
            node_exec_status = payload.get("nodeExecutionStatus")
            if not node_exec_status:
                if stage_type == "summarize" and status == "completed":
                    node_exec_status = "completed"
                elif status == "stopped":
                    node_exec_status = "stopped"
                else:
                    node_exec_status = node["execution_status"]

            node_content_status = (
                payload.get("nodeContentStatus") or node["content_status"]
            )
            if node_content_status == "cooled" and not (
                _internal_seed or node_id == "N0"
            ):
                raise ValidationError("禁止将非基线节点直接标记为 cooled")

            stop_or_fail_reason = payload.get(
                "nodeStopOrFailReason", node["stop_or_fail_reason"]
            )
            missing_reasons = json.loads(node["missing_stage_reasons_json"] or "{}")
            if payload.get("missingStageReasons"):
                missing_reasons.update(payload["missingStageReasons"])

            new_node_ver = int(node["version"]) + 1
            conn.execute(
                """
                UPDATE nodes
                SET primary_kernel_version_id = ?,
                    execution_status = ?,
                    content_status = ?,
                    current_stage = ?,
                    latency_us = ?,
                    speedup_vs_baseline = ?,
                    correctness_passed = ?,
                    stop_or_fail_reason = ?,
                    pipeline_metrics_json = ?,
                    missing_stage_reasons_json = ?,
                    version = ?,
                    updated_at = ?
                WHERE exploration_id = ? AND id = ?
                """,
                (
                    primary_kernel,
                    node_exec_status,
                    node_content_status,
                    stage_type,
                    chain_latency,
                    speedup,
                    1 if chain_verified else 0,
                    stop_or_fail_reason,
                    pipeline_metrics,
                    json.dumps(missing_reasons, ensure_ascii=False),
                    new_node_ver,
                    ended_at,
                    exploration_id,
                    node_id,
                ),
            )

            # Recompute best_valid_node_id strictly across nodes with active chain_verified == 1 and chain_latency IS NOT NULL
            best_row = conn.execute(
                """
                SELECT id, latency_us FROM nodes
                WHERE exploration_id = ? AND correctness_passed = 1 AND latency_us IS NOT NULL
                ORDER BY latency_us ASC
                LIMIT 1
                """,
                (exploration_id,),
            ).fetchone()
            best_valid_node_id = best_row["id"] if best_row else "N0"

            conn.execute(
                """
                UPDATE explorations
                SET budget_json = ?,
                    best_valid_node_id = ?,
                    version = version + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(budget, ensure_ascii=False),
                    best_valid_node_id,
                    ended_at,
                    exploration_id,
                ),
            )

            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="stage_attempt",
                entity_id=attempt_id,
                entity_version=new_att_ver,
                event_type="attempt.finished",
                payload={
                    "attemptId": attempt_id,
                    "nodeId": node_id,
                    "stageType": stage_type,
                    "code": att["code"],
                    "status": status,
                    "correctnessPassed": chain_verified,
                    "latencyUs": chain_latency,
                    "bestValidNodeId": best_valid_node_id,
                    "budget": budget,
                },
            )
            result = {
                "attemptId": attempt_id,
                "nodeId": node_id,
                "status": status,
                "bestValidNodeId": best_valid_node_id,
                "budget": budget,
                "event": evt,
            }
            self.storage.save_idempotency(
                conn,
                exploration_id,
                request_id,
                "finish_stage_attempt",
                payload_hash,
                result,
            )
            return result

    # ------------------------------------------------------------------
    # Knowledge & ready-for-cool Candidates
    # ------------------------------------------------------------------
    def upsert_knowledge_item(
        self,
        exploration_id: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
        *,
        _internal_seed: bool = False,
    ) -> Dict[str, Any]:
        self._reject_internal_flags(payload, _internal_seed)
        payload_hash = self.storage.compute_payload_hash(payload)
        item_id = payload["id"]
        content_status = payload.get("contentStatus", "hot")
        if content_status == "cooled" and not _internal_seed:
            raise ValidationError(
                "hot -> cooled 必须经过人工审批并合入远端主线；当前接口仅支持标记 hot 或 ready-for-cool"
            )

        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "upsert_knowledge", payload_hash
            )
            if cached is not None:
                return cached

            existing = conn.execute(
                "SELECT 1 FROM knowledge_items WHERE exploration_id=? AND id=?",
                (exploration_id, item_id),
            ).fetchone()
            if existing and not _internal_seed:
                linked = conn.execute(
                    "SELECT 1 FROM graph_relations WHERE exploration_id=? AND (json_extract(payload_json,'$.sourceId')=? OR json_extract(payload_json,'$.targetId')=?)",
                    (exploration_id, item_id, item_id),
                ).fetchone()
                legacy_link = conn.execute(
                    "SELECT 1 FROM references_table WHERE exploration_id=? AND referenced_knowledge_id=?",
                    (exploration_id, item_id),
                ).fetchone()
                if linked or legacy_link:
                    raise ConflictError(
                        "Referenced knowledge is immutable; create a new knowledge ID/version"
                    )

            now = utc_now_iso()
            conn.execute(
                """
                INSERT OR REPLACE INTO knowledge_items (
                    id, exploration_id, category, claim_type, title, version,
                    content_status, applicability, unverified_scope, body,
                    supporting_node_ids_json, counterexample_node_ids_json,
                    evidence_attempt_ids_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    exploration_id,
                    payload["category"],
                    payload["claimType"],
                    payload["title"],
                    payload["version"],
                    content_status,
                    payload["applicability"],
                    payload["unverifiedScope"],
                    payload["body"],
                    json.dumps(
                        payload.get("supportingNodeIds", []), ensure_ascii=False
                    ),
                    json.dumps(
                        payload.get("counterexampleNodeIds", []), ensure_ascii=False
                    ),
                    json.dumps(
                        payload.get("evidenceAttemptIds", []), ensure_ascii=False
                    ),
                    now,
                    now,
                ),
            )

            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="knowledge",
                entity_id=item_id,
                entity_version=1,
                event_type="knowledge.upserted",
                payload={
                    "knowledgeId": item_id,
                    "title": payload["title"],
                    "contentStatus": content_status,
                },
            )
            result = {
                "knowledgeId": item_id,
                "contentStatus": content_status,
                "event": evt,
            }
            self.storage.save_idempotency(
                conn,
                exploration_id,
                request_id,
                "upsert_knowledge",
                payload_hash,
                result,
            )
            return result

    def mark_ready_for_cool(
        self,
        exploration_id: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark hot nodes, resource versions, or knowledge items as ready-for-cool (never cooled!)."""
        payload_hash = self.storage.compute_payload_hash(payload)
        target_status = payload.get("contentStatus", "ready-for-cool")
        if target_status not in ("hot", "ready-for-cool"):
            raise ValidationError(
                "当前仅支持标记为 ready-for-cool 或恢复为 hot，不支持转换为 cooled"
            )

        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "mark_ready_for_cool", payload_hash
            )
            if cached is not None:
                return cached

            for nid in payload.get("nodeIds", []):
                conn.execute(
                    "UPDATE nodes SET content_status = ?, version = version + 1, updated_at = ? WHERE exploration_id = ? AND id = ? AND content_status != 'cooled'",
                    (target_status, utc_now_iso(), exploration_id, nid),
                )
            for vid in payload.get("resourceVersionIds", []):
                conn.execute(
                    "UPDATE resource_versions SET content_status = ? WHERE exploration_id = ? AND id = ? AND content_status != 'cooled'",
                    (target_status, exploration_id, vid),
                )
            for kid in payload.get("knowledgeIds", []):
                conn.execute(
                    "UPDATE knowledge_items SET content_status = ?, updated_at = ? WHERE exploration_id = ? AND id = ? AND content_status != 'cooled'",
                    (target_status, utc_now_iso(), exploration_id, kid),
                )

            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="exploration",
                entity_id=exploration_id,
                entity_version=1,
                event_type="candidate.marked",
                payload={
                    "contentStatus": target_status,
                    "nodeIds": payload.get("nodeIds", []),
                    "resourceVersionIds": payload.get("resourceVersionIds", []),
                    "knowledgeIds": payload.get("knowledgeIds", []),
                },
            )
            result = {"contentStatus": target_status, "event": evt}
            self.storage.save_idempotency(
                conn,
                exploration_id,
                request_id,
                "mark_ready_for_cool",
                payload_hash,
                result,
            )
            return result

    # ------------------------------------------------------------------
    # Coordinator Stop & Resume
    # ------------------------------------------------------------------
    def request_stop(
        self,
        exploration_id: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload_hash = self.storage.compute_payload_hash(payload)
        reason = payload.get("reason", "操作者主动请求停止探索")
        finalize_immediately = bool(payload.get("finalizeImmediately", False))

        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "request_stop", payload_hash
            )
            if cached is not None:
                return cached

            exp = conn.execute(
                "SELECT version FROM explorations WHERE id = ?", (exploration_id,)
            ).fetchone()
            if not exp:
                raise NotFoundError(f"Exploration '{exploration_id}' not found")

            running_cnt = conn.execute(
                "SELECT COUNT(*) AS cnt FROM stage_attempts WHERE exploration_id = ? AND status = 'running'",
                (exploration_id,),
            ).fetchone()["cnt"]

            if running_cnt and finalize_immediately:
                raise ConflictError(
                    "Cannot finalize stop while attempts are running; reconcile external work first"
                )
            new_status = "stopped" if running_cnt == 0 else "stopping"
            new_ver = int(exp["version"]) + 1
            conn.execute(
                """
                UPDATE explorations
                SET coordinator_status = ?,
                    stop_reason = ?,
                    version = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (new_status, reason, new_ver, utc_now_iso(), exploration_id),
            )

            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="exploration",
                entity_id=exploration_id,
                entity_version=new_ver,
                event_type="exploration.stopping",
                payload={
                    "coordinatorStatus": new_status,
                    "stopReason": reason,
                    "runningAttemptsCount": int(running_cnt),
                },
            )
            result = {
                "coordinatorStatus": new_status,
                "stopReason": reason,
                "runningAttemptsCount": int(running_cnt),
                "event": evt,
            }
            self.storage.save_idempotency(
                conn, exploration_id, request_id, "request_stop", payload_hash, result
            )
            return result

    def resume_exploration(
        self,
        exploration_id: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload_hash = self.storage.compute_payload_hash(payload)
        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "resume_exploration", payload_hash
            )
            if cached is not None:
                return cached

            exp = conn.execute(
                "SELECT version FROM explorations WHERE id = ?", (exploration_id,)
            ).fetchone()
            if not exp:
                raise NotFoundError(f"Exploration '{exploration_id}' not found")

            if (
                payload.get("expectedVersion") is not None
                and payload["expectedVersion"] != exp["version"]
            ):
                raise ConflictError(
                    "Project version changed; read context before resuming"
                )
            unresolved = conn.execute(
                "SELECT COUNT(*) FROM stage_attempts WHERE exploration_id = ? AND (status = 'running' OR reserved_eval_slot != 0)",
                (exploration_id,),
            ).fetchone()[0]
            if unresolved:
                raise ConflictError(
                    "Reconcile in-flight attempts and evaluation reservations before resuming with any session"
                )

            new_ver = int(exp["version"]) + 1
            conn.execute(
                """
                UPDATE explorations
                SET coordinator_status = 'running',
                    stop_reason = ?,
                    version = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.get("note", "显式恢复探索（保留全部历史尝试与预算记录）"),
                    new_ver,
                    utc_now_iso(),
                    exploration_id,
                ),
            )
            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="exploration",
                entity_id=exploration_id,
                entity_version=new_ver,
                event_type="exploration.resumed",
                payload={"coordinatorStatus": "running"},
            )
            result = {"coordinatorStatus": "running", "version": new_ver, "event": evt}
            self.storage.save_idempotency(
                conn,
                exploration_id,
                request_id,
                "resume_exploration",
                payload_hash,
                result,
            )
            return result

    def create_graph_relation(self, exploration_id, payload, request_id=None):
        from .graph import validate_relation

        payload_hash = self.storage.compute_payload_hash(payload)
        with self.storage.write_tx() as conn:
            cached = self.storage.check_idempotency(
                conn, exploration_id, request_id, "graph_relation", payload_hash
            )
            if cached is not None:
                return cached
            if not isinstance(payload.get("id"), str) or not payload["id"]:
                raise ValidationError("Relation id required")
            validate_relation(conn, exploration_id, payload)
            if conn.execute(
                "SELECT 1 FROM graph_relations WHERE exploration_id=? AND id=?",
                (exploration_id, payload["id"]),
            ).fetchone():
                raise ConflictError("Relation is immutable; use a new relation ID")
            conn.execute(
                "INSERT INTO graph_relations VALUES (?,?,?)",
                (
                    payload["id"],
                    exploration_id,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            evt = self.storage.append_event(
                conn,
                exploration_id=exploration_id,
                entity_type="relation",
                entity_id=payload["id"],
                entity_version=1,
                event_type="relation.created",
                payload=payload,
            )
            result = {"relationId": payload["id"], "event": evt}
            self.storage.save_idempotency(
                conn, exploration_id, request_id, "graph_relation", payload_hash, result
            )
            return result
