"""Tenant-scoped cases, immutable audit operations and bounded materialized-path reads."""

import json

from app.core.time import utc_now
from app.db.cypher import creation_properties, fingerprint, stable_id
from app.db.errors import GraphConflict, GraphNotFound
from app.models.graph import NodeKind as N
from app.models.graph import Provenance, epoch_us, from_epoch_us
from app.models.graph import RelationshipKind as R
from app.models.investigation import CASE_SOURCE, CASE_VERSION, CHECKS
from app.models.workflow import STAGES, AgentKey
from app.repositories.memory import MemoryMutation
from app.repositories.workflow import WorkflowRepository
from app.services.investigation_errors import CaseLimitExceeded, InvalidCaseTransition


def case_node_id(tenant, kind, key):
    return stable_id(tenant, kind.value, "", CASE_SOURCE, key)


class CaseMutation(MemoryMutation):
    def provenance(self, key):
        return Provenance(
            source=CASE_SOURCE,
            source_id=key,
            observed_at=from_epoch_us(self.now),
            collector=CASE_VERSION,
            source_uri="urn:shadowchain:investigation:" + key,
        )


class InvestigationRepository(WorkflowRepository):
    def stream(self):
        identifier = stable_id(self.scope.tenant_id, "case-stream")
        rows = self.database.query(
            "MERGE (s:CaseStream {id: $id, tenant_id: $tenant_id}) "
            "ON CREATE SET s.revision = 0, s.lock_version = 0, s.created_at = $now, "
            "s.updated_at = $now, s.observed_at = $now, s.source = $source, "
            "s.source_id = $id, s.collector = $version, s.source_uri = $uri "
            ", s.kind = 'CaseStream', s.fingerprint = $digest "
            "RETURN properties(s)",
            self.params(
                {
                    "id": identifier,
                    "now": epoch_us(utc_now()),
                    "source": CASE_SOURCE,
                    "version": CASE_VERSION,
                    "uri": "urn:shadowchain:case-stream:" + identifier,
                    "digest": fingerprint({"id": identifier}),
                }
            ),
            idempotent=True,
        )
        return rows[0][0]

    def case(self, identifier):
        rows = self.read(
            "MATCH (i:SecurityCase {tenant_id: $tenant_id, id: $id}) RETURN properties(i)",
            {"id": identifier},
        )
        if not rows:
            raise GraphNotFound()
        return rows[0][0]

    def cases(self, status=None, limit=50):
        rows = self.read(
            "MATCH (i:SecurityCase {tenant_id: $tenant_id}) "
            "WHERE $status IS NULL OR i.status = $status "
            "RETURN properties(i) ORDER BY i.created_at, i.id LIMIT $limit",
            {"status": status, "limit": limit + 1},
        )
        if len(rows) > limit:
            raise CaseLimitExceeded()
        return [r[0] for r in rows]

    def operation_key(self, case_id, action, request_id, actor_id="system"):
        return stable_id(self.scope.tenant_id, case_id, action, request_id, actor_id)

    def operation(self, key, digest):
        rows = self.read(
            "MATCH (d:Decision {tenant_id: $tenant_id, id: $id, source: $source}) "
            "RETURN d.request_sha256, d.response_json",
            {"id": case_node_id(self.scope.tenant_id, N.DECISION, key), "source": CASE_SOURCE},
        )
        if not rows:
            return None
        if rows[0][0] != digest:
            raise GraphConflict()
        return json.loads(rows[0][1])

    def audit(self, mutation, key, case, action, actor, details, response):
        if max(len(json.dumps(details)), len(json.dumps(response))) > 15000:
            raise CaseLimitExceeded()
        audit = mutation.node(
            N.DECISION,
            key,
            {
                "action": "case." + action,
                "status": "applied",
                "rationale": "Audited investigation operation",
                "actor_agent_id": actor,
                "case_id": case["id"],
                "space_id": case["space_id"],
                "request_sha256": mutation.params["digest"],
                "audit_json": json.dumps(details, sort_keys=True),
                "response_json": json.dumps(response, sort_keys=True),
            },
            "audit",
        )
        mutation.edge(audit, case, R.DERIVED_FROM, "audit", "investigation")
        return audit

    def commit(self, mutation, stream, case=None):
        query = (
            "MATCH (s:CaseStream {tenant_id: $tenant_id, id: $stream_id}) "
            "SET s.lock_version = s.lock_version + 1 WITH s WHERE s.revision = $stream_revision "
        )
        if case:
            query += (
                "MATCH (investigation:SecurityCase {tenant_id: $tenant_id, id: $case_id}) "
                "WHERE investigation.revision = $case_revision "
            )
            if mutation.params.get("actor_id"):
                query += (
                    "MATCH (authorized_actor:Agent {tenant_id: $tenant_id, id: $actor_id})"
                    "-[membership:MEMBER_OF]->(authorized_space:MemorySpace "
                    "{tenant_id: $tenant_id, id: $authorized_space_id}) "
                    "WHERE membership.tenant_id = $tenant_id "
                    "AND membership.verification_status = 'verified' "
                    "AND membership.role IN ['writer', 'curator'] "
                    "AND membership.valid_from <= $auth_now "
                    "AND (membership.valid_to IS NULL OR $auth_now < membership.valid_to) "
                )
                if mutation.params.get("transfer_recipient"):
                    query += (
                        "MATCH (authorized_recipient:Agent {tenant_id: $tenant_id, "
                        "id: $transfer_recipient})"
                        "-[recipient_membership:MEMBER_OF]->(authorized_space) "
                        "WHERE recipient_membership.tenant_id = $tenant_id "
                        "AND recipient_membership.verification_status = 'verified' "
                        "AND recipient_membership.valid_from <= $auth_now "
                        "AND (recipient_membership.valid_to IS NULL OR "
                        "$auth_now < recipient_membership.valid_to) "
                    )
        rows = self.database.mutate(
            query
            + "".join(mutation.matches + mutation.changes)
            + "SET s.revision = s.revision + 1, s.updated_at = $now RETURN s.revision",
            self.params(
                {
                    **mutation.params,
                    "stream_id": stream["id"],
                    "stream_revision": stream["revision"],
                    "case_id": case["id"] if case else None,
                    "case_revision": case["revision"] if case else None,
                    "authorized_space_id": case["space_id"] if case else None,
                    "auth_now": epoch_us(utc_now()),
                    "now": mutation.now,
                    "changed_at": mutation.now,
                }
            ),
            idempotent=True,
            tenant_id=self.scope.tenant_id,
            event_type="investigation.changed",
            entity_ids=[case["id"]] if case else [],
        )
        return bool(rows)

    def mutation(self, key, digest):
        mutation = CaseMutation(self.scope.tenant_id, key, epoch_us(utc_now()))
        mutation.params["digest"] = digest
        return mutation

    def touch(self, mutation, case, **updates):
        updates = {"revision": case["revision"] + 1, "updated_at": mutation.now, **updates}
        for key, value in updates.items():
            # These property names come exclusively from the closed service implementation.
            if key not in {
                "revision",
                "updated_at",
                "status",
                "severity",
                "assessed_at",
                "owner_agent_id",
                "resolution",
            }:
                raise ValueError("Invalid case update")
            mutation.params["update_" + key] = value
            mutation.changes.append(f"SET investigation.{key} = $update_{key} ")

    def linked(self, identifier):
        rows = self.read(
            "MATCH path=(i:SecurityCase {tenant_id: $tenant_id, id: $id})"
            "-[edge:INVESTIGATES|SUPPORTS|CONTRADICTS|REMEMBERS|ASSIGNED_TO|"
            "BELONGS_TO|DERIVED_FROM|SUPERSEDES]-"
            "(n:Entity) WHERE all(v IN nodes(path) WHERE v.tenant_id = $tenant_id) "
            "AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id) "
            "RETURN properties(n), properties(edge) LIMIT 2001",
            {"id": identifier},
        )
        if len(rows) > 2000:
            raise CaseLimitExceeded()
        return rows

    def prior_incidents(self, case):
        rows = self.read(
            "MATCH path=(current:SecurityCase {tenant_id: $tenant_id, id: $id})"
            "-[:INVESTIGATES]->(asset:Entity)<-[:INVESTIGATES|AFFECTS*1..2]-(prior:Investigation) "
            "WHERE prior.id <> current.id AND prior.created_at < current.created_at "
            "AND asset.kind IN ['Identity', 'Role', 'Resource'] "
            "AND (prior.space_id = $space_id OR "
            "(prior.space_id IS NULL AND prior.synthetic = true)) "
            "AND all(n IN nodes(path) WHERE n.tenant_id = $tenant_id) "
            "AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id) "
            "WITH prior, collect(DISTINCT asset.id) AS shared "
            "RETURN properties(prior), shared ORDER BY prior.created_at, prior.id LIMIT 31",
            {"id": case["id"], "space_id": case["space_id"]},
        )
        if len(rows) > 30:
            raise CaseLimitExceeded()
        return rows

    def incident_assets(self, identifier):
        rows = self.read(
            "MATCH path=(i:Investigation {tenant_id: $tenant_id, id: $id})"
            "-[:INVESTIGATES|AFFECTS*1..2]->(asset:Entity) "
            "WHERE asset.kind IN ['Identity', 'Role', 'Resource'] "
            "AND all(v IN nodes(path) WHERE v.id = $id OR v.kind <> 'Investigation') "
            "AND all(n IN nodes(path) WHERE n.tenant_id = $tenant_id) "
            "AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id) "
            "RETURN DISTINCT asset.id LIMIT 101",
            {"id": identifier},
        )
        if len(rows) > 100:
            raise CaseLimitExceeded()
        return sorted(r[0] for r in rows)

    def workflow_cases(self, identifier):
        result = [
            row[0]
            for row in self.read(
                "MATCH path=(c:SecurityCase {tenant_id: $tenant_id})-[:INVESTIGATES]->"
                "(w:WorkflowInvestigation {tenant_id: $tenant_id, id: $id}) "
                "WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id) "
                "AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id) "
                "RETURN DISTINCT c.id ORDER BY c.id LIMIT 101",
                {"id": identifier},
            )
        ]
        if len(result) > 100:
            raise CaseLimitExceeded()
        return result

    def observe(self, finding, analysis, space_id, evidence, workflow_id=None):
        key = self.operation_key(
            space_id + "/" + finding.path_id, "detection", analysis.analysis_id
        )
        digest = fingerprint(
            {"analysis_id": analysis.analysis_id, "path_id": finding.path_id, "space_id": space_id}
        )
        for _ in range(5):
            duplicate = self.operation(key, digest)
            if duplicate:
                return duplicate["case_id"]
            stream = self.stream()
            head_id = stable_id(self.scope.tenant_id, "case-head", space_id, finding.path_id)
            heads = self.read(
                "MATCH (h:CaseHead {tenant_id: $tenant_id, id: $id}) RETURN properties(h)",
                {"id": head_id},
            )
            previous = self.case(heads[0][0]["head_id"]) if heads else None
            fresh = previous is None or (
                previous["status"] in {"RESOLVED", "CLOSED"}
                and epoch_us(analysis.at) >= previous["assessed_at"]
            )
            generation = (
                (heads[0][0]["generation"] + 1 if heads else 1) if fresh else previous["generation"]
            )
            source_key = "case/" + space_id + "/" + finding.path_id + "/" + str(generation)
            identifier = case_node_id(self.scope.tenant_id, N.INVESTIGATION, source_key)
            mutation = self.mutation(key, digest)
            if fresh:
                case = mutation.node(
                    N.INVESTIGATION,
                    source_key,
                    {
                        "title": "Investigate potential sensitive-resource access",
                        "status": "NEW",
                        "summary": "Graph-grounded potential attack path; "
                        "effective authorization remains unproven",
                        "severity": finding.score.severity,
                        "path_id": finding.path_id,
                        "generation": generation,
                        "revision": 0,
                        "case_version": CASE_VERSION,
                        "owner_agent_id": self.agent_id(AgentKey.MEMORY_KEEPER),
                        "space_id": space_id,
                        "assessed_at": epoch_us(analysis.at),
                    },
                    "investigation",
                )
                mutation.changes.append("SET investigation:SecurityCase ")
                if heads:
                    mutation.params["head_id"] = head_id
                    mutation.matches.append(
                        "MATCH (head:CaseHead {tenant_id: $tenant_id, id: $head_id}) "
                    )
                    mutation.params.update(new_head=identifier, generation=generation)
                    mutation.changes.append(
                        "SET head.head_id = $new_head, head.generation = $generation, "
                        "head.updated_at = $now "
                    )
                    mutation.bind(previous, "previous")
                    mutation.edge(case, previous, R.SUPERSEDES, "investigation", "previous")
                else:
                    props = {
                        **{
                            k: case[k]
                            for k in (
                                "tenant_id",
                                "source",
                                "collector",
                                "observed_at",
                                "created_at",
                                "updated_at",
                            )
                        },
                        "id": head_id,
                        "head_id": identifier,
                        "generation": generation,
                        "source_id": head_id,
                        "source_uri": "urn:shadowchain:case-head:" + head_id,
                        "kind": "CaseHead",
                        "fingerprint": fingerprint({"id": head_id}),
                    }
                    literal, params = creation_properties(props, "head")
                    mutation.params.update(params)
                    mutation.changes.append(f"CREATE (head:CaseHead {literal}) ")
                for index, check in enumerate(CHECKS):
                    task = mutation.node(
                        N.TASK,
                        identifier + "/" + check.value,
                        {
                            "title": check.value.replace("_", " "),
                            "status": "pending",
                            "task_type": "investigation_check",
                            "check_key": check.value,
                            "investigation_id": identifier,
                            "agent_id": case["owner_agent_id"],
                            "sequence": index,
                            "output_json": "null",
                        },
                        "check" + str(index),
                    )
                    mutation.edge(task, case, R.BELONGS_TO, "check" + str(index), "investigation")
                    mutation.bind(
                        {"id": case["owner_agent_id"], "kind": "Agent"}, "check_owner" + str(index)
                    )
                    mutation.edge(
                        task,
                        {"id": case["owner_agent_id"], "kind": "Agent"},
                        R.ASSIGNED_TO,
                        "check" + str(index),
                        "check_owner" + str(index),
                    )
            else:
                case = previous
                self.touch(
                    mutation,
                    case,
                    **(
                        {"severity": finding.score.severity, "assessed_at": epoch_us(analysis.at)}
                        if epoch_us(analysis.at) >= case["assessed_at"]
                        else {}
                    ),
                )
            assets = [finding.identity, *finding.roles, finding.resource]
            references = {n["id"]: self.node(n["id"]) for n in assets}
            references[finding.finding_id] = self.node(finding.finding_id)
            for event in finding.contributing_events:
                try:
                    references[event.id] = self.node(event.id)
                except GraphNotFound:
                    pass  # The immutable evidence snapshots still retain the source record.
            if workflow_id:
                references[workflow_id] = self.node(workflow_id)
            for index, node in enumerate(references.values()):
                variable = "subject" + str(index)
                mutation.bind(node, variable)
                mutation.edge(case, node, R.INVESTIGATES, "investigation", variable)
            for index, item in enumerate(evidence):
                variable = "evidence" + str(index)
                mutation.bind(item, variable)
                mutation.edge(item, case, R.SUPPORTS, variable, "investigation")
            if fresh:
                for index, stage in enumerate(STAGES):
                    agent = self.node(self.agent_id(stage))
                    mutation.bind(agent, "agent" + str(index))
                    mutation.edge(case, agent, R.ASSIGNED_TO, "investigation", "agent" + str(index))
                hypothesis = mutation.node(
                    N.DECISION,
                    identifier + "/hypothesis",
                    {
                        "action": "case.hypothesis",
                        "status": "unresolved",
                        "case_id": identifier,
                        "actor_agent_id": self.agent_id(AgentKey.RISK_ORACLE),
                        "space_id": space_id,
                        "rationale": finding.explanation,
                        "audit_json": json.dumps(
                            {
                                "finding_id": finding.finding_id,
                                "evidence_ids": [e["id"] for e in evidence],
                                "confidence": 0.5,
                            }
                        ),
                    },
                    "hypothesis",
                )
                mutation.edge(hypothesis, case, R.DERIVED_FROM, "hypothesis", "investigation")
            self.audit(
                mutation,
                key,
                case,
                "detection",
                self.agent_id(AgentKey.RISK_ORACLE),
                {
                    "analysis_id": analysis.analysis_id,
                    "finding_id": finding.finding_id,
                    "contributing_event_ids": [e.id for e in finding.contributing_events],
                    "assessed_at": epoch_us(analysis.at),
                    "initial_status": "NEW" if fresh else case["status"],
                },
                {"case_id": identifier},
            )
            try:
                if self.commit(mutation, stream, None if fresh else case):
                    return identifier
            except GraphConflict:
                continue
        raise InvalidCaseTransition()
