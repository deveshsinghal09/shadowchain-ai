"""Deterministic assessment of paths enumerated by FalkorDB, never Python DFS or an LLM."""

from datetime import datetime

from app.db.cypher import fingerprint, stable_id
from app.models.graph import from_epoch_us
from app.repositories.risk import RiskRepository
from app.schemas.risk import (
    ENGINE_VERSION,
    AnalysisRequest,
    ContributingEvent,
    DifferenceRequest,
    DiscoveryResult,
    PathFinding,
    PathIntroduction,
    RejectedCandidate,
    RemediationResult,
    RiskDifference,
    RiskOptions,
    RiskScore,
    RiskTimeline,
    ScoreFactor,
    TimelinePoint,
    TimelineRequest,
    WhatIfRequest,
)
from app.services.authorization import PolicyEvaluator, action_matches, condition_result
from app.services.risk_errors import RiskHistoryChanged, RiskLimitExceeded

LIMITATIONS = [
    "POTENTIAL ATTACK PATHS are modeled possibilities, not proven exploitable AWS access.",
    "Only normalized identity/trust/resource policies, boundary/session policies and attached "
    "SCP/RCP constraints are evaluated. Missing unmodeled AWS constraints remain unknown.",
    "Candidates require an identity-policy grant. Resource-policy-only grants, session-principal "
    "exceptions, IAM groups, NotAction/NotResource, ABAC, service-specific semantics, KMS and "
    "S3 ownership/public-access-block evaluation are not implemented.",
    "Unknown applicable conditions or required constraints exclude a candidate as unresolved; "
    "zero findings does not certify safety. User-supplied context is a hypothetical assumption.",
    "Traversal is bounded to the requested role hops (maximum four); IAM evaluation is per hop. "
    "STS sessions, transitive session tags and session-policy propagation are not simulated.",
    "Scores are explainable prioritization heuristics, not exploitation probabilities.",
    "Account/organization controls are resolved through at most two membership hops; "
    "deeper modeled hierarchy is detected and rejected as unresolved.",
]


def view(key: str, at: datetime, excluded: list[str] | None = None) -> dict:
    from app.models.graph import epoch_us

    return {"key": key, "at": epoch_us(at), "excluded": sorted(excluded or [])}


def graph_records(bundle: dict) -> dict[str, dict]:
    records = {}
    for collection in ("candidates", "lineages", "attachments", "statements", "proofs"):
        for path in bundle[collection]:
            for record in [*path["nodes"], *path["edges"]]:
                records[record["id"]] = dict(record)
    for record in [*bundle["events"], *bundle["justifications"]]:
        # Event content hash identifies its normalized payload; the complete event
        # stays in FalkorDB. Avoid duplicating potentially large payload strings.
        records[record["id"]] = {k: v for k, v in record.items() if k != "payload"}
    return dict(sorted(records.items()))


def event_provenance(
    bundle: dict,
    relationship_ids: set[str],
    closed_scope: set[str],
) -> list[ContributingEvent]:
    edges = {
        edge["id"]: edge
        for collection in ("candidates", "statements", "lineages", "proofs")
        for path in bundle[collection]
        for edge in path["edges"]
    }
    result = []
    for event in bundle["events"]:
        affected = sorted(
            identifier
            for identifier in relationship_ids
            if edges.get(identifier, {}).get("source_event_id") == event["id"]
        )
        closed = sorted(
            edge_id
            for edge_id, edge in edges.items()
            if edge.get("closed_by_event_id") == event["id"] and edge_id in closed_scope
        )
        if affected or closed:
            result.append(
                ContributingEvent(
                    id=event["id"],
                    source_id=event["source_id"],
                    event_type=event["event_type"],
                    occurred_at=from_epoch_us(event["event_time"]),
                    relationship_ids=affected,
                    closed_relationship_ids=closed,
                )
            )
    return sorted(result, key=lambda e: (e.occurred_at, e.id))


def risk_score(
    chain: list[dict],
    chain_edges: list[dict],
    resource: dict,
    permission: dict,
    bundle: dict,
    mfa_permission_ids: set[str],
) -> RiskScore:
    factors = []

    def factor(name: str, points: int, explanation: str, record_ids: list[str]) -> None:
        factors.append(
            ScoreFactor(
                name=name,
                points=points,
                explanation=explanation,
                record_ids=sorted(set(record_ids)),
            )
        )

    sensitivity = (
        40
        if resource.get("data_classification") == "restricted"
        else 30
        if resource.get("sensitive")
        else 0
    )
    factor(
        "resource_sensitivity",
        sensitivity,
        "Sensitive resource; restricted classification adds 10",
        [resource["id"]],
    )
    pattern = permission.get("action", "")
    if pattern.lower().startswith(("iam:", "organizations:")):
        privilege, detail = 30, "IAM/organization administration capability"
    elif "*" in pattern or "?" in pattern:
        privilege, detail = 25, "Wildcard action grant has broad privilege scope"
    elif any(word in pattern.lower() for word in ("delete", "put", "write", "update", "create")):
        privilege, detail = 20, "Modeled action can modify or delete data/configuration"
    else:
        privilege, detail = 10, "Modeled data-read or other scoped action"
    factor("privilege", privilege, detail, [permission["id"]])
    cross_account = sum(
        a.get("account_id") != b.get("account_id") for a, b in zip(chain, chain[1:], strict=False)
    )
    trust = min(12, 3 * len(chain_edges)) + min(10, 5 * cross_account)
    factor(
        "trust_relationships",
        trust,
        "3 per assumption hop (cap 12), 5 per cross-account hop (cap 10)",
        [e["id"] for e in chain_edges],
    )
    relevant = {n["id"] for n in chain} | {resource["id"]}
    verified = [
        proof["nodes"][0]
        for proof in bundle["proofs"]
        if proof["nodes"][1]["id"] in relevant
        and proof["nodes"][0].get("status") == "verified"
        and proof["nodes"][0].get("fact_value") is True
    ]
    exposure = [e for e in verified if e.get("evidence_type") == "internet-exposure"]
    factor(
        "internet_exposure",
        15 if exposure else 0,
        "Verified structured exposure evidence"
        if exposure
        else "No verified structured internet-exposure evidence; no exposure assumed",
        [e["id"] for e in exposure],
    )
    current = {j.get("logical_key", j["id"]): j for j in bundle["justifications"]}
    stale = [
        e
        for e in chain_edges
        if e.get("justification_id")
        and (
            e.get("justification_expires_at", bundle["at"] + 1) <= bundle["at"]
            or current.get(e.get("justification_logical_key"), {}).get("status")
            in {"resolved", "closed"}
        )
    ]
    factor(
        "temporary_access_violation",
        12 if stale else 0,
        "Active grant has expired/resolved business justification"
        if stale
        else "No evidenced temporary-access violation on this path",
        [e["id"] for e in stale],
    )
    controls = [
        e
        for e in verified
        if e.get("evidence_type") == "compensating-control"
        and e.get("control_type") in {"monitoring", "private-endpoint"}
    ]
    reduction = min(
        15,
        sum(
            {"monitoring": 5, "private-endpoint": 10}[kind]
            for kind in {e["control_type"] for e in controls}
        ),
    )
    if mfa_permission_ids:
        reduction = min(20, reduction + 10)
    factor(
        "compensating_controls",
        -reduction,
        "Monitoring -5, private endpoint -10, required matched MFA -10; reduction capped at 20. "
        "Controls do not themselves authorize access.",
        [e["id"] for e in controls] + sorted(mfa_permission_ids),
    )
    total = max(0, min(100, sum(f.points for f in factors)))
    severity = (
        "critical" if total >= 80 else "high" if total >= 60 else "medium" if total >= 40 else "low"
    )
    return RiskScore(value=total, severity=severity, factors=factors)


class RiskEngine:
    def __init__(self, repository: RiskRepository) -> None:
        self.repository = repository

    def evaluate(
        self,
        options: RiskOptions,
        bundle: dict,
        excluded: list[str] | None = None,
        *,
        persist: bool = True,
    ) -> DiscoveryResult:
        records = graph_records(bundle)
        records[stable_id(self.repository.scope.tenant_id, "risk-scope-stats")] = {
            "kind": "RiskGraphScope",
            "tenant_id": self.repository.scope.tenant_id,
            **bundle["scope_stats"],
        }
        request_record = {
            "kind": "RiskAnalysisRequest",
            "engine_version": ENGINE_VERSION,
            "options": options.model_dump(
                mode="json", exclude={"at", "remove_relationship_ids", "before", "after"}
            ),
            "at": bundle["at"],
            "excluded_relationship_ids": sorted(excluded or []),
        }
        digest = fingerprint({"request": request_record, "records": records})
        analysis_id = stable_id(self.repository.scope.tenant_id, "risk-analysis", digest)
        records[stable_id(analysis_id, "request")] = request_record
        evaluator = PolicyEvaluator(bundle, options.context)
        findings, rejected = {}, {}
        available_events = {e["id"] for e in bundle["events"]}
        for candidate in sorted(bundle["candidates"], key=fingerprint):
            nodes, edges = candidate["nodes"], candidate["edges"]
            chain, chain_edges = nodes[:-3], edges[:-3]
            identity, principal, policy, permission, resource = (
                nodes[0],
                nodes[-4],
                nodes[-3],
                nodes[-2],
                nodes[-1],
            )
            if (
                permission.get("effect") != "Allow"
                or policy.get("policy_type") != "IdentityPolicy"
                or not action_matches(permission.get("action", ""), options.action)
            ):
                continue
            if len({n["id"] for n in chain}) != len(chain) or any(
                n["kind"] != "Role" for n in chain[1:]
            ):
                continue
            reason, unresolved = None, False
            supported, assumptions, mfa_permission_ids = {e["id"] for e in edges}, [], set()
            for caller, role in zip(chain, chain[1:], strict=False):
                decision = evaluator.evaluate(caller, role, "sts:AssumeRole", assume_role=True)
                if not decision.accepted:
                    reason, unresolved = decision.reason, decision.unresolved
                    break
                supported.update(decision.supporting_ids)
                assumptions.extend(decision.assumptions)
                mfa_permission_ids.update(decision.mfa_permission_ids)
            if reason is None:
                decision = evaluator.evaluate(principal, resource, options.action)
                if not decision.accepted:
                    reason, unresolved = decision.reason, decision.unresolved
                else:
                    supported.update(decision.supporting_ids)
                    assumptions.extend(decision.assumptions)
                    mfa_permission_ids.update(decision.mfa_permission_ids)
            terminal_condition = condition_result(
                permission, evaluator.condition_context(principal)
            )
            if reason is None and terminal_condition is not True:
                reason, unresolved = (
                    "terminal grant condition is not established",
                    terminal_condition is None,
                )
            positive_edges = [records[key] for key in supported]
            if reason is None and any(
                e.get("source_event_id") not in available_events for e in positive_edges
            ):
                reason, unresolved = (
                    "supporting grant source event is missing or outside scope",
                    True,
                )
            if reason:
                key = (identity["id"], resource["id"], reason, unresolved)
                rejected[key] = RejectedCandidate(
                    identity_id=identity["id"],
                    resource_id=resource["id"],
                    reason=reason,
                    unresolved=unresolved,
                )
                continue
            path_id = stable_id(
                self.repository.scope.tenant_id,
                "potential-path",
                options.action,
                *[n["id"] for n in chain],
                resource["id"],
                permission["id"],
                *[e["id"] for e in edges],
            )
            principals = {n["id"] for n in chain}
            owners = principals | {resource["id"]}
            for node_id in list(owners):
                owners.update(evaluator.ancestors(node_id))
            destinations = principals | {resource["id"]}
            closed_scope = {
                edge["id"]
                for statement in bundle["statements"]
                if statement["nodes"][0]["id"] in owners
                and statement["nodes"][-1]["id"] in destinations
                and any(
                    action_matches(statement["nodes"][2].get("action", ""), action)
                    for action in (options.action, "sts:AssumeRole")
                )
                for edge in statement["edges"]
            }
            closed_scope.update(
                edge["id"]
                for attachment in bundle["attachments"]
                if attachment["nodes"][0]["id"] in owners
                and attachment["nodes"][1].get("policy_type")
                in {
                    "PermissionsBoundary",
                    "SessionPolicy",
                    "ServiceControlPolicy",
                    "ResourceControlPolicy",
                }
                for edge in attachment["edges"]
            )
            explanation = (
                f"{identity.get('display_name', identity['id'])} can potentially reach "
                f"{resource.get('display_name', resource['id'])} for {options.action} through "
                f"{len(chain_edges)} modeled role assumption(s). Caller grants, role trust and "
                "modeled constraints were checked; this is not proof of exploitable AWS access."
            )
            findings[path_id] = PathFinding(
                finding_id=self.repository.finding_id(analysis_id, path_id),
                path_id=path_id,
                identity=identity,
                roles=chain[1:],
                resource=resource,
                action=options.action,
                permission_id=permission["id"],
                relationship_ids=[e["id"] for e in edges],
                supporting_relationship_ids=sorted(supported),
                contributing_events=event_provenance(bundle, supported, closed_scope),
                score=risk_score(
                    chain, chain_edges, resource, permission, bundle, mfa_permission_ids
                ),
                explanation=explanation,
                assumptions=sorted(
                    set(assumptions + ["Unmodeled AWS constraints may prevent access"])
                ),
            )
        if len(findings) > 50 or len(findings) * len(records) > 5000:
            raise RiskLimitExceeded()
        result = DiscoveryResult(
            analysis_id=analysis_id,
            at=from_epoch_us(bundle["at"]),
            scenario=options.scenario,
            action=options.action,
            max_role_hops=options.max_role_hops,
            context_sha256=digest,
            findings=[findings[key] for key in sorted(findings)],
            rejected_candidates=[rejected[key] for key in sorted(rejected)],
            limitations=LIMITATIONS,
        )
        return self.repository.persist(result, records) if persist else result

    def discover(self, request: AnalysisRequest) -> DiscoveryResult:
        bundles = self.repository.load_views(request, [view("at", request.at)])
        return self.evaluate(request, bundles["at"])

    def time_views(self, request: DifferenceRequest) -> tuple[list[dict], list[dict]]:
        events = self.repository.event_history(request, request.before, request.after)
        stamps = sorted(
            {request.before, request.after, *[from_epoch_us(e["event_time"]) for e in events]}
        )
        return [view(str(index), stamp) for index, stamp in enumerate(stamps)], events

    def difference(self, request: DifferenceRequest) -> RiskDifference:
        views, events, bundles = self.temporal_data(request)
        results = [self.evaluate(request, bundles[v["key"]]) for v in views]
        first, last = results[0], results[-1]
        before_ids, after_ids = (
            {f.path_id for f in first.findings},
            {f.path_id for f in last.findings},
        )
        added = after_ids - before_ids
        introductions = []
        for path_id in sorted(added):
            reached = next(r for r in results if any(f.path_id == path_id for f in r.findings))
            finding = next(f for f in reached.findings if f.path_id == path_id)
            transitions = []
            for event in events:
                if from_epoch_us(event["event_time"]) == reached.at:
                    source = next(
                        (e for e in finding.contributing_events if e.id == event["id"]), None
                    )
                    if source is not None:
                        # Coincident timestamps alone cannot attribute an event.
                        # Require linked creation/closure provenance for this path.
                        transitions.append(source)
            introductions.append(
                PathIntroduction(
                    path_id=path_id,
                    first_reachable_at=reached.at,
                    transition_events=transitions,
                    contributing_events=finding.contributing_events,
                )
            )
        return RiskDifference(
            before=first,
            after=last,
            added_path_ids=sorted(added),
            removed_path_ids=sorted(before_ids - after_ids),
            unchanged_path_ids=sorted(before_ids & after_ids),
            introductions=introductions,
        )

    def timeline(self, request: TimelineRequest) -> RiskTimeline:
        views, _, bundles = self.temporal_data(request)
        results = [self.evaluate(request, bundles[v["key"]]) for v in views]
        return RiskTimeline(
            points=[
                TimelinePoint(
                    at=r.at,
                    analysis_id=r.analysis_id,
                    path_count=len(r.findings),
                    maximum_score=max((f.score.value for f in r.findings), default=0),
                    path_ids=[f.path_id for f in r.findings],
                )
                for r in results
            ]
        )

    def temporal_data(self, request: DifferenceRequest) -> tuple[list[dict], list[dict], dict]:
        for _ in range(3):
            views, events = self.time_views(request)
            bundles = self.repository.load_views(request, views)
            count_before = bundles[views[0]["key"]]["scope_stats"]["source_event_count"]
            count_after = bundles[views[-1]["key"]]["scope_stats"]["source_event_count"]
            if count_after - count_before == len(events):
                return views, events, bundles
        raise RiskHistoryChanged()

    def what_if(self, request: WhatIfRequest) -> RemediationResult:
        self.repository.validate_exclusions(request.remove_relationship_ids, request)
        bundles = self.repository.load_views(
            request,
            [
                view("before", request.at),
                view("after", request.at, request.remove_relationship_ids),
            ],
        )
        before = self.evaluate(request, bundles["before"], persist=False)
        after = self.evaluate(
            request, bundles["after"], request.remove_relationship_ids, persist=False
        )
        a, b = {f.path_id for f in before.findings}, {f.path_id for f in after.findings}
        return RemediationResult(
            excluded_relationship_ids=sorted(request.remove_relationship_ids),
            before=before,
            after=after,
            removed_path_ids=sorted(a - b),
            added_path_ids=sorted(b - a),
            unchanged_path_ids=sorted(a & b),
        )
