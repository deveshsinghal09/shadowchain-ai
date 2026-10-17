"""Synthetic local test identity; never accepted by default runtime settings."""

import hashlib

TEST_TOKEN = "synthetic-test-token-not-a-runtime-credential-2026"  # noqa: S105 - test only
TEST_HEADERS = {"Authorization": "Bearer " + TEST_TOKEN}
TEST_CLIENT = {
    "principal_id": "test-operator",
    "tenant_id": "shadowchain-synthetic-demo",
    "token_sha256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
    "permissions": ["read", "write", "review", "aws:sync"],
}
