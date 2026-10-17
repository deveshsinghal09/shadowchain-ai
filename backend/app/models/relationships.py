"""Permitted directions; validates domain structure before any database mutation."""

from app.models.graph import CLOUD_KINDS, RelationshipInput
from app.models.graph import NodeKind as N
from app.models.graph import RelationshipKind as R

ALL = frozenset(N)
ALLOWED_ENDPOINTS = {
    R.BELONGS_TO: (
        ALL,
        frozenset(
            {N.CLOUD_ACCOUNT, N.CLOUD_ORGANIZATION, N.INVESTIGATION, N.AGENT, N.MEMORY_SPACE}
        ),
    ),
    R.CAN_ASSUME: (frozenset({N.IDENTITY, N.ROLE}), frozenset({N.ROLE})),
    R.HAS_POLICY: (
        frozenset({N.IDENTITY, N.ROLE, N.RESOURCE, N.CLOUD_ACCOUNT, N.CLOUD_ORGANIZATION}),
        frozenset({N.POLICY}),
    ),
    R.GRANTS: (frozenset({N.POLICY}), frozenset({N.PERMISSION})),
    R.TARGETS: (frozenset({N.PERMISSION}), frozenset({N.RESOURCE, N.ROLE, N.IDENTITY})),
    R.AFFECTS: (frozenset({N.SECURITY_EVENT, N.RISK_FINDING}), CLOUD_KINDS),
    R.DERIVED_FROM: (
        frozenset({N.EVIDENCE, N.RISK_FINDING, N.AGENT_MEMORY, N.DECISION}),
        frozenset(
            {
                N.SECURITY_EVENT,
                N.EVIDENCE,
                N.AGENT_MEMORY,
                N.RISK_FINDING,
                N.DECISION,
                N.AGENT_RUN,
                N.INVESTIGATION,
                N.PROCEDURE,
            }
        ),
    ),
    R.SUPPORTS: (
        frozenset({N.EVIDENCE, N.AGENT_MEMORY, N.DECISION}),
        frozenset({N.RISK_FINDING, N.INVESTIGATION, N.DECISION, N.RESOURCE, N.IDENTITY, N.ROLE}),
    ),
    R.CONTRADICTS: (
        frozenset({N.EVIDENCE, N.AGENT_MEMORY, N.DECISION}),
        frozenset({N.RISK_FINDING, N.EVIDENCE, N.AGENT_MEMORY, N.DECISION, N.INVESTIGATION}),
    ),
    R.CAUSED_BY: (
        frozenset({N.SECURITY_EVENT, N.RISK_FINDING, N.DECISION, N.TASK, N.HANDOFF}),
        frozenset({N.SECURITY_EVENT, N.DECISION, N.AGENT_RUN}),
    ),
    R.INVESTIGATES: (
        frozenset({N.INVESTIGATION, N.AGENT, N.AGENT_RUN}),
        frozenset(
            {N.RISK_FINDING, N.SECURITY_EVENT, N.IDENTITY, N.ROLE, N.RESOURCE, N.INVESTIGATION}
        ),
    ),
    R.REMEMBERS: (frozenset({N.AGENT, N.AGENT_RUN, N.INVESTIGATION}), frozenset({N.AGENT_MEMORY})),
    R.ASSIGNED_TO: (frozenset({N.TASK, N.INVESTIGATION}), frozenset({N.AGENT})),
    R.DEPENDS_ON: (frozenset({N.TASK}), frozenset({N.TASK})),
    R.HANDS_OFF_TO: (frozenset({N.AGENT, N.TASK, N.HANDOFF}), frozenset({N.AGENT, N.HANDOFF})),
    R.USES_PROCEDURE: (frozenset({N.AGENT_RUN, N.TASK, N.DECISION}), frozenset({N.PROCEDURE})),
    R.SUPERSEDES: (ALL, ALL),
    R.MEMBER_OF: (frozenset({N.AGENT}), frozenset({N.MEMORY_SPACE})),
    R.HAS_CONTEXT: (frozenset({N.MEMORY_SPACE}), ALL - {N.MEMORY_SPACE}),
    R.ABOUT: (frozenset({N.AGENT_MEMORY}), ALL - {N.MEMORY_SPACE, N.AGENT_MEMORY}),
    R.INCLUDES_MEMORY: (frozenset({N.HANDOFF}), frozenset({N.AGENT_MEMORY})),
}


def validate_endpoints(edge: RelationshipInput) -> None:
    sources, targets = ALLOWED_ENDPOINTS[edge.kind]
    if edge.source.kind not in sources or edge.target.kind not in targets:
        raise ValueError("Invalid endpoint kinds for this relationship")
    if edge.kind == R.SUPERSEDES and edge.source.kind != edge.target.kind:
        raise ValueError("SUPERSEDES must connect records of the same kind")
    if edge.source == edge.target:
        raise ValueError("Self relationships are not permitted")
