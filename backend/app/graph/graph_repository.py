"""Graph persistence primitives: one interface, a Neo4j and an in-memory store.

Both implementations share the same semantics so the query service and the
sync service are tested offline, and a live parity test can compare them.

Write semantics (idempotent upserts keyed by ``id``):

* scalar properties: latest non-null value wins;
* list properties (``LIST_PROPS``): set-union, order preserved, never shrinks;
* ``verification_status``: the strongest status ever seen is kept;
* relationships: ``claim_ids`` / ``evidence_ids`` / ``source_ids`` /
  ``source_record_ids`` / ``research_job_ids`` are unioned, ``assertion``
  becomes ``direct`` once any direct claim supports it, ``has_conflict`` is
  sticky, ``first_observed_at``/``last_observed_at`` widen.

Cypher text is assembled only from ``Label`` / ``Rel`` enum values and fixed
fragments; every value is a parameter. Read methods run in read transactions.
"""

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal, Protocol

from app.graph.graph_schema import (
    BASE_LABEL,
    CONSTRAINTS,
    INDEXES,
    SCHEMA_VERSION,
    Label,
    Rel,
)
from app.graph.models import (
    STATUS_RANK,
    ClaimNode,
    GraphEdge,
    GraphEntity,
    GraphNode,
    RelationshipRow,
    SourceNode,
    stronger,
)
from app.schemas.research import utc_now

Direction = Literal["out", "in", "both"]
NODE_LIST_PROPS = (
    "aliases",
    "original_names",
    "source_record_ids",
    "source_urls",
    "research_job_ids",
    "content_hashes",
)
EDGE_LIST_PROPS = (
    "claim_ids",
    "evidence_ids",
    "source_ids",
    "source_record_ids",
    "research_job_ids",
)
MAX_DEPTH = 5
_LABEL_VALUES = frozenset(lab.value for lab in Label)


# -- row building (shared by both stores) --------------------------------------------


def _scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str)
    return value


def _clean(props: dict[str, Any]) -> dict[str, Any]:
    return {k: _scalar(v) for k, v in props.items() if v is not None and v != []}


def entity_row(e: GraphEntity) -> dict[str, Any]:
    props = {
        "canonical_name": e.canonical_name,
        "normalized_name": e.normalized_name,
        "match_key": e.match_key,
        "scope_domain": e.scope_domain,
        "confidence": e.confidence,
        "derived_from_claim": e.derived_from_claim,
        **e.properties,
    }
    if e.label is Label.KEYWORD:
        props.update(keyword=e.canonical_name, normalized_keyword=e.normalized_name)
    if e.source_record_ids:
        props["source_record_id"] = e.source_record_ids[0]
    lists = {
        "aliases": e.aliases,
        "original_names": e.original_names,
        "source_record_ids": e.source_record_ids,
        "source_urls": e.source_urls[:50],
        "research_job_ids": e.research_job_ids,
    }
    return {
        "id": e.id,
        "props": _clean(props),
        "lists": lists,
        "status": e.verification_status,
    }


def source_row(s: SourceNode) -> dict[str, Any]:
    props = s.model_dump(exclude={"id", "source_record_ids", "research_job_ids"})
    lists = {
        "source_record_ids": s.source_record_ids,
        "research_job_ids": s.research_job_ids,
        "content_hashes": [s.content_hash] if s.content_hash else [],
    }
    return {"id": s.id, "props": _clean(props), "lists": lists, "status": None}


def claim_row(c: ClaimNode) -> dict[str, Any]:
    props = c.model_dump(exclude={"id", "verification_status", "notes"})
    props["notes"] = c.notes[:10]
    return {
        "id": c.id,
        "props": _clean(props),
        "lists": {"research_job_ids": [c.research_job_id]},
        "status": c.verification_status,
    }


def edge_row(r: RelationshipRow) -> dict[str, Any]:
    props = {
        "assertion": r.assertion.value,
        "extraction_method": r.extraction_method,
        "basis": r.basis,
        "role": r.role,
        "excerpt_verified": r.excerpt_verified,
    }
    return {
        "source_id": r.source_id,
        "target_id": r.target_id,
        "props": _clean(props),
        "lists": {
            "claim_ids": r.claim_ids,
            "evidence_ids": r.evidence_ids,
            "source_ids": r.source_ids,
            "source_record_ids": r.source_record_ids,
            "research_job_ids": [r.research_job_id] if r.research_job_id else [],
        },
        "status": r.verification_status,
        "confidence": r.confidence,
        "observed_at": _scalar(r.observed_at),
        "has_conflict": r.verification_status == "conflicting",
        "direct": r.assertion.value == "direct",
        "role": r.role,
    }


class GraphRepository(Protocol):
    async def initialize_schema(self) -> dict[str, Any]: ...
    async def verify_schema(self) -> dict[str, Any]: ...
    async def upsert_nodes(
        self, label: Label, rows: list[dict[str, Any]]
    ) -> tuple[int, int]: ...
    async def upsert_edges(
        self, rel: Rel, source: Label, target: Label, rows: list[dict[str, Any]]
    ) -> tuple[int, int, int]: ...
    async def get_node(self, node_id: str) -> GraphNode | None: ...
    async def get_nodes(self, node_ids: list[str]) -> list[GraphNode]: ...
    async def find_nodes(
        self,
        label: Label,
        *,
        name: str | None = None,
        domain: str | None = None,
        limit: int = 20,
    ) -> list[GraphNode]: ...
    async def edges(
        self,
        node_id: str,
        *,
        direction: Direction = "both",
        rel_types: Iterable[Rel] | None = None,
        neighbor_labels: Iterable[Label] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[tuple[GraphEdge, GraphNode]]: ...
    async def neighborhood(
        self,
        node_id: str,
        *,
        depth: int,
        rel_types: Iterable[Rel] | None = None,
        limit: int = 100,
    ) -> tuple[list[GraphNode], list[GraphEdge]]: ...
    async def degree(self, node_id: str) -> dict[str, int]: ...
    async def count_job_claims(self, job_id: str) -> int: ...
    async def delete_nodes(self, node_ids: list[str]) -> int: ...


# -- in-memory store --------------------------------------------------------------------


class InMemoryGraphRepository:
    """Deterministic, dependency-free store with the Neo4j repository's semantics."""

    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges_by_key: dict[tuple[str, str, str], GraphEdge] = {}
        self.schema_initialized = 0

    async def initialize_schema(self) -> dict[str, Any]:
        self.schema_initialized += 1
        return {
            "constraints": [n for n, _ in CONSTRAINTS],
            "indexes": [n for n, _ in INDEXES],
            "version": SCHEMA_VERSION,
        }

    async def verify_schema(self) -> dict[str, Any]:
        ok = self.schema_initialized > 0
        return {"version": SCHEMA_VERSION if ok else None, "missing_constraints": [] if ok else [n for n, _ in CONSTRAINTS],
                "missing_indexes": [] if ok else [n for n, _ in INDEXES]}  # fmt: skip

    async def upsert_nodes(
        self, label: Label, rows: list[dict[str, Any]]
    ) -> tuple[int, int]:
        now = utc_now().isoformat()
        created = updated = 0
        for row in rows:
            node = self.nodes.get(row["id"])
            if node is None:
                node = GraphNode(
                    id=row["id"],
                    label=label,
                    properties={"id": row["id"], "created_at": now},
                )
                self.nodes[row["id"]] = node
                created += 1
            else:
                updated += 1
            p = node.properties
            p.update(row["props"])
            for key, values in row["lists"].items():
                cur = list(p.get(key, []))
                cur.extend(v for v in values if v not in cur)
                if cur:
                    p[key] = cur
            if row.get("status"):
                p["verification_status"] = (
                    stronger(p.get("verification_status"), row["status"])
                    if "verification_status" in p
                    else row["status"]
                )
            p["updated_at"] = now
        return created, updated

    async def upsert_edges(
        self, rel: Rel, source: Label, target: Label, rows: list[dict[str, Any]]
    ) -> tuple[int, int, int]:
        now = utc_now().isoformat()
        created = updated = missing = 0
        for row in rows:
            a, b = self.nodes.get(row["source_id"]), self.nodes.get(row["target_id"])
            if a is None or b is None or a.label is not source or b.label is not target:
                missing += 1
                continue
            key = (
                (rel.value, a.id, b.id)
                if rel is not Rel.MENTIONS
                else (rel.value, a.id, f"{b.id}|{row.get('role')}")
            )
            edge = self.edges_by_key.get(key)
            if edge is None:
                edge = GraphEdge(type=rel, source_id=a.id, target_id=b.id,
                                 properties={"created_at": now, "first_observed_at": row.get("observed_at")})  # fmt: skip
                self.edges_by_key[key] = edge
                created += 1
            else:
                updated += 1
            p = edge.properties
            direct = p.get("assertion") == "direct" or row["direct"]
            p.update(row["props"])
            p["assertion"] = (
                "direct" if direct else row["props"].get("assertion", "inferred")
            )
            for k, values in row["lists"].items():
                cur = list(p.get(k, []))
                cur.extend(v for v in values if v not in cur)
                p[k] = cur
            p["verification_status"] = (
                stronger(p.get("verification_status"), row["status"])
                if "verification_status" in p
                else row["status"]
            )
            p["has_conflict"] = bool(p.get("has_conflict")) or row["has_conflict"]
            if row.get("confidence") is not None and (
                p.get("confidence") is None or row["confidence"] > p["confidence"]
            ):
                p["confidence"] = row["confidence"]
            obs = row.get("observed_at")
            if obs and (
                p.get("last_observed_at") is None or obs > p["last_observed_at"]
            ):
                p["last_observed_at"] = obs
            if obs and (
                p.get("first_observed_at") is None or obs < p["first_observed_at"]
            ):
                p["first_observed_at"] = obs
            p["updated_at"] = now
        return created, updated, missing

    async def get_node(self, node_id: str) -> GraphNode | None:
        node = self.nodes.get(node_id)
        return node.model_copy(deep=True) if node else None

    async def get_nodes(self, node_ids: list[str]) -> list[GraphNode]:
        return [
            self.nodes[i].model_copy(deep=True) for i in node_ids if i in self.nodes
        ]

    async def find_nodes(
        self,
        label: Label,
        *,
        name: str | None = None,
        domain: str | None = None,
        limit: int = 20,
    ) -> list[GraphNode]:
        out = []
        for n in sorted(self.nodes.values(), key=lambda n: n.id):
            if n.label is not label:
                continue
            if name and name not in str(n.properties.get("normalized_name", "")):
                continue
            if domain and n.properties.get("official_domain") != domain:
                continue
            out.append(n.model_copy(deep=True))
        return out[:limit]

    async def edges(
        self,
        node_id,
        *,
        direction="both",
        rel_types=None,
        neighbor_labels=None,
        limit=100,
        offset=0,
    ):
        types = {r.value for r in rel_types} if rel_types else None
        labels = set(neighbor_labels) if neighbor_labels else None
        found = []
        for e in self.edges_by_key.values():
            if types and e.type.value not in types:
                continue
            if e.source_id == node_id and direction in ("out", "both"):
                other = e.target_id
            elif e.target_id == node_id and direction in ("in", "both"):
                other = e.source_id
            else:
                continue
            node = self.nodes[other]
            if labels and node.label not in labels:
                continue
            found.append((e.model_copy(deep=True), node.model_copy(deep=True)))
        found.sort(key=lambda t: (t[0].type.value, t[1].id))
        return found[offset : offset + limit]

    async def neighborhood(self, node_id, *, depth, rel_types=None, limit=100):
        depth = max(1, min(depth, MAX_DEPTH))
        if node_id not in self.nodes:
            return [], []
        seen_nodes = {node_id}
        seen_edges: dict[tuple, GraphEdge] = {}
        frontier = [node_id]
        for _ in range(depth):
            nxt = []
            for nid in frontier:
                for edge, other in await self.edges(
                    nid, rel_types=rel_types, limit=10_000
                ):
                    key = (
                        edge.type.value,
                        edge.source_id,
                        edge.target_id,
                        edge.properties.get("role"),
                    )
                    if key not in seen_edges:
                        if len(seen_edges) >= limit:
                            break
                        seen_edges[key] = edge
                    if other.id not in seen_nodes:
                        seen_nodes.add(other.id)
                        nxt.append(other.id)
            frontier = nxt
        nodes = [self.nodes[i].model_copy(deep=True) for i in sorted(seen_nodes)]
        return nodes, list(seen_edges.values())

    async def degree(self, node_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.edges_by_key.values():
            if node_id in (e.source_id, e.target_id):
                counts[e.type.value] = counts.get(e.type.value, 0) + 1
        return counts

    async def count_job_claims(self, job_id: str) -> int:
        return sum(
            1
            for n in self.nodes.values()
            if n.label is Label.CLAIM and n.properties.get("research_job_id") == job_id
        )

    async def delete_nodes(self, node_ids: list[str]) -> int:
        ids = set(node_ids)
        self.edges_by_key = {
            k: e
            for k, e in self.edges_by_key.items()
            if e.source_id not in ids and e.target_id not in ids
        }
        removed = [i for i in ids if self.nodes.pop(i, None) is not None]
        return len(removed)


# -- Neo4j store --------------------------------------------------------------------------


def _union(target: str, key: str) -> str:
    return (
        f"SET {target}.{key} = reduce(acc = coalesce({target}.{key}, []), x IN coalesce(row.lists.{key}, []) | "
        f"CASE WHEN x IN acc THEN acc ELSE acc + x END)"
    )


def _node_props(var: str) -> str:
    return f"{var} {{.*}}"


class Neo4jGraphRepository:
    """``GraphRepository`` backed by ``Neo4jClient`` (parameterized Cypher only)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    # schema --------------------------------------------------------------------------------

    async def initialize_schema(self) -> dict[str, Any]:
        for _, statement in [*CONSTRAINTS, *INDEXES]:
            await self._client.execute_write(statement, {})
        await self._client.execute_write(
            "MERGE (v:GraphSchemaVersion {id: 'aurora'}) "
            "ON CREATE SET v.created_at = $now SET v.version = $version, v.updated_at = $now",
            {"version": SCHEMA_VERSION, "now": utc_now().isoformat()},
        )
        return {
            "constraints": [n for n, _ in CONSTRAINTS],
            "indexes": [n for n, _ in INDEXES],
            "version": SCHEMA_VERSION,
        }

    async def verify_schema(self) -> dict[str, Any]:
        constraints = {
            r["name"]
            for r in await self._client.execute_read(
                "SHOW CONSTRAINTS YIELD name RETURN name", {}
            )
        }
        indexes = {
            r["name"]
            for r in await self._client.execute_read(
                "SHOW INDEXES YIELD name RETURN name", {}
            )
        }
        version = await self._client.execute_read(
            "MATCH (v:GraphSchemaVersion {id: 'aurora'}) RETURN v.version AS version",
            {},
        )
        return {
            "version": version[0]["version"] if version else None,
            "missing_constraints": [n for n, _ in CONSTRAINTS if n not in constraints],
            "missing_indexes": [n for n, _ in INDEXES if n not in indexes],
        }

    # writes ---------------------------------------------------------------------------------

    async def upsert_nodes(
        self, label: Label, rows: list[dict[str, Any]]
    ) -> tuple[int, int]:
        label = Label(label)
        query = "\n".join([
            "UNWIND $rows AS row",
            f"MERGE (n:{BASE_LABEL} {{id: row.id}})",
            "ON CREATE SET n.created_at = $now",
            f"SET n:{label.value}",
            "SET n += row.props",
            *[_union("n", k) for k in NODE_LIST_PROPS],
            "SET n.verification_status = CASE WHEN row.status IS NULL THEN n.verification_status "
            "WHEN n.verification_status IS NULL OR coalesce($rank[row.status], 0) > coalesce($rank[n.verification_status], 0) "
            "THEN row.status ELSE n.verification_status END",
            "SET n.updated_at = $now",
            "RETURN sum(CASE WHEN n.created_at = $now THEN 1 ELSE 0 END) AS created, count(n) AS total",
        ])  # fmt: skip
        res = await self._client.execute_write(
            query, {"rows": rows, "now": utc_now().isoformat(), "rank": STATUS_RANK}
        )
        created = int(res[0]["created"]) if res else 0
        total = int(res[0]["total"]) if res else 0
        return created, total - created

    async def upsert_edges(
        self, rel: Rel, source: Label, target: Label, rows: list[dict[str, Any]]
    ) -> tuple[int, int, int]:
        rel, source, target = Rel(rel), Label(source), Label(target)
        merge_key = " {role: coalesce(row.role, '')}" if rel is Rel.MENTIONS else ""
        query = "\n".join([
            "UNWIND $rows AS row",
            f"MATCH (a:{source.value} {{id: row.source_id}})",
            f"MATCH (b:{target.value} {{id: row.target_id}})",
            f"MERGE (a)-[r:{rel.value}{merge_key}]->(b)",
            "ON CREATE SET r.created_at = $now, r.first_observed_at = row.observed_at",
            "SET r += row.props",
            "SET r.assertion = CASE WHEN r.assertion = 'direct' OR row.direct THEN 'direct' ELSE coalesce(row.props.assertion, 'inferred') END",
            *[_union("r", k) for k in EDGE_LIST_PROPS],
            "SET r.verification_status = CASE WHEN r.verification_status IS NULL "
            "OR coalesce($rank[row.status], 0) > coalesce($rank[r.verification_status], 0) THEN row.status ELSE r.verification_status END",
            "SET r.has_conflict = coalesce(r.has_conflict, false) OR row.has_conflict",
            "SET r.confidence = CASE WHEN row.confidence IS NOT NULL AND (r.confidence IS NULL OR row.confidence > r.confidence) "
            "THEN row.confidence ELSE r.confidence END",
            "SET r.last_observed_at = CASE WHEN row.observed_at IS NOT NULL AND (r.last_observed_at IS NULL OR row.observed_at > r.last_observed_at) "
            "THEN row.observed_at ELSE r.last_observed_at END",
            "SET r.first_observed_at = CASE WHEN row.observed_at IS NOT NULL AND (r.first_observed_at IS NULL OR row.observed_at < r.first_observed_at) "
            "THEN row.observed_at ELSE r.first_observed_at END",
            "SET r.updated_at = $now",
            "RETURN sum(CASE WHEN r.created_at = $now THEN 1 ELSE 0 END) AS created, count(r) AS total",
        ])  # fmt: skip
        res = await self._client.execute_write(
            query, {"rows": rows, "now": utc_now().isoformat(), "rank": STATUS_RANK}
        )
        created = int(res[0]["created"]) if res else 0
        total = int(res[0]["total"]) if res else 0
        return created, total - created, len(rows) - total

    async def delete_nodes(self, node_ids: list[str]) -> int:
        res = await self._client.execute_write(
            f"MATCH (n:{BASE_LABEL}) WHERE n.id IN $ids DETACH DELETE n RETURN count(*) AS deleted",
            {"ids": node_ids},
        )
        return int(res[0]["deleted"]) if res else 0

    # reads ----------------------------------------------------------------------------------

    @staticmethod
    def _node(props: dict[str, Any], labels: list[str]) -> GraphNode | None:
        label = next((lab for lab in labels if lab in _LABEL_VALUES), None)
        if label is None:
            return None
        return GraphNode(id=props["id"], label=Label(label), properties=dict(props))

    _NODE_RETURN = (
        f"n {{.*}} AS props, [l IN labels(n) WHERE l <> '{BASE_LABEL}'] AS labels"
    )

    async def get_node(self, node_id: str) -> GraphNode | None:
        rows = await self._client.execute_read(
            f"MATCH (n:{BASE_LABEL} {{id: $id}}) RETURN {self._NODE_RETURN}",
            {"id": node_id},
        )
        return self._node(rows[0]["props"], rows[0]["labels"]) if rows else None

    async def get_nodes(self, node_ids: list[str]) -> list[GraphNode]:
        if not node_ids:
            return []
        rows = await self._client.execute_read(
            f"MATCH (n:{BASE_LABEL}) WHERE n.id IN $ids RETURN {self._NODE_RETURN} ORDER BY n.id",
            {"ids": node_ids},
        )
        by_id = {r["props"]["id"]: self._node(r["props"], r["labels"]) for r in rows}
        return [by_id[i] for i in node_ids if by_id.get(i) is not None]  # type: ignore[misc]

    async def find_nodes(
        self,
        label: Label,
        *,
        name: str | None = None,
        domain: str | None = None,
        limit: int = 20,
    ) -> list[GraphNode]:
        label = Label(label)
        rows = await self._client.execute_read(
            f"MATCH (n:{label.value}) WHERE ($name IS NULL OR n.normalized_name CONTAINS $name) "
            "AND ($domain IS NULL OR n.official_domain = $domain) "
            f"RETURN {self._NODE_RETURN} ORDER BY n.id LIMIT $limit",
            {"name": name, "domain": domain, "limit": limit},
        )
        return [
            n for r in rows if (n := self._node(r["props"], r["labels"])) is not None
        ]

    async def edges(
        self,
        node_id,
        *,
        direction="both",
        rel_types=None,
        neighbor_labels=None,
        limit=100,
        offset=0,
    ):
        pattern = {"out": "(n)-[r]->(m)", "in": "(n)<-[r]-(m)", "both": "(n)-[r]-(m)"}[
            direction
        ]
        types = [Rel(t).value for t in rel_types] if rel_types else None
        labels = (
            [Label(lab).value for lab in neighbor_labels] if neighbor_labels else None
        )
        rows = await self._client.execute_read(
            f"MATCH (n:{BASE_LABEL} {{id: $id}}) MATCH {pattern} "
            "WHERE ($types IS NULL OR type(r) IN $types) "
            "AND ($labels IS NULL OR any(l IN labels(m) WHERE l IN $labels)) "
            "RETURN type(r) AS type, startNode(r).id AS source_id, endNode(r).id AS target_id, properties(r) AS rprops, "
            f"m {{.*}} AS props, [l IN labels(m) WHERE l <> '{BASE_LABEL}'] AS labels "
            "ORDER BY type, props.id SKIP $offset LIMIT $limit",
            {
                "id": node_id,
                "types": types,
                "labels": labels,
                "offset": offset,
                "limit": limit,
            },
        )
        out = []
        for r in rows:
            node = self._node(r["props"], r["labels"])
            if node is None:
                continue
            out.append((GraphEdge(type=Rel(r["type"]), source_id=r["source_id"], target_id=r["target_id"],
                                  properties=dict(r["rprops"])), node))  # fmt: skip
        return out

    async def neighborhood(self, node_id, *, depth, rel_types=None, limit=100):
        depth = int(depth)
        if not 1 <= depth <= MAX_DEPTH:
            raise ValueError("depth out of range")
        types = [Rel(t).value for t in rel_types] if rel_types else None
        rows = await self._client.execute_read(
            f"MATCH p = (n:{BASE_LABEL} {{id: $id}})-[rels*1..{depth}]-(m) "  # depth is a validated int
            "WHERE $types IS NULL OR all(x IN rels WHERE type(x) IN $types) "
            "WITH p LIMIT $limit UNWIND relationships(p) AS r WITH DISTINCT r LIMIT $limit "
            "WITH r, startNode(r) AS s, endNode(r) AS e "
            "RETURN type(r) AS type, s.id AS source_id, e.id AS target_id, properties(r) AS rprops, "
            f"s {{.*}} AS sprops, [l IN labels(s) WHERE l <> '{BASE_LABEL}'] AS slabels, "
            f"e {{.*}} AS eprops, [l IN labels(e) WHERE l <> '{BASE_LABEL}'] AS elabels",
            {"id": node_id, "types": types, "limit": limit},
        )
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        root = await self.get_node(node_id)
        if root:
            nodes[root.id] = root
        for r in rows:
            for props, labels in (
                (r["sprops"], r["slabels"]),
                (r["eprops"], r["elabels"]),
            ):
                node = self._node(props, labels)
                if node is not None:
                    nodes[node.id] = node
            edges.append(GraphEdge(type=Rel(r["type"]), source_id=r["source_id"], target_id=r["target_id"],
                                   properties=dict(r["rprops"])))  # fmt: skip
        return [nodes[k] for k in sorted(nodes)], edges

    async def degree(self, node_id: str) -> dict[str, int]:
        rows = await self._client.execute_read(
            f"MATCH (n:{BASE_LABEL} {{id: $id}})-[r]-() RETURN type(r) AS type, count(r) AS n",
            {"id": node_id},
        )
        return {r["type"]: int(r["n"]) for r in rows}

    async def count_job_claims(self, job_id: str) -> int:
        rows = await self._client.execute_read(
            "MATCH (c:Claim {research_job_id: $job}) RETURN count(c) AS n",
            {"job": job_id},
        )
        return int(rows[0]["n"]) if rows else 0
