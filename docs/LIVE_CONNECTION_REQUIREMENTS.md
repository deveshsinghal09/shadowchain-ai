# Live website connection requirements

Reviewed against the implementation on 2026-10-17.

GraphVerse currently reads a separate synthetic FalkorDB graph. The Python backend has bounded, explicitly triggered AWS IAM and CloudTrail readers, but GraphVerse does not yet consume those imports. Its browser assistant is deterministic; the optional Python Gemini provider is not yet connected to the GraphVerse question interface. Configuring credentials alone does not complete those integrations.

Amazon Bedrock is not a dependency and is not required.

## What the operator must supply

| Capability | Configuration required | Where it belongs |
| --- | --- | --- |
| Real AWS inventory | An authorized read-only AWS CLI profile, expected AWS account ID, region | Existing AWS credential provider chain; profile/account/region settings in the server environment |
| Real CloudTrail events | The same authorized profile with `cloudtrail:LookupEvents`, plus an explicitly selected region | Server environment; separately enabled and user-triggered |
| Existing non-AWS graph | FalkorDB connection details, graph name and the graph's schema/tenant model | Server-only configuration; schema adapter must match the actual graph |
| Local FalkorDB | Existing local password and persistent server | Already configured; no new cloud account is required for local operation |
| Optional Gemini explanations | A Gemini API key for an authorized Google AI project | Server-only `GEMINI_API_KEY`; never a `VITE_*` variable or browser request |
| Public website | A chosen hosting environment and a stable HTTPS address; optional custom domain | Hosting configuration; authentication and deployment work are still required |

Do not send AWS secrets, session tokens, Gemini keys, or database passwords in chat. AWS profile name and region are enough to discuss the setup. Account scope is configured locally and explicitly confirmed before any live synchronization.

For an existing IAM Identity Center setup, AWS CLI SSO can supply credentials without placing long-lived access keys into the application. The application needs only the read operations listed in `AWS_SETUP.md`; administrative or IAM write access is not required.

## Implementation work still needed for a live GraphVerse website

1. Select the real source and implement the correct schema adapter rather than copying the synthetic node format over it.
2. Connect live inventory, provenance, refresh status and actual evidence to the explorer. Show honest empty/error states when no source is configured; do not seed sample records as a fallback.
3. Connect optional Gemini explanations to bounded graph results with validated citations. Graph traversal remains authoritative in FalkorDB.
4. Replace the local shared operator-token experience with the intended user authentication and tenant access model for public use.
5. Configure and verify HTTPS hosting, persistent database operation, backups, resource limits and an end-to-end live read-only import.

Real AWS IAM inventory does not automatically establish effective access. Unsupported conditions, trust requirements, boundaries and organizational constraints must remain explicit limitations. Existing AWS controls are development-only; enabling public production routes requires a reviewed service authorization design, not simply removing their environment checks.

## References

- Repository-specific permissions and settings: [AWS_SETUP.md](AWS_SETUP.md), [aws-readonly-policy.json](aws-readonly-policy.json).
- [AWS CLI IAM Identity Center setup](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).
- [Gemini API key setup](https://ai.google.dev/gemini-api/docs/api-key).

No live AWS request, new provider credential configuration, infrastructure provisioning or public deployment was performed during this requirements review.
