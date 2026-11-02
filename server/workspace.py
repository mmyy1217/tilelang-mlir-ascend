"""Durable session handoffs and consistent, executable project forks."""

import base64
import json
import re
import sqlite3
import uuid
from .db import ConflictError, NotFoundError, ValidationError, utc_now_iso

TABLES = (
    "agents",
    "nodes",
    "stage_attempts",
    "resources",
    "resource_versions",
    "resource_links",
    "references_table",
    "knowledge_items",
    "graph_relations",
    "handoffs",
)


class Workspace:
    def __init__(self, storage):
        self.db = storage
        with storage.write_tx() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, exploration_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS handoffs(id TEXT NOT NULL, exploration_id TEXT NOT NULL, session_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(exploration_id,id));
            CREATE TABLE IF NOT EXISTS project_metadata(exploration_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
            """)

    def mutate(self, exp, endpoint, payload, request, action):
        if not request:
            raise ValidationError("X-Request-Id is required")
        digest = self.db.compute_payload_hash(payload)
        with self.db.write_tx() as c:
            old = self.db.check_idempotency(c, exp, request, endpoint, digest)
            if old is not None:
                return old
            row = c.execute("SELECT * FROM explorations WHERE id=?", (exp,)).fetchone()
            if not row:
                raise NotFoundError("Project not found")
            result = action(c, dict(row))
            event = self.db.append_event(
                c,
                exploration_id=exp,
                entity_type=endpoint,
                entity_id=result.get("id", exp),
                entity_version=1,
                event_type=endpoint + ".saved",
                payload=dict(result),
            )
            result["event"] = event
            self.db.save_idempotency(c, exp, request, endpoint, digest, result)
            return result

    def checkpoint(self, exp, payload, request):
        def create(c, row):
            identity = "cp-" + uuid.uuid4().hex
            tables = {
                table: [
                    dict(r)
                    for r in c.execute(
                        f"SELECT * FROM {table} WHERE exploration_id=?", (exp,)
                    )
                ]
                for table in TABLES
            }
            metadata = c.execute(
                "SELECT payload_json FROM project_metadata WHERE exploration_id=?",
                (exp,),
            ).fetchone()
            seq = c.execute(
                "SELECT COALESCE(MAX(seq),0) FROM events WHERE exploration_id=?", (exp,)
            ).fetchone()[0]
            data = dict(
                project=row,
                tables=tables,
                eventSeq=seq,
                metadata=json.loads(metadata[0]) if metadata else {},
            )
            c.execute(
                "INSERT INTO checkpoints VALUES (?,?,?,?)",
                (identity, exp, json.dumps(data), utc_now_iso()),
            )
            return {"id": identity, "checkpointId": identity, "observedEventSeq": seq}

        return self.mutate(exp, "checkpoint", payload, request, create)

    def fork(self, exp, payload, request):
        def create(c, _):
            cp = c.execute(
                "SELECT payload_json FROM checkpoints WHERE exploration_id=? AND id=?",
                (exp, payload.get("checkpointId")),
            ).fetchone()
            if not cp:
                raise NotFoundError("Checkpoint not found in source project")
            data = json.loads(cp[0])
            new = "exp-" + uuid.uuid4().hex
            now = utc_now_iso()
            row = data["project"]
            row.update(
                id=new,
                title=payload.get("name") or row["title"] + " fork",
                coordinator_status="stopped",
                stop_reason="Fork: waiting for a new session",
                version=1,
                created_at=now,
                updated_at=now,
            )
            budget = json.loads(row["budget_json"])
            budget["reservedEvaluations"] = 0
            row["budget_json"] = json.dumps(budget)
            self.insert(c, "explorations", row)
            for table in TABLES:
                for entry in data["tables"].get(table, []):
                    entry["exploration_id"] = new
                    if table == "agents":
                        entry.update(
                            status="stopped", current_node_id=None, current_stage=None
                        )
                    if table == "nodes" and entry["execution_status"] == "running":
                        entry["execution_status"] = "stopped"
                    if table == "stage_attempts":
                        entry["reserved_eval_slot"] = 0
                        if entry["status"] in ("running", "pending"):
                            entry.update(
                                status="stopped",
                                ended_at=now,
                                failure_or_skip_reason="Fork snapshot: source work is not running in this copy",
                            )
                    self.insert(c, table, entry)
                    if table == "agents":
                        c.execute(
                            "INSERT INTO closed_sessions VALUES (?,?)",
                            (new, entry["id"]),
                        )
            metadata = data.get("metadata", {})
            metadata["forkSource"] = {
                "explorationId": exp,
                "checkpointId": payload["checkpointId"],
                "eventSeq": data["eventSeq"],
                "capturedAt": now,
            }
            c.execute(
                "INSERT INTO project_metadata VALUES (?,?)", (new, json.dumps(metadata))
            )
            self.db.append_event(
                c,
                exploration_id=new,
                entity_type="exploration",
                entity_id=new,
                entity_version=1,
                event_type="project.forked",
                payload=metadata["forkSource"],
            )
            return {"id": new, "projectId": new, "coordinatorStatus": "stopped"}

        return self.mutate(exp, "fork", payload, request, create)

    @staticmethod
    def insert(c, table, row):
        keys = list(row)
        c.execute(
            f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
            [row[k] for k in keys],
        )

    def handoff(self, exp, payload, request):
        def create(c, _):
            sid = payload.get("sessionId")
            identity = payload.get("id") or "handoff-" + uuid.uuid4().hex
            if not c.execute(
                "SELECT 1 FROM agents WHERE exploration_id=? AND id=?", (exp, sid)
            ).fetchone():
                raise NotFoundError("Session not registered")
            if not isinstance(payload.get("summary"), str):
                raise ValidationError("summary required")
            for rid in payload.get("evidenceVersions", []):
                if not c.execute(
                    "SELECT 1 FROM resource_versions WHERE exploration_id=? AND id=?",
                    (exp, rid),
                ).fetchone():
                    raise NotFoundError("Evidence version missing")
            saved = {**payload, "id": identity, "createdAt": utc_now_iso()}
            c.execute(
                "INSERT INTO handoffs VALUES (?,?,?,?,?)",
                (identity, exp, sid, json.dumps(saved), saved["createdAt"]),
            )
            return {"id": identity, "handoffId": identity}

        return self.mutate(exp, "handoff", payload, request, create)

    def close(self, exp, payload, request):
        def close(c, _):
            sid = payload.get("sessionId")
            if not c.execute(
                "SELECT 1 FROM handoffs WHERE exploration_id=? AND id=? AND session_id=?",
                (exp, payload.get("handoffId"), sid),
            ).fetchone():
                raise NotFoundError("Session handoff required")
            if c.execute(
                "SELECT 1 FROM stage_attempts WHERE exploration_id=? AND agent_id=? AND (status='running' OR reserved_eval_slot!=0)",
                (exp, sid),
            ).fetchone():
                raise ConflictError(
                    "Unresolved in-flight work; reconcile before closing session"
                )
            c.execute("INSERT INTO closed_sessions VALUES (?,?)", (exp, sid))
            c.execute(
                "UPDATE agents SET status='stopped',current_node_id=NULL,current_stage=NULL WHERE exploration_id=? AND id=?",
                (exp, sid),
            )
            return {"id": sid, "sessionId": sid, "status": "stopped"}

        return self.mutate(exp, "session_close", payload, request, close)

    def context(self, exp):
        snap = self.db.get_snapshot(exp)
        in_flight = [
            {**a, "nodeId": n["id"]}
            for n in snap["nodes"]
            for s in n["stages"].values()
            for a in s["attempts"]
            if a["status"] == "running"
        ]
        return {
            key: snap[key]
            for key in (
                "id",
                "title",
                "operatorName",
                "targetGoal",
                "inputDomain",
                "environment",
                "budget",
                "coordinatorStatus",
                "bestValidNodeId",
                "stopReason",
                "lastEventSeq",
                "version",
            )
        } | {
            "nodeCount": len(snap["nodes"]),
            "nodes": [
                {
                    "id": n["id"],
                    "title": n["title"],
                    "parentIds": n["parentIds"],
                    "status": n["executionStatus"],
                }
                for n in snap["nodes"][-20:]
            ],
            "nodesTruncated": len(snap["nodes"]) > 20,
            "inFlight": in_flight,
            "latestHandoff": snap.get("handoffs", [])[-1]
            if snap.get("handoffs")
            else None,
            "metadata": snap.get("workspaceMetadata", {}),
            "unfinishedDirections": snap["unfinishedDirections"],
        }

    def import_snapshot(self, payload, request):
        """Import a portable snapshot atomically; imported claims require revalidation."""
        from .db import STAGE_TYPES
        from .graph import project_relations, validate_relation

        if not request:
            raise ValidationError("X-Request-Id is required")
        snapshot = payload.get("exploration", payload)
        if not isinstance(snapshot, dict) or not all(
            k in snapshot for k in ("nodes", "resources", "operatorName", "budget")
        ):
            raise ValidationError("Expected a workbench snapshot export")
        digest = self.db.compute_payload_hash(payload)
        now = utc_now_iso()
        new = "exp-" + uuid.uuid4().hex
        try:
            with self.db.write_tx() as c:
                old = self.db.check_idempotency(
                    c, "_imports", request, "import", digest
                )
                if old is not None:
                    return old

                def row(table, obj, **overrides):
                    # Column names come exclusively from the application-owned schema.
                    record = {}
                    for col in c.execute(f"PRAGMA table_info({table})"):
                        key = col["name"]
                        base = key.removesuffix("_json")
                        camel = re.sub(r"_([a-z])", lambda m: m[1].upper(), base)
                        if camel in obj:
                            value = obj[camel]
                            record[key] = (
                                json.dumps(value) if key.endswith("_json") else value
                            )
                    record.update(overrides)
                    record["exploration_id"] = new
                    columns = {
                        x["name"] for x in c.execute(f"PRAGMA table_info({table})")
                    }
                    record = {k: v for k, v in record.items() if k in columns}
                    for key in ("created_at", "updated_at"):
                        if key in columns:
                            record.setdefault(key, now)
                    self.insert(c, table, record)

                budget = {**snapshot["budget"], "reservedEvaluations": 0}
                row(
                    "explorations",
                    snapshot,
                    id=new,
                    title=snapshot.get("title", snapshot["operatorName"]),
                    target_goal=snapshot.get("targetGoal", ""),
                    input_domain=snapshot.get("inputDomain", ""),
                    environment=snapshot.get("environment", ""),
                    baseline_ref_json=json.dumps(snapshot.get("baselineRef", {})),
                    budget_json=json.dumps(budget),
                    coordinator_status="stopped",
                    stop_reason="Imported snapshot: verify results before continuing",
                    best_valid_node_id="",
                    version=1,
                )
                nodes = snapshot["nodes"]
                node_ids = {n["id"] for n in nodes}
                agents = snapshot.get("agents", [])
                agent_ids = {a["id"] for a in agents}
                attempts = [
                    {**a, "nodeId": n["id"], "stageType": st}
                    for n in nodes
                    for st, v in n.get("stages", {}).items()
                    for a in v["attempts"]
                ]
                attempt_ids = {a["id"] for a in attempts}
                versions = set(snapshot["resources"])
                if len(node_ids) != len(nodes) or len(attempt_ids) != len(attempts):
                    raise ValidationError("Duplicate node/attempt IDs")
                for agent in agents:
                    row(
                        "agents",
                        agent,
                        status="stopped",
                        last_heartbeat=agent.get("lastHeartbeat", now),
                        current_node_id=None,
                        current_stage=None,
                    )
                    c.execute(
                        "INSERT INTO closed_sessions VALUES (?,?)", (new, agent["id"])
                    )
                for n in nodes:
                    parents = n.get(
                        "parentIds", [n["parentId"]] if n.get("parentId") else []
                    )
                    if any(p not in node_ids or p == n["id"] for p in parents):
                        raise ValidationError("Invalid parent")
                    if (
                        n.get("primaryKernelVersionId")
                        and n["primaryKernelVersionId"] not in versions
                    ):
                        raise ValidationError("Missing primary kernel")
                    row(
                        "nodes",
                        n,
                        parent_id=None,
                        depth=0,
                        branch_label=n.get("branchLabel", ""),
                        hypothesis=n.get("hypothesis", ""),
                        change_summary=n.get("changeSummary", ""),
                        execution_status="stopped"
                        if n.get("executionStatus") in ("pending", "running")
                        else n.get("executionStatus", "stopped"),
                        correctness_passed=0,
                        speedup_vs_baseline=None,
                    )
                resources = set()
                for identity, r in snapshot["resources"].items():
                    if identity != r["id"]:
                        raise ValidationError("Resource ID mismatch")
                    owner = r.get("ownerNodeId", "")
                    if owner and owner not in node_ids:
                        raise ValidationError("Missing resource owner")
                    rid = r.get("resourceId", identity)
                    if rid not in resources:
                        row("resources", r, id=rid, owner_node_id=owner)
                        resources.add(rid)
                    missing = r.get("missingReason")
                    if missing:
                        sha = r.get("sha256", "")
                        blob = None
                        size = 0
                    else:
                        if r.get("isBinary") and not r.get("binaryBase64"):
                            raise ValidationError("Binary bytes missing from export")
                        raw = (
                            base64.b64decode(r["binaryBase64"], validate=True)
                            if r.get("isBinary")
                            else r.get("content", "").encode("utf-8")
                        )
                        sha, blob, size = self.db.write_verified_blob(
                            raw, r.get("sha256")
                        )
                    row(
                        "resource_versions",
                        r,
                        resource_id=rid,
                        owner_node_id=owner,
                        sha256=sha,
                        blob_rel_path=blob,
                        size_bytes=size,
                        preview_content=r.get("content", ""),
                        is_binary=int(r.get("isBinary", False)),
                    )
                for index, a in enumerate(attempts):
                    if (
                        a["stageType"] not in STAGE_TYPES
                        or a["agentId"] not in agent_ids
                    ):
                        raise ValidationError("Unknown stage/session")
                    if any(
                        x not in attempt_ids for x in a.get("dependsOnAttemptIds", [])
                    ):
                        raise ValidationError("Missing attempt dependency")
                    if any(
                        x not in versions
                        for x in a.get("inputResourceVersionIds", [])
                        + a.get("outputResourceVersionIds", [])
                    ):
                        raise ValidationError("Missing resource version")
                    stopped = a["status"] in ("pending", "running")
                    row(
                        "stage_attempts",
                        a,
                        status="stopped" if stopped else a["status"],
                        ended_at=now if stopped else a.get("endedAt"),
                        failure_or_skip_reason="Imported in-flight work is historical"
                        if stopped
                        else a.get("failureOrSkipReason"),
                        reserved_eval_slot=0,
                        seq_order=index,
                        correctness_passed=None,
                    )
                for link in snapshot.get("resourceLinks", []):
                    if (
                        link["nodeId"] not in node_ids
                        or link["resourceVersionId"] not in versions
                        or (
                            link.get("attemptId")
                            and link["attemptId"] not in attempt_ids
                        )
                    ):
                        raise ValidationError("Invalid resource link")
                    row("resource_links", link)
                knowledge = snapshot.get("knowledgeItems", [])
                for k in knowledge:
                    row(
                        "knowledge_items",
                        k,
                        supporting_node_ids_json="[]",
                        counterexample_node_ids_json="[]",
                        evidence_attempt_ids_json="[]",
                    )
                edges = project_relations(
                    nodes,
                    snapshot.get("references", []),
                    knowledge,
                    snapshot.get("graphRelations", []),
                )
                for edge in edges:
                    validate_relation(c, new, edge)
                    self.insert(
                        c,
                        "graph_relations",
                        dict(
                            id=edge["id"],
                            exploration_id=new,
                            payload_json=json.dumps(edge),
                        ),
                    )
                for handoff in snapshot.get("handoffs", []):
                    if handoff.get("sessionId") not in agent_ids or any(
                        x not in versions for x in handoff.get("evidenceVersions", [])
                    ):
                        raise ValidationError("Invalid imported handoff")
                    c.execute(
                        "INSERT INTO handoffs VALUES (?,?,?,?,?)",
                        (
                            handoff["id"],
                            new,
                            handoff["sessionId"],
                            json.dumps(handoff),
                            handoff.get("createdAt", now),
                        ),
                    )
                metadata = {
                    "sourceMetadata": snapshot.get("workspaceMetadata", {}),
                    "importSource": {
                        "id": snapshot.get("id"),
                        "eventSeq": snapshot.get("lastEventSeq"),
                        "importedAt": now,
                    },
                    "validation": "Imported records are historical claims; repeat verification before ranking results.",
                    "claimedBestNodeId": snapshot.get("bestValidNodeId"),
                }
                c.execute(
                    "INSERT INTO project_metadata VALUES (?,?)",
                    (new, json.dumps(metadata)),
                )
                event = self.db.append_event(
                    c,
                    exploration_id=new,
                    entity_type="exploration",
                    entity_id=new,
                    entity_version=1,
                    event_type="project.imported",
                    payload=metadata,
                )
                result = {
                    "projectId": new,
                    "coordinatorStatus": "stopped",
                    "event": event,
                }
                self.db.save_idempotency(
                    c, "_imports", request, "import", digest, result
                )
                return result
        except (KeyError, TypeError, ValueError, sqlite3.IntegrityError) as error:
            raise ValidationError(f"Invalid snapshot: {error}") from error
