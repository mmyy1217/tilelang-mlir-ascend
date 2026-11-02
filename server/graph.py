"""Typed provenance graph, including a lossless projection of legacy tree records."""

from .db import ValidationError, NotFoundError

KINDS = {"derivation", "reference", "support", "refute"}


def project_relations(nodes, references, knowledge, stored):
    result = list(stored)

    def add(edge):
        signature = (
            edge["kind"],
            edge["sourceType"],
            edge["sourceId"],
            edge["targetType"],
            edge["targetId"],
        )
        if not any(
            (r["kind"], r["sourceType"], r["sourceId"], r["targetType"], r["targetId"])
            == signature
            for r in result
        ):
            result.append(edge)

    for r in references:
        add(
            dict(
                id=r["id"],
                kind="derivation" if r["purpose"] == "derivation" else "reference",
                sourceType="node",
                sourceId=r["sourceNodeId"],
                targetType="node",
                targetId=r["targetNodeId"],
                rationale=r.get("rationale", ""),
                resourceVersionId=r.get("referencedResourceVersionId"),
                knowledgeVersion=r.get("referencedKnowledgeVersion"),
            )
        )
        if r.get("referencedKnowledgeId") and any(
            k["id"] == r["referencedKnowledgeId"] for k in knowledge
        ):
            add(
                dict(
                    id="knowledge:" + r["id"],
                    kind="reference",
                    sourceType="knowledge",
                    sourceId=r["referencedKnowledgeId"],
                    targetType="node",
                    targetId=r["targetNodeId"],
                    rationale=r.get("rationale", ""),
                    knowledgeVersion=r.get("referencedKnowledgeVersion"),
                )
            )
    for n in nodes:
        for parent in n.get(
            "parentIds", ([n["parentId"]] if n.get("parentId") else [])
        ):
            add(
                dict(
                    id=f"legacy:{parent}:{n['id']}",
                    kind="derivation",
                    sourceType="node",
                    sourceId=parent,
                    targetType="node",
                    targetId=n["id"],
                    rationale=n.get("derivationNote", ""),
                )
            )
    for k in knowledge:
        for field, kind in [
            ("supportingNodeIds", "support"),
            ("counterexampleNodeIds", "refute"),
        ]:
            for node in k.get(field, []):
                add(
                    dict(
                        id=f"{kind}:{node}:{k['id']}",
                        kind=kind,
                        sourceType="node",
                        sourceId=node,
                        targetType="knowledge",
                        targetId=k["id"],
                        rationale="既有知识条目中的证据关联；不自动证明因果",
                        knowledgeVersion=k["version"],
                        evidenceAttemptIds=k.get("evidenceAttemptIds", []),
                    )
                )
    return result


def validate_relation(conn, exp_id, edge):
    kind = edge.get("kind")
    if kind not in KINDS:
        raise ValidationError("Unknown graph relation kind")
    for end in ["source", "target"]:
        typ, identity = edge.get(end + "Type"), edge.get(end + "Id")
        if typ not in ("node", "knowledge") or not isinstance(identity, str):
            raise ValidationError("Graph endpoints require node/knowledge type and ID")
        table = "nodes" if typ == "node" else "knowledge_items"
        row = conn.execute(
            f"SELECT * FROM {table} WHERE exploration_id=? AND id=?", (exp_id, identity)
        ).fetchone()
        if not row:
            raise NotFoundError(f"{end} endpoint not found in this project")
        if typ == "knowledge" and edge.get("knowledgeVersion") != row["version"]:
            raise ValidationError(
                "Knowledge endpoint requires its exact knowledgeVersion"
            )
    if (edge["sourceType"], edge["sourceId"]) == (edge["targetType"], edge["targetId"]):
        raise ValidationError("Self relation is not allowed")
    if not isinstance(edge.get("rationale"), str) or not edge["rationale"].strip():
        raise ValidationError("Describe the contribution or evidence in rationale")
    if kind == "derivation" and (edge["sourceType"], edge["targetType"]) != (
        "node",
        "node",
    ):
        raise ValidationError("Derivation connects solution nodes only")
    if kind in ("support", "refute") and (edge["sourceType"], edge["targetType"]) != (
        "node",
        "knowledge",
    ):
        raise ValidationError("Evidence connects a solution node to a knowledge claim")
    if edge.get("resourceVersionId"):
        if not conn.execute(
            "SELECT 1 FROM resource_versions WHERE exploration_id=? AND id=?",
            (exp_id, edge["resourceVersionId"]),
        ).fetchone():
            raise NotFoundError("Referenced resource version does not exist")
    attempts = edge.get("evidenceAttemptIds", [])
    if not isinstance(attempts, list) or not all(isinstance(a, str) for a in attempts):
        raise ValidationError("evidenceAttemptIds must be an array of IDs")
    if kind in ("support", "refute") and not attempts:
        raise ValidationError("Support/refute requires explicit evidenceAttemptIds")
    for attempt in attempts:
        row = conn.execute(
            "SELECT node_id,status FROM stage_attempts WHERE exploration_id=? AND id=?",
            (exp_id, attempt),
        ).fetchone()
        if (
            not row
            or row["node_id"] != edge["sourceId"]
            or row["status"] not in ("completed", "failed")
        ):
            raise ValidationError(
                "Evidence must be a finished attempt of the source node"
            )
    if kind == "derivation":
        import json

        links = [
            (r["parent_id"], r["id"])
            for r in conn.execute(
                "SELECT id,parent_id FROM nodes WHERE exploration_id=?", (exp_id,)
            )
            if r["parent_id"]
        ]
        links += [
            (r["source_node_id"], r["target_node_id"])
            for r in conn.execute(
                "SELECT * FROM references_table WHERE exploration_id=? AND purpose='derivation'",
                (exp_id,),
            )
        ]
        for row in conn.execute(
            "SELECT payload_json FROM graph_relations WHERE exploration_id=?", (exp_id,)
        ):
            r = json.loads(row[0])
            if r["kind"] == "derivation":
                links.append((r["sourceId"], r["targetId"]))
        pending = [edge["targetId"]]
        seen = set()
        while pending:
            current = pending.pop()
            if current == edge["sourceId"]:
                raise ValidationError("Derivation cycle detected")
            if current in seen:
                continue
            seen.add(current)
            pending.extend(target for source, target in links if source == current)
