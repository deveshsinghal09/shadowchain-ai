"""Lazy, explicitly bounded AWS calls. This is the only AWS SDK dispatch boundary."""

import logging
from time import monotonic, sleep

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.services.aws_errors import AwsDisabled, AwsImportLimit, AwsOperationError, AwsScopeError

ALLOWED_OPERATIONS = {
    "sts": frozenset({"get_caller_identity"}),
    "iam": frozenset(
        {
            "list_roles",
            "get_role",
            "list_attached_role_policies",
            "list_role_policies",
            "get_role_policy",
            "get_policy",
            "list_policy_versions",
            "get_policy_version",
        }
    ),
    "cloudtrail": frozenset({"lookup_events"}),
    "s3": frozenset(
        {
            "get_public_access_block",
            "get_bucket_policy_status",
            "get_bucket_acl",
            "head_object",
            "put_object",
        }
    ),
}


class AwsGateway:
    def __init__(self, settings, *, session_factory=None, sleeper=sleep):
        self.settings, self.session_factory, self.sleeper = settings, session_factory, sleeper
        self._clients = {}
        self._last_lookup = None
        self._call_count = 0

    def require(self, account):
        if not self.settings.aws_enabled:
            raise AwsDisabled()
        if account != self.settings.aws_account_id:
            raise AwsScopeError()

    def call(self, service, operation, **kwargs):
        if not self.settings.aws_enabled:
            raise AwsDisabled()
        if operation not in ALLOWED_OPERATIONS.get(service, frozenset()):
            raise AwsDisabled()
        if service == "cloudtrail" and not self.settings.cloudtrail_enabled:
            raise AwsDisabled()
        if service == "s3" and self.settings.export_mode != "s3":
            raise AwsDisabled()
        if service == "s3" and (
            kwargs.get("Bucket") != self.settings.s3_export_bucket
            or kwargs.get("ExpectedBucketOwner") != self.settings.aws_account_id
        ):
            raise AwsScopeError()
        self._call_count += 1
        if self._call_count > self.settings.aws_max_records:
            raise AwsImportLimit()
        try:
            if service not in self._clients:
                # Suppress SDK wire/debug logs even when the application runs at DEBUG.
                for name in ("boto3", "botocore", "s3transfer", "urllib3"):
                    logging.getLogger(name).setLevel(logging.WARNING)
                factory = self.session_factory
                if factory is None:
                    import boto3

                    factory = boto3.Session
                session = factory(
                    profile_name=self.settings.aws_profile or None,
                    region_name=self.settings.aws_region,
                )
                self._clients[service] = session.client(
                    service,
                    config=Config(
                        connect_timeout=2,
                        read_timeout=10,
                        retries={"mode": "standard", "total_max_attempts": 3},
                        ignore_configured_endpoint_urls=True,
                    ),
                )
            if service == "cloudtrail":
                if self._last_lookup is not None:
                    self.sleeper(max(0, 0.55 - (monotonic() - self._last_lookup)))
                self._last_lookup = monotonic()
            return getattr(self._clients[service], operation)(**kwargs)
        except (BotoCoreError, ValueError) as exc:
            raise AwsOperationError() from exc
        except ClientError:
            # Services handle only typed not-found/precondition codes. No exception is logged.
            raise

    def validate_account(self, expected):
        self.require(expected)
        try:
            identity = self.call("sts", "get_caller_identity")
        except ClientError as exc:
            raise AwsOperationError() from exc
        if identity.get("Account") != expected:
            raise AwsScopeError()

    def pages(self, service, operation, result_key, **kwargs):
        token_key, response_key = (
            ("NextToken", "NextToken") if service == "cloudtrail" else ("Marker", "Marker")
        )
        seen, count, token = set(), 0, None
        for _ in range(self.settings.aws_max_pages):
            params = dict(kwargs)
            if token:
                params[token_key] = token
            try:
                response = self.call(service, operation, **params)
            except ClientError as exc:
                raise AwsOperationError() from exc
            if result_key not in response or not isinstance(response[result_key], list):
                raise AwsImportLimit()
            for item in response.get(result_key, []):
                count += 1
                if count > self.settings.aws_max_records:
                    raise AwsImportLimit()
                yield item
            next_token = response.get(response_key)
            more = (
                bool(next_token) if service == "cloudtrail" else response.get("IsTruncated", False)
            )
            if not more:
                return
            if not next_token or next_token in seen:
                raise AwsImportLimit()
            seen.add(next_token)
            token = next_token
        raise AwsImportLimit()

    def close(self):
        for client in self._clients.values():
            client.close()
        self._clients.clear()
