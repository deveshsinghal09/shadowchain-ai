# Contributing to ShadowChain AI

The project is maintained collaboratively by
[Devesh Singhal](https://github.com/deveshsinghal09),
[Mayank Gupta](https://github.com/Mayank2142), and
[Devesh Raj](https://github.com/DEVESH859).

## Make a change

1. Describe the problem and the supported behavior your change will add.
2. Use a focused branch, preserving existing source and unrelated local work.
3. Keep FalkorDB authoritative for domain state, evidence, memory and coordination.
4. Run the checks appropriate to the changed code. Graph behavior needs real
   FalkorDB verification, and provider mocks must be labeled as mocks.
5. Summarize what changed, how it was verified, and any remaining limitations.

Use the root README and [local development guide](docs/LOCAL_DEVELOPMENT.md) for
setup. Use synthetic fixtures only where they are explicitly identified; do not
present them as real imports or production monitoring.

## Commits and shared credit

Keep commits focused on coherent changes, with messages explaining their purpose.
Use the author's verified Git identity or the GitHub-provided no-reply address.
The person performing a commit is the committer; the author records who wrote
the change. These are separate from the account authenticating a push.

For jointly authored work, add an agreed trailer for each co-author:

```text
Co-authored-by: Teammate Name <verified-email-or-github-noreply-address>
```

Confirm author details and contribution areas with the team. Contributor status
is derived by GitHub from commit history and its attribution rules; a collaborator
invitation grants repository access but does not establish authorship. Keep real
commit timestamps and group work by its actual scope rather than a contribution
counter.

## Files to keep private

Do not add `.env`, `.env.local`, tokens, API keys, private database data, virtual
environments, installed dependencies, browser authentication state, or test
artifacts containing secrets. `.env.example` may contain placeholders only.
Inspect the exact staged files before committing or publishing.

## Attribution and licensing

Preserve the root MIT license for original project code. Third-party source,
dependencies, images and fonts retain their original terms and notices. Update
[backend attribution](docs/attribution.md) or
[GraphVerse attribution](graphverse/docs/THIRD_PARTY.md) when introducing a new
external foundation. Describe actual development dates and verification outcomes;
do not infer hackathon eligibility from a commit timestamp.
