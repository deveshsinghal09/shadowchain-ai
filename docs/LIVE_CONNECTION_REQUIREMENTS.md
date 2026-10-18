# Live website connections

The hosted explorer is connected to the public `deveshsinghal09/shadowchain-ai`
repository. It uses read-only public GitHub requests and the private Railway
FalkorDB instance. AWS, CloudTrail, and Amazon Bedrock are disabled and are not
required for this deployment.

For configuration, coverage, endpoint contracts, and actual verification, see
[GitHub integration](GITHUB_INTEGRATION.md) and
[Railway/Vercel deployment](RAILWAY_VERCEL_DEPLOYMENT.md).

Public browsing requires no credentials. Operator-only synchronization, saved
investigations, proposal review, and optional Gemini explanations use the hosted
operator token. An optional GitHub read token can increase upstream request limits;
it is not required for the current public source. Gemini needs a valid server-side
key, supported model, and usable account quota. Never place these keys in browser
settings or commit them.

The live repository workflow is implemented in the explorer API. The Python
IAM/LangGraph workflows are a separate supported local capability; the original
schema adapter and production workflow restrictions remain explicit. Do not
switch production to development mode to bypass them.

There is no scheduled GitHub synchronization, webhook, automatic remediation,
cloud monitoring, or complete effective-permission evaluation. Source refresh is
operator-triggered and its coverage limits are visible in the website.
