"""Persistent graph memory with live ACL checks and a strict data/instruction boundary."""

import json

from app.core.time import utc_now
from app.db.cypher import creation_properties, fingerprint, stable_id
from app.db.errors import GraphConflict, GraphNotFound
from app.models.graph import NodeKind, RelationshipKind, VerificationStatus, epoch_us, from_epoch_us
from app.models.memory import (
    MEMORY_VERSION,
    AgentContext,
    Audience,
    HandoffContext,
    HandoffRequest,
    MemoryClaim,
    MemoryConflict,
    MemoryQuery,
    MemoryRecord,
    MemoryType,
    MemoryWrite,
    ProcedurePlan,
    ReadOnlyStep,
    ReviewMemory,
    SupersedeMemory,
    ValidatedProcedure,
)
from app.repositories.memory import (
    READ_ROLES,
    SOURCE,
    WRITE_ROLES,
    MemoryMutation,
    MemoryRepository,
    active,
)
from app.services.memory_access import node_id
from app.services.memory_errors import MemoryAccessDenied, MemoryVersionConflict

EVIDENCE_KINDS = {"Evidence", "SecurityEvent", "RiskFinding", "Decision", "AgentRun", "Procedure"}


class MemoryService:
    def __init__(self, repository: MemoryRepository):
        self.repository = repository

    def _identity(self, request_id: str, payload: dict, action: str) -> tuple[str, str, str]:
        repo = self.repository
        key = stable_id(
            repo.scope.tenant_id, repo.space_id, repo.agent_id, "memory-operation", request_id
        )
        return (
            key,
            node_id(repo.scope.tenant_id, NodeKind.DECISION, key),
            fingerprint({"action": action, **payload}),
        )

    def _audit(
        self,
        mutation: MemoryMutation,
        key: str,
        digest: str,
        action: str,
        response: dict,
        details: dict,
    ) -> dict:
        return mutation.node(
            NodeKind.DECISION,
            key,
            {
                "action": "memory." + action,
                "status": "applied",
                "rationale": "Graph-authorized memory operation",
                "space_id": self.repository.space_id,
                "actor_agent_id": self.repository.agent_id,
                "request_sha256": digest,
                "response_json": json.dumps(response, sort_keys=True),
                "audit_json": json.dumps(details, sort_keys=True),
            },
            "audit",
        )

    def write_memory(self, request: MemoryWrite) -> MemoryRecord:
        return self._write(MemoryWrite.model_validate(request.model_dump()))

    def supersede_memory(self, request: SupersedeMemory) -> MemoryRecord:
        request = SupersedeMemory.model_validate(request.model_dump())
        return self._write(request.correction, previous=request)

    def _write(self, request: MemoryWrite, previous: SupersedeMemory | None = None) -> MemoryRecord:
        repo = self.repository
        action = "supersede" if previous else "write"
        payload = previous.model_dump(mode="json") if previous else request.model_dump(mode="json")
        key, operation_id, digest = self._identity(request.request_id, payload, action)
        for _ in range(5):
            access = repo.access()
            if access["role"] not in WRITE_ROLES:
                raise MemoryAccessDenied()
            existing = repo.operation(operation_id, digest)
            if existing:
                return self._records([repo.get(existing["memory_id"])])[0]
            old = None
            if previous:
                old = repo.get(previous.memory_id)["memory"]
                if old["state"] != "current" or old["memory_version"] != previous.expected_version:
                    raise MemoryVersionConflict()
                if old["author_agent_id"] != repo.agent_id and access["role"] != "curator":
                    raise MemoryAccessDenied()
                if old["author_agent_id"] != repo.agent_id and request.audience != Audience.TEAM:
                    raise MemoryAccessDenied()
                if (
                    request.memory_key != old["memory_key"]
                    or request.memory_type != old["memory_type"]
                ):
                    raise MemoryVersionConflict()
            series_id = (
                old["series_id"]
                if old
                else stable_id(
                    repo.scope.tenant_id,
                    repo.space_id,
                    repo.agent_id,
                    "memory-series",
                    request.memory_key,
                )
            )
            series = repo.series(series_id)
            if (old is None and series is not None) or (
                old is not None and (series is None or series["head_id"] != old["id"])
            ):
                raise MemoryVersionConflict()
            version = old["memory_version"] + 1 if old else 1
            source_key = series_id + "/v" + str(version)
            memory_id = node_id(repo.scope.tenant_id, NodeKind.AGENT_MEMORY, source_key)
            now = epoch_us(utc_now())
            valid_from = epoch_us(request.valid_from) if request.valid_from else now
            valid_to = epoch_us(request.valid_to) if request.valid_to else None
            if valid_to is not None and valid_to <= valid_from:
                raise MemoryVersionConflict()
            subjects = set(request.subject_ids)
            claim_key = ""
            claim_value = ""
            if request.claim:
                subjects.add(request.claim.subject_id)
                if request.claim.object_id:
                    subjects.add(request.claim.object_id)
                claim_key = fingerprint(
                    {
                        "space_id": repo.space_id,
                        "subject": request.claim.subject_id,
                        "predicate": request.claim.predicate,
                        "object": request.claim.object_id,
                    }
                )
                value = request.claim.value
                if type(value) is float and value.is_integer():
                    value = int(value)
                claim_value = json.dumps(value, sort_keys=True)
            references = subjects | set(request.evidence_ids)
            if not references <= access["contexts"].keys():
                raise MemoryAccessDenied()
            if any(
                access["contexts"][i]["kind"] not in EVIDENCE_KINDS for i in request.evidence_ids
            ):
                raise MemoryAccessDenied()
            if any(
                access["contexts"][i]["kind"] in {"AgentMemory", "MemorySpace", "Handoff"}
                for i in subjects
            ):
                raise MemoryAccessDenied()
            others = (
                repo.retrieve(MemoryQuery(limit=100), audit=True, claim_key=claim_key)
                if claim_key
                else []
            )
            mutation = MemoryMutation(repo.scope.tenant_id, key, now)
            for index, reference_id in enumerate(sorted(references)):
                mutation.bind(access["contexts"][reference_id], "x" + str(index), context=True)
            variables = {
                identifier: "x" + str(i) for i, identifier in enumerate(sorted(references))
            }
            if series is None:
                literal, params = creation_properties(
                    {
                        "id": series_id,
                        "tenant_id": repo.scope.tenant_id,
                        "space_id": repo.space_id,
                        "head_id": memory_id,
                        "version": version,
                        "created_at": now,
                        "updated_at": now,
                        "observed_at": now,
                        "source": SOURCE,
                        "source_id": series_id,
                        "collector": MEMORY_VERSION,
                        "source_uri": "urn:shadowchain:memory:series:" + series_id,
                        "kind": "MemorySeries",
                        "fingerprint": fingerprint({"series_id": series_id}),
                    },
                    "series",
                )
                mutation.params.update(params)
                mutation.changes.append(f"CREATE (series:MemorySeries {literal}) ")
            else:
                mutation.params["series_id"] = series_id
                mutation.matches.append(
                    "MATCH (series:MemorySeries {tenant_id: $tenant_id, "
                    "id: $series_id, space_id: $space_id}) "
                )
            audit = self._audit(
                mutation,
                key,
                digest,
                action,
                {"memory_id": memory_id},
                {"previous_id": old["id"] if old else None, "new_version": version},
            )
            attributes = {
                "memory_type": request.memory_type.value,
                "memory_key": request.memory_key,
                "memory_version": version,
                "series_id": series_id,
                "space_id": repo.space_id,
                "content": request.content,
                "confidence": request.confidence,
                "audience": request.audience.value,
                "author_agent_id": repo.agent_id,
                "state": "current",
                "verification_status": "unverified",
                "source_trust": "untrusted_data",
                "valid_from": valid_from,
                "claim_key": claim_key,
                "claim_value_json": claim_value,
                "claim_json": request.claim.model_dump_json() if request.claim else "null",
                "procedure_json": request.procedure.model_dump_json()
                if request.procedure
                else "null",
                "source_provenance_json": request.provenance.model_dump_json(),
                "request_sha256": digest,
                "memory_engine_version": MEMORY_VERSION,
                "reference_count": len(subjects) + len(request.evidence_ids) + 1,
            }
            if valid_to is not None:
                attributes["valid_to"] = valid_to
            memory = mutation.node(NodeKind.AGENT_MEMORY, source_key, attributes, "memory")
            mutation.edge(access["agent"], memory, RelationshipKind.REMEMBERS, "agent", "memory")
            mutation.edge(memory, access["space"], RelationshipKind.BELONGS_TO, "memory", "space")
            mutation.edge(memory, audit, RelationshipKind.DERIVED_FROM, "memory", "audit")
            for reference_id in sorted(subjects):
                mutation.edge(
                    memory,
                    access["contexts"][reference_id],
                    RelationshipKind.ABOUT,
                    "memory",
                    variables[reference_id],
                )
            for reference_id in request.evidence_ids:
                mutation.edge(
                    memory,
                    access["contexts"][reference_id],
                    RelationshipKind.DERIVED_FROM,
                    "memory",
                    variables[reference_id],
                )
            if old:
                mutation.bind(old, "old")
                mutation.edge(memory, old, RelationshipKind.SUPERSEDES, "memory", "old")
                mutation.changes.append(
                    "SET old.state = 'superseded', old.superseded_at = $changed_at, "
                    "old.updated_at = $changed_at "
                )
            for index, candidate in enumerate(others):
                other = candidate["memory"]
                if old and other["id"] == old["id"]:
                    continue
                if (
                    other["claim_value_json"] == claim_value
                    or (other.get("valid_to") is not None and other["valid_to"] <= valid_from)
                    or (valid_to is not None and valid_to <= other["valid_from"])
                ):
                    continue
                variable = "c" + str(index)
                mutation.bind(other, variable)
                mutation.edge(
                    memory,
                    other,
                    RelationshipKind.CONTRADICTS,
                    "memory",
                    variable,
                    extra={"claim_key": claim_key, "verification_status": "disputed"},
                )
            mutation.params.update({"new_head": memory_id, "new_version": version})
            mutation.changes.append(
                "SET series.head_id = $new_head, series.version = $new_version, "
                "series.updated_at = $changed_at "
            )
            try:
                if repo.commit(mutation, access["space"]["revision"], WRITE_ROLES):
                    return self._records([repo.get(memory_id)])[0]
            except GraphConflict:
                continue
        raise MemoryVersionConflict()

    def _records(
        self, rows: list[dict], at=None, other_viewer: MemoryRepository | None = None
    ) -> list[MemoryRecord]:
        visible = {row["memory"]["id"]: row["memory"] for row in rows}
        conflicts = self.repository.raw_conflicts(list(visible)) if rows else []
        partners = sorted(
            {
                identifier
                for c in conflicts
                if c["a"] in visible or c["b"] in visible
                for identifier in (c["a"], c["b"])
            }
            - visible.keys()
        )
        for offset in range(0, len(partners), 50):
            additional = self.repository.retrieve(
                MemoryQuery(
                    memory_ids=partners[offset : offset + 50],
                    include_superseded=True,
                    limit=100,
                ),
                audit=True,
            )
            visible.update({row["memory"]["id"]: row["memory"] for row in additional})
        if other_viewer is not None:
            ids = sorted(visible)
            shared_ids = set()
            for offset in range(0, len(ids), 50):
                shared_ids.update(
                    row["memory"]["id"]
                    for row in other_viewer.retrieve(
                        MemoryQuery(
                            memory_ids=ids[offset : offset + 50], include_superseded=True, limit=100
                        ),
                        audit=True,
                    )
                )
            visible = {
                identifier: record
                for identifier, record in visible.items()
                if identifier in shared_ids
            }
        instant = epoch_us(at or utc_now())

        def current(identifier):
            record = visible.get(identifier)
            return (
                record is not None
                and record["state"] == "current"
                and record["valid_from"] <= instant
                and (record.get("valid_to") is None or instant < record["valid_to"])
            )

        results = []
        for row in rows:
            memory, refs = row["memory"], row["refs"]
            current_conflicts = sorted(
                {
                    c["b"] if c["a"] == memory["id"] else c["a"]
                    for c in conflicts
                    if memory["id"] in {c["a"], c["b"]}
                    and c["a"] in visible
                    and c["b"] in visible
                    and current(c["a"])
                    and current(c["b"])
                }
            )
            results.append(
                MemoryRecord(
                    id=memory["id"],
                    memory_key=memory["memory_key"],
                    memory_type=memory["memory_type"],
                    version=memory["memory_version"],
                    author_agent_id=memory["author_agent_id"],
                    audience=memory["audience"],
                    confidence=memory["confidence"],
                    verification_status=memory["verification_status"],
                    state=memory["state"],
                    content=memory["content"],
                    created_at=from_epoch_us(memory["created_at"]),
                    updated_at=from_epoch_us(memory["updated_at"]),
                    valid_from=from_epoch_us(memory["valid_from"]),
                    valid_to=from_epoch_us(memory["valid_to"])
                    if memory.get("valid_to") is not None
                    else None,
                    provenance=json.loads(memory["source_provenance_json"]),
                    claim=MemoryClaim.model_validate_json(memory["claim_json"])
                    if memory["claim_json"] != "null"
                    else None,
                    procedure=ProcedurePlan.model_validate_json(memory["procedure_json"])
                    if memory["procedure_json"] != "null"
                    else None,
                    subjects=sorted(
                        [ref["node"] for ref in refs if ref["kind"] == "ABOUT"],
                        key=lambda n: n["id"],
                    ),
                    evidence=sorted(
                        [
                            {"record": ref["node"], "data_only": True, "executable": False}
                            for ref in refs
                            if ref["kind"] == "DERIVED_FROM"
                        ],
                        key=lambda e: e["record"]["id"],
                    ),
                    conflicting_memory_ids=current_conflicts,
                )
            )
        return results

    def retrieve_related_memories(self, query: MemoryQuery | None = None) -> list[MemoryRecord]:
        self.repository.access()
        request = query or MemoryQuery()
        return self._records(self.repository.retrieve(request), request.at)

    def retrieve_investigation_history(self, investigation_id: str) -> list[MemoryRecord]:
        access = self.repository.access()
        if access["contexts"].get(investigation_id, {}).get("kind") != "Investigation":
            raise GraphNotFound()
        return self._records(
            self.repository.retrieve(
                MemoryQuery(
                    subject_ids=[investigation_id], include_superseded=True, max_hops=2, limit=100
                ),
                audit=True,
            )
        )

    def get_agent_context(self, query: MemoryQuery | None = None) -> AgentContext:
        access = self.repository.access()
        # This boundary cannot be weakened by include_superseded in an incoming query.
        request = (query or MemoryQuery()).model_copy(update={"include_superseded": False})
        records = self._records(self.repository.retrieve(request), request.at)
        return self._context(records, access)

    def _context(self, records: list[MemoryRecord], access: dict) -> AgentContext:
        established = [
            r
            for r in records
            if r.memory_type == MemoryType.SEMANTIC
            and r.verification_status == VerificationStatus.VERIFIED
            and not r.conflicting_memory_ids
        ]
        procedures = [
            ValidatedProcedure(memory_id=r.id, steps=r.procedure.steps, outcome=r.procedure.outcome)
            for r in records
            if r.memory_type == MemoryType.PROCEDURAL
            and r.procedure
            and r.procedure.outcome in {"successful", "lesson"}
            and r.verification_status == VerificationStatus.VERIFIED
            and not r.conflicting_memory_ids
        ]
        return AgentContext(
            agent_id=access["agent"]["id"],
            space_id=self.repository.space_id,
            observations=records,
            established_facts=established,
            validated_procedures=procedures,
            read_only_capabilities=list(ReadOnlyStep),
        )

    def detect_memory_conflicts(self, query: MemoryQuery | None = None) -> list[MemoryConflict]:
        records = self.retrieve_related_memories(query)
        visible = {r.id: r for r in records}
        return [
            MemoryConflict(
                id=c["edge"]["id"],
                memory_ids=sorted([c["a"], c["b"]]),
                claim_key=c["edge"]["claim_key"],
                current=visible[c["a"]].state == visible[c["b"]].state == "current",
            )
            for c in self.repository.raw_conflicts(list(visible))
            if c["a"] in visible and c["b"] in visible
        ]

    def review_memory(self, request: ReviewMemory) -> MemoryRecord:
        request = ReviewMemory.model_validate(request.model_dump())
        repo = self.repository
        key, operation_id, digest = self._identity(
            request.request_id, request.model_dump(mode="json"), "review"
        )
        for _ in range(5):
            access = repo.access()
            if access["role"] != "curator":
                raise MemoryAccessDenied()
            if repo.operation(operation_id, digest):
                return self._records([repo.get(request.memory_id)])[0]
            record = repo.get(request.memory_id)["memory"]
            if record["state"] != "current" or record["memory_version"] != request.expected_version:
                raise MemoryVersionConflict()
            if (
                record["memory_type"] == "procedural"
                and request.verification_status == VerificationStatus.VERIFIED
            ):
                if any(
                    request.memory_id in c.memory_ids and c.current
                    for c in self.detect_memory_conflicts()
                ):
                    raise MemoryVersionConflict()
            now = epoch_us(utc_now())
            mutation = MemoryMutation(repo.scope.tenant_id, key, now)
            mutation.bind(record, "memory")
            audit = self._audit(
                mutation,
                key,
                digest,
                "review",
                {"memory_id": record["id"]},
                {
                    "before": record["verification_status"],
                    "after": request.verification_status.value,
                    "reason": request.reason,
                    "version": record["memory_version"],
                },
            )
            mutation.edge(record, audit, RelationshipKind.DERIVED_FROM, "memory", "audit")
            mutation.params["status"] = request.verification_status.value
            mutation.changes.append(
                "SET memory.verification_status = $status, memory.reviewed_at = $changed_at, "
                "memory.reviewed_by = $agent_id, memory.updated_at = $changed_at, "
                "memory.reference_count = memory.reference_count + 1 "
            )
            try:
                if repo.commit(mutation, access["space"]["revision"], ["curator"]):
                    return self._records([repo.get(record["id"])])[0]
            except GraphConflict:
                continue
        raise MemoryVersionConflict()

    def create_handoff_context(self, request: HandoffRequest) -> HandoffContext:
        request = HandoffRequest.model_validate(request.model_dump())
        repo = self.repository
        if request.recipient_agent_id == repo.agent_id:
            raise MemoryAccessDenied()
        key, operation_id, digest = self._identity(
            request.request_id, request.model_dump(mode="json"), "handoff"
        )
        for _ in range(5):
            access = repo.access()
            recipient = repo.access(request.recipient_agent_id)
            if (
                access["role"] not in WRITE_ROLES
                or not set(request.subject_ids) <= access["contexts"].keys()
            ):
                raise MemoryAccessDenied()
            duplicate = repo.operation(operation_id, digest)
            if duplicate:
                return self.get_handoff_context(duplicate["handoff_id"])
            rows = repo.retrieve(
                MemoryQuery(subject_ids=request.subject_ids), recipient=request.recipient_agent_id
            )
            now = epoch_us(utc_now())
            mutation = MemoryMutation(repo.scope.tenant_id, key, now)
            mutation.bind(recipient["agent"], "recipient")
            mutation.matches.append(
                "MATCH (recipient)-[rm:MEMBER_OF]->(space) "
                f"WHERE {active('rm')} AND rm.role IN $read_roles "
            )
            mutation.params["read_roles"] = READ_ROLES
            handoff_id = node_id(repo.scope.tenant_id, NodeKind.HANDOFF, key + "/handoff")
            self._audit(
                mutation,
                key,
                digest,
                "handoff",
                {"handoff_id": handoff_id},
                {"recipient": request.recipient_agent_id, "memory_count": len(rows)},
            )
            handoff = mutation.node(
                NodeKind.HANDOFF,
                key + "/handoff",
                {
                    "reason": "Authorized graph-grounded memory context",
                    "status": "available",
                    "space_id": repo.space_id,
                    "sender_agent_id": repo.agent_id,
                    "recipient_agent_id": request.recipient_agent_id,
                    "context_sha256": fingerprint(
                        {"memories": sorted(row["memory"]["id"] for row in rows)}
                    ),
                },
                "handoff",
            )
            mutation.edge(
                access["agent"], handoff, RelationshipKind.HANDS_OFF_TO, "agent", "handoff"
            )
            mutation.edge(
                handoff, recipient["agent"], RelationshipKind.HANDS_OFF_TO, "handoff", "recipient"
            )
            for index, row in enumerate(rows):
                variable = "m" + str(index)
                mutation.bind(row["memory"], variable)
                mutation.edge(
                    handoff, row["memory"], RelationshipKind.INCLUDES_MEMORY, "handoff", variable
                )
            try:
                if repo.commit(mutation, access["space"]["revision"], WRITE_ROLES):
                    return self.get_handoff_context(handoff_id)
            except GraphConflict:
                continue
        raise MemoryVersionConflict()

    def get_handoff_context(self, handoff_id: str) -> HandoffContext:
        repo = self.repository
        repo.access()
        record = repo.handoff(handoff_id)
        handoff = record["handoff"]
        recipient_repo = MemoryRepository(
            repo.database, repo.scope, repo.space_id, handoff["recipient_agent_id"]
        )
        recipient_service = MemoryService(recipient_repo)
        recipient_repo.access()
        rows = (
            recipient_repo.retrieve(MemoryQuery(memory_ids=record["memory_ids"]))
            if record["memory_ids"]
            else []
        )
        # Current caller and recipient permissions are checked; the stored handoff
        # does not itself grant access or revive superseded observations.
        visible_sender = (
            {r["memory"]["id"] for r in repo.retrieve(MemoryQuery(memory_ids=record["memory_ids"]))}
            if record["memory_ids"]
            else set()
        )
        rows = [r for r in rows if r["memory"]["id"] in visible_sender]
        context = recipient_service._context(
            self._records(rows, other_viewer=recipient_repo), recipient_repo.access()
        )
        return HandoffContext(
            handoff_id=handoff_id,
            sender_agent_id=handoff["sender_agent_id"],
            recipient_agent_id=handoff["recipient_agent_id"],
            memory_ids=[r["memory"]["id"] for r in rows],
            context=context,
        )
