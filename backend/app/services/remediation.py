"""Evidence-backed suggestions and read-only Cypher overlays; never an AWS executor."""

import json

from app.core.time import utc_now
from app.db.cypher import fingerprint, stable_id
from app.db.errors import GraphConflict
from app.models.graph import NodeKind as N
from app.models.graph import RelationshipKind as R
from app.models.graph import epoch_us, from_epoch_us
from app.models.remediation import (
    SIMULATOR_VERSION,
    AccessImpact,
    Proposal,
    ProposalReview,
    ProposalStatus,
    ProposeRequest,
    Recommendations,
    RecommendRequest,
    RiskDelta,
    Simulation,
    SimulationRequest,
)
from app.models.remediation import (
    RecommendationKind as K,
)
from app.models.workflow import AgentKey
from app.repositories.remediation import RemediationMutation, identifier
from app.repositories.risk import RiskRepository
from app.schemas.risk import AnalysisRequest
from app.services.remediation_errors import (
    InvalidRemediation,
    RemediationAccessDenied,
    RemediationConflict,
    RemediationLimit,
)
from app.services.risk import LIMITATIONS, RiskEngine, graph_records, view

EXPLANATIONS = {
    K.EXCESSIVE_PERMISSION: "Consider withdrawing this evidenced Allow grant. "
    "Excessiveness requires workload-owner review; reachability alone does not establish it.",
    K.ROLE_TRUST: "Consider withdrawing this modeled assumption edge. "
    "The trust and caller policies are checked; whether trust is unnecessary remains unknown.",
    K.EXPIRED_ACCESS: "Review this active assumption after its modeled business justification "
    "expired or was resolved. Simulate withdrawing it; confirm business intent with its owner.",
    K.BROAD_RESOURCE: "Consider withdrawing this exact target from a wildcard-action or "
    "multi-resource Allow. This models target removal, not an invented replacement IAM policy.",
    K.POLICY_EXCEPTION: "Review a high-risk modeled path with verified policy-exception "
    "evidence. The exception's continuing business necessity requires human assessment.",
}
SIMULATION_LIMITATIONS = [
    "Approval concerns the recommendation only; there is no AWS executor or execution endpoint.",
    "Business necessity and legitimate usage are unknown without workload-owner confirmation. "
    "Access impact lists modeled possible access, including non-sensitive resources.",
    "Impact is scoped to the requested action, source timestamp, scenario and bounded role hops; "
    "other actions, tenants, sessions and unmodeled policies are not exhaustively evaluated.",
    "Removing a supporting edge can leave alternate access; zero remaining findings is not "
    "proof of safety or of a successful AWS deployment.",
    *LIMITATIONS,
]


def source_digest(bundle):
    return fingerprint({"records": graph_records(bundle), "scope_stats": bundle["scope_stats"]})


def risk_bundle(bundle, analysis):
    return {
        **bundle,
        "candidates": [
            p
            for p in bundle["candidates"]
            if p["nodes"][-1].get("sensitive") is True
            and (analysis.identity_id is None or p["nodes"][0]["id"] == analysis.identity_id)
        ],
    }


class RemediationService:
    def __init__(self, repository, *, human_reviewer_id=None):
        self.repository = repository
        self.human_reviewer_id = human_reviewer_id
        self.risk = RiskRepository(repository.database, repository.scope)
        self.engine = RiskEngine(self.risk)

    def require(self, *, write=False, human=False):
        access = self.repository.access()
        if write and access["role"] not in {"writer", "curator"}:
            raise RemediationAccessDenied()
        if human and (
            not self.human_reviewer_id
            or self.repository.actor_id != self.repository.agent_id(AgentKey.COORDINATOR)
        ):
            raise RemediationAccessDenied()

    def context(self, analysis, excluded=None):
        # Always evaluate collateral impact for all identities, not just the selected scout.
        options = analysis.model_copy(update={"identity_id": None})
        views = [view("before", analysis.at)]
        if excluded:
            views.append(view("after", analysis.at, excluded))
        return self.risk.load_views(options, views, include_non_sensitive=True)

    def _operation(self, key, digest, build, *, proposal=None):
        repo = self.repository
        for _ in range(5):
            self.require(write=True)
            existing = repo.operation(key, digest)
            if existing is not None:
                return existing
            stream = repo.stream()
            mutation = RemediationMutation(repo.scope.tenant_id, key, epoch_us(utc_now()))
            response = build(mutation)
            mutation.node(
                N.DECISION,
                key,
                {
                    "action": "remediation.operation",
                    "status": "applied",
                    "rationale": "Idempotent remediation metadata operation; no AWS action",
                    "space_id": repo.space_id,
                    "actor_agent_id": repo.actor_id,
                    "request_sha256": digest,
                    "response_json": json.dumps(response, sort_keys=True),
                },
                "operation",
            )
            try:
                if repo.commit(mutation, stream, proposal=proposal):
                    return response
            except GraphConflict:
                continue
            if proposal and repo.proposal(proposal["id"])["revision"] != proposal["revision"]:
                # Reconcile a lost acknowledgement before reporting a stale review.
                existing = repo.operation(key, digest)
                if existing is not None:
                    return existing
                raise RemediationConflict()
        raise RemediationConflict()

    def _target(self, bundle, relationship_id):
        records = graph_records(bundle)
        edge = records.get(relationship_id)
        if not edge or edge.get("kind") not in {"CAN_ASSUME", "HAS_POLICY", "GRANTS", "TARGETS"}:
            raise InvalidRemediation()
        if (
            edge.get("verification_status") != "verified"
            or edge["valid_from"] > bundle["at"]
            or (edge.get("valid_to") is not None and bundle["at"] >= edge["valid_to"])
            or edge.get("source_event_id") not in {e["id"] for e in bundle["events"]}
        ):
            raise InvalidRemediation()
        statements = [
            p
            for p in bundle["statements"]
            if p.get("active") and any(e["id"] == relationship_id for e in p["edges"])
        ]
        if edge["kind"] == "CAN_ASSUME":
            statements = [
                p
                for p in bundle["statements"]
                if p.get("active")
                and p["nodes"][0]["id"] == edge["target_node_id"]
                and p["nodes"][-1]["id"] == edge["source_node_id"]
                and p["nodes"][1].get("policy_type") == "TrustPolicy"
            ]
        if not statements or any(
            p["nodes"][2].get("effect") != "Allow"
            or p["nodes"][1].get("policy_type") not in {"IdentityPolicy", "TrustPolicy"}
            for p in statements
        ):
            raise InvalidRemediation()
        permissions = sorted({p["nodes"][2]["id"] for p in statements})
        evidence_records = {edge["id"]: edge}
        event = records[edge["source_event_id"]]
        evidence_records[event["id"]] = event
        for permission_id in permissions:
            evidence_records[permission_id] = records[permission_id]
        # Include exact subject snapshots and their provenance.
        for endpoint in (edge["source_node_id"], edge["target_node_id"]):
            evidence_records[endpoint] = records[endpoint]
        if len(evidence_records) > 15:
            raise RemediationLimit()
        return edge, permissions, evidence_records

    def propose(self, request: ProposeRequest, *, context=None):
        request = ProposeRequest.model_validate(request.model_dump())
        self.require(write=True)
        repo = self.repository
        key = repo.key("proposal", request.request_id)
        digest = fingerprint(request.model_dump(mode="json"))
        previous = repo.operation(key, digest)
        if previous is not None:
            return self.get(previous["proposal_id"])
        bundle = (context or self.context(request.analysis))["before"]
        edge, permission_ids, records = self._target(bundle, request.relationship_id)
        if request.category in {K.ROLE_TRUST, K.EXPIRED_ACCESS} and edge["kind"] != "CAN_ASSUME":
            raise InvalidRemediation()
        baseline = self.engine.evaluate(request.analysis, risk_bundle(bundle, request.analysis))
        findings = [f for f in baseline.findings if edge["id"] in f.supporting_relationship_ids]
        if not findings:
            raise InvalidRemediation()
        if request.category not in self._candidate_categories(bundle, baseline).get(
            edge["id"], set()
        ):
            raise InvalidRemediation()
        if request.category == K.EXPIRED_ACCESS:
            for record in bundle["justifications"]:
                if record["id"] == edge.get("justification_id") or record.get(
                    "logical_key"
                ) == edge.get("justification_logical_key"):
                    records[record["id"]] = record
        if request.category == K.POLICY_EXCEPTION:
            subjects = {n["id"] for f in findings for n in [f.identity, *f.roles, f.resource]}
            for proof in bundle["proofs"]:
                if (
                    proof["nodes"][-1]["id"] in subjects
                    and proof["nodes"][0].get("evidence_type") == "policy-exception"
                ):
                    for record in [*proof["nodes"], *proof["edges"]]:
                        records[record["id"]] = record

        def build(mutation):
            # Evidence copies and the proposal commit together; hashes preserve historic bytes.
            evidence = []
            proposal_id = identifier(repo.scope.tenant_id, key + "/proposal")
            for index, record in enumerate(records.values()):
                evidence_id = stable_id(
                    repo.scope.tenant_id,
                    N.EVIDENCE.value,
                    "",
                    mutation.provenance("unused").source,
                    key + "/source/" + str(index),
                )
                evidence.append(
                    {"id": evidence_id, "record": record, "content_sha256": fingerprint(record)}
                )
            if len(json.dumps(evidence)) > 15000:
                raise RemediationLimit()
            proposal = mutation.node(
                N.DECISION,
                key + "/proposal",
                {
                    "action": "remediation.proposal",
                    "status": "PROPOSED",
                    "revision": 0,
                    "category": request.category.value,
                    "space_id": repo.space_id,
                    "actor_agent_id": repo.actor_id,
                    "simulator_version": SIMULATOR_VERSION,
                    "analysis_request_json": request.analysis.model_dump_json(),
                    "relationship_json": json.dumps(edge, sort_keys=True),
                    "permission_ids_json": json.dumps(permission_ids),
                    "finding_ids_json": json.dumps([f.finding_id for f in findings]),
                    "evidence_json": json.dumps(evidence, sort_keys=True),
                    "source_sha256": source_digest(bundle),
                    "request_sha256": digest,
                    "requested_reason": request.reason,
                    "rationale": EXPLANATIONS[request.category],
                },
                "proposal",
            )
            mutation.changes.append("SET proposal:RemediationProposal ")
            for index, item in enumerate(evidence):
                variable = "evidence" + str(index)
                node = mutation.node(
                    N.EVIDENCE,
                    key + "/source/" + str(index),
                    {
                        "evidence_type": "remediation-source-snapshot",
                        "record_json": json.dumps(item["record"], sort_keys=True),
                        "content_sha256": item["content_sha256"],
                    },
                    variable,
                )
                mutation.edge(node, proposal, R.SUPPORTS, variable, "proposal")
            for index, finding in enumerate(findings):
                node = {"id": finding.finding_id, "kind": "RiskFinding"}
                mutation.bind(node, "finding" + str(index))
                mutation.edge(proposal, node, R.DERIVED_FROM, "proposal", "finding" + str(index))
            return {"proposal_id": proposal_id}

        result = self._operation(key, digest, build)
        return self.get(result["proposal_id"])

    def _candidate_categories(self, bundle, baseline):
        records, result = graph_records(bundle), {}
        for finding in baseline.findings:
            for edge_id in finding.relationship_ids:
                edge = records[edge_id]
                kinds = result.setdefault(edge_id, set())
                if edge["kind"] == "CAN_ASSUME":
                    kinds.add(K.ROLE_TRUST)
                    if any(
                        f.name == "temporary_access_violation"
                        and edge_id in f.record_ids
                        and f.points > 0
                        for f in finding.score.factors
                    ):
                        kinds.add(K.EXPIRED_ACCESS)
                if edge["kind"] == "GRANTS":
                    kinds.add(K.EXCESSIVE_PERMISSION)
                if (
                    edge["kind"] == "HAS_POLICY"
                    and records[edge["target_node_id"]].get("policy_type") == "IdentityPolicy"
                ):
                    kinds.add(K.EXCESSIVE_PERMISSION)
                if edge["kind"] == "TARGETS":
                    permission = records[finding.permission_id]
                    targets = {
                        p["nodes"][-1]["id"]
                        for p in bundle["statements"]
                        if p.get("active") and p["nodes"][2]["id"] == permission["id"]
                    }
                    if any(c in permission.get("action", "") for c in "*?") or len(targets) > 1:
                        kinds.add(K.BROAD_RESOURCE)
                relevant = {
                    finding.identity["id"],
                    finding.resource["id"],
                    *(r["id"] for r in finding.roles),
                }
                exception = any(
                    p["nodes"][0].get("evidence_type") == "policy-exception"
                    and p["nodes"][0].get("status") == "verified"
                    and p["nodes"][0].get("fact_value") is True
                    and p["nodes"][-1]["id"] in relevant
                    for p in bundle["proofs"]
                )
                if exception and finding.score.value >= 60 and edge["kind"] == "GRANTS":
                    kinds.add(K.POLICY_EXCEPTION)
        candidates = {}
        for key, value in result.items():
            if not value:
                continue
            try:
                self._target(bundle, key)
            except InvalidRemediation:
                # A mixed Allow/Deny policy attachment must not be recommended for removal.
                continue
            candidates[key] = value
        return candidates

    def recommend(self, request: RecommendRequest):
        request = RecommendRequest.model_validate(request.model_dump())
        self.require(write=True)
        repo = self.repository
        key, digest = (
            repo.key("recommend", request.request_id),
            fingerprint(request.model_dump(mode="json")),
        )
        existing = repo.operation(key, digest)
        if existing is None:
            context = self.context(request.analysis)
            baseline = self.engine.evaluate(
                request.analysis, risk_bundle(context["before"], request.analysis)
            )
            candidates = self._candidate_categories(context["before"], baseline)
            pairs = sorted((edge, kind) for edge, kinds in candidates.items() for kind in kinds)
            proposals = [
                self.propose(
                    ProposeRequest(
                        request_id=stable_id(key, edge, kind.value),
                        analysis=request.analysis,
                        relationship_id=edge,
                        category=kind,
                        reason="Graph-derived candidate for owner review",
                    ),
                    context=context,
                )
                for edge, kind in pairs[: request.limit]
            ]
            payload = {
                "proposal_ids": [p.proposal_id for p in proposals],
                "omitted": max(0, len(pairs) - request.limit),
            }
            existing = self._operation(key, digest, lambda _: payload)
        proposals = [self.get(p) for p in existing["proposal_ids"]]
        return Recommendations(
            proposals=proposals,
            available_categories=sorted({p.category for p in proposals}),
            omitted_candidate_count=existing["omitted"],
            limitations=SIMULATION_LIMITATIONS,
        )

    def get(self, proposal_id):
        self.require()
        repo = self.repository
        node = repo.proposal(proposal_id)
        reviews = repo.links(proposal_id, "RemediationReview")
        return Proposal(
            proposal_id=node["id"],
            category=node["category"],
            status=node["status"],
            revision=node["revision"],
            analysis=AnalysisRequest.model_validate_json(node["analysis_request_json"]),
            target_relationship=json.loads(node["relationship_json"]),
            permission_ids=json.loads(node["permission_ids_json"]),
            finding_ids=json.loads(node["finding_ids_json"]),
            evidence=json.loads(node["evidence_json"]),
            explanation=node["rationale"],
            requested_reason=node["requested_reason"],
            created_at=from_epoch_us(node["created_at"]),
            updated_at=from_epoch_us(node["updated_at"]),
            review_history=[
                {
                    "decision_id": r["id"],
                    "reviewer_id": r["reviewer_id"],
                    "created_at": from_epoch_us(r["created_at"]).isoformat(),
                    **json.loads(r["audit_json"]),
                }
                for r in reviews
            ],
            simulation_ids=[s["id"] for s in repo.links(proposal_id, "RemediationSimulation")],
        )

    def saved_simulation(self, simulation_id):
        self.require()
        return Simulation.model_validate(self.repository.saved_simulation(simulation_id))

    def simulate(self, proposal_id, request: SimulationRequest):
        request = SimulationRequest.model_validate(request.model_dump())
        self.require(write=True)
        repo = self.repository
        key = repo.key("simulation/" + proposal_id, request.request_id)
        digest = fingerprint({"proposal_id": proposal_id, **request.model_dump(mode="json")})
        existing = repo.operation(key, digest)
        if existing is not None:
            return self.saved_simulation(existing["simulation_id"])
        ids = sorted([proposal_id, *request.additional_proposal_ids])
        if len(ids) != len(set(ids)):
            raise InvalidRemediation()
        proposals = [repo.proposal(i) for i in ids]
        if any(p["status"] == "REJECTED" for p in proposals):
            raise RemediationConflict()
        first = proposals[0]
        if any(
            p["analysis_request_json"] != first["analysis_request_json"]
            or p["source_sha256"] != first["source_sha256"]
            for p in proposals
        ):
            raise RemediationConflict()
        analysis = AnalysisRequest.model_validate_json(first["analysis_request_json"])
        excluded = sorted({json.loads(p["relationship_json"])["id"] for p in proposals})
        bundles = self.context(analysis, excluded)
        if source_digest(bundles["before"]) != first["source_sha256"]:
            raise RemediationConflict()
        for edge_id in excluded:
            self._target(bundles["before"], edge_id)
        before = self.engine.evaluate(
            analysis, risk_bundle(bundles["before"], analysis), persist=False
        )
        after = self.engine.evaluate(
            analysis, risk_bundle(bundles["after"], analysis), excluded, persist=False
        )
        a, b = {f.path_id for f in before.findings}, {f.path_id for f in after.findings}
        all_options = analysis.model_copy(update={"identity_id": None})
        all_before = self.engine.evaluate(all_options, bundles["before"], persist=False)
        all_after = self.engine.evaluate(all_options, bundles["after"], excluded, persist=False)
        impacts = []
        groups = {(f.identity["id"], f.resource["id"]) for f in all_before.findings}
        for principal, resource in sorted(groups):
            old = [
                f
                for f in all_before.findings
                if f.identity["id"] == principal and f.resource["id"] == resource
            ]
            new = [
                f
                for f in all_after.findings
                if f.identity["id"] == principal and f.resource["id"] == resource
            ]
            removed = {f.path_id for f in old} - {f.path_id for f in new}
            if removed:
                impacts.append(
                    AccessImpact(
                        identity_id=principal,
                        resource_id=resource,
                        action=analysis.action,
                        resource_sensitive=old[0].resource.get("sensitive", False),
                        removed_path_ids=sorted(removed),
                        remaining_path_ids=sorted(f.path_id for f in new),
                        potential_access_lost=not new,
                    )
                )
        peers = {
            (f.identity["id"], f.resource["id"], f.action)
            for f in before.findings
            if f.path_id in a - b
        }
        alternate = [
            f.path_id
            for f in after.findings
            if (f.identity["id"], f.resource["id"], f.action) in peers
        ]
        evidence = {item["id"]: item for p in proposals for item in json.loads(p["evidence_json"])}
        records = graph_records(bundles["before"])
        nodes = [r for r in records.values() if "id" in r and "source_node_id" not in r]
        edges = [r for r in records.values() if "source_node_id" in r]
        old_scores, new_scores = (
            [f.score.value for f in before.findings],
            [f.score.value for f in after.findings],
        )
        delta = RiskDelta(
            before_path_count=len(a),
            after_path_count=len(b),
            before_maximum_score=max(old_scores, default=0),
            after_maximum_score=max(new_scores, default=0),
            before_total_score=sum(old_scores),
            after_total_score=sum(new_scores),
            path_count_change=len(b) - len(a),
            maximum_score_change=max(new_scores, default=0) - max(old_scores, default=0),
            total_score_change=sum(new_scores) - sum(old_scores),
        )
        simulation_id = identifier(repo.scope.tenant_id, key + "/simulation")
        result = Simulation(
            simulation_id=simulation_id,
            proposal_ids=ids,
            evaluated_at=analysis.at,
            excluded_relationship_ids=excluded,
            before=before,
            after=after,
            removed_path_ids=sorted(a - b),
            remaining_path_ids=sorted(b),
            added_path_ids=sorted(b - a),
            alternate_paths=sorted(alternate),
            access_impact=impacts,
            risk_difference=delta,
            historical_evidence_ids=sorted(evidence),
            supporting_evidence=[evidence[k] for k in sorted(evidence)],
            visualization={
                "nodes": [{"id": n["id"], "kind": n["kind"], "properties": n} for n in nodes],
                "relationships": [
                    {
                        "id": e["id"],
                        "kind": e["kind"],
                        "source": e["source_node_id"],
                        "target": e["target_node_id"],
                        "hypothetically_removed": e["id"] in excluded,
                        "properties": e,
                    }
                    for e in edges
                ],
                "before_path_ids": sorted(a),
                "after_path_ids": sorted(b),
                "coordinates_included": False,
            },
            explanation=f"Excluding {len(excluded)} exact modeled edges removes "
            f"{len(a - b)} potential paths; {len(b)} remain, including {len(alternate)} "
            "alternatives for affected identity/resource "
            "pairs. Business impact and effective AWS authorization remain uncertain.",
            limitations=SIMULATION_LIMITATIONS,
        )

        def build(mutation):
            simulation = mutation.node(
                N.DECISION,
                key + "/simulation",
                {
                    "action": "remediation.simulation",
                    "status": "recorded",
                    "rationale": "Read-only Cypher exclusions; cloud projection unchanged",
                    "space_id": repo.space_id,
                    "actor_agent_id": repo.actor_id,
                    "proposal_ids_json": json.dumps(ids),
                    "simulator_version": SIMULATOR_VERSION,
                    "result_sha256": fingerprint(result.model_dump(mode="json")),
                    "chunk_ids_json": "[]",
                },
                "simulation",
            )
            mutation.changes.append("SET simulation:RemediationSimulation ")
            chunks = repo.simulation_payload(mutation, result.model_dump(mode="json"))
            mutation.params["chunk_ids"] = json.dumps(chunks)
            mutation.changes.append("SET simulation.chunk_ids_json = $chunk_ids ")
            for index, proposal in enumerate(proposals):
                mutation.bind(proposal, "p" + str(index))
                mutation.edge(simulation, proposal, R.DERIVED_FROM, "simulation", "p" + str(index))
            for index, item in enumerate(evidence.values()):
                node = {"id": item["id"], "kind": "Evidence"}
                mutation.bind(node, "proof" + str(index))
                mutation.edge(node, simulation, R.SUPPORTS, "proof" + str(index), "simulation")
            return {"simulation_id": simulation_id}

        result_id = self._operation(key, digest, build)["simulation_id"]
        return self.saved_simulation(result_id)

    def review(self, proposal_id, request: ProposalReview):
        request = ProposalReview.model_validate(request.model_dump())
        self.require(write=True, human=True)
        repo = self.repository
        key = repo.key("review/" + proposal_id, request.request_id)
        digest = fingerprint(request.model_dump(mode="json"))
        duplicate = repo.operation(key, digest)
        if duplicate is not None:
            return self.get(proposal_id)
        proposal = repo.proposal(proposal_id)
        allowed = {"PROPOSED": {"UNDER_REVIEW"}, "UNDER_REVIEW": {"APPROVED", "REJECTED"}}
        if (
            request.status.value not in allowed.get(proposal["status"], set())
            or request.expected_revision != proposal["revision"]
        ):
            raise RemediationConflict()
        if request.status == ProposalStatus.APPROVED:
            if not request.simulation_id:
                raise RemediationConflict()
            simulation = self.saved_simulation(request.simulation_id)
            if simulation.proposal_ids != [proposal_id]:
                raise RemediationConflict()
            # Approval is bound to the stored result; never silently substitute a newer source.
            analysis = AnalysisRequest.model_validate_json(proposal["analysis_request_json"])
            if source_digest(self.context(analysis)["before"]) != proposal["source_sha256"]:
                raise RemediationConflict()

        def build(mutation):
            review = mutation.node(
                N.DECISION,
                key + "/review",
                {
                    "action": "remediation.review",
                    "status": request.status.value,
                    "rationale": request.reason,
                    "space_id": repo.space_id,
                    "actor_agent_id": repo.actor_id,
                    "reviewer_id": self.human_reviewer_id,
                    "proposal_id": proposal_id,
                    "audit_json": json.dumps(
                        {
                            **request.model_dump(mode="json"),
                            "approval_scope": "recommendation_only",
                            "execution_allowed": False,
                        }
                    ),
                },
                "review",
            )
            mutation.changes.append("SET review:RemediationReview ")
            mutation.edge(review, proposal, R.DERIVED_FROM, "review", "proposal")
            mutation.params.update(status=request.status.value, revision=proposal["revision"] + 1)
            mutation.changes.append(
                "SET proposal.status = $status, proposal.revision = $revision, "
                "proposal.updated_at = $now "
            )
            return {"proposal_id": proposal_id}

        self._operation(key, digest, build, proposal=proposal)
        return self.get(proposal_id)
