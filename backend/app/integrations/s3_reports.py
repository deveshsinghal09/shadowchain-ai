"""One configured private bucket. Only redacted report bytes can reach PutObject."""

import base64
import hashlib

from botocore.exceptions import ClientError

from app.services.aws_errors import AwsOperationError, PrivateBucketRequired, ReportConflict


class S3ReportStore:
    def __init__(self, gateway):
        self.gateway = gateway
        self.settings = gateway.settings

    def validate(self):
        self.gateway.validate_account(self.settings.aws_account_id)
        params = {
            "Bucket": self.settings.s3_export_bucket,
            "ExpectedBucketOwner": self.settings.aws_account_id,
        }
        try:
            block = self.gateway.call("s3", "get_public_access_block", **params)[
                "PublicAccessBlockConfiguration"
            ]
            if not all(
                block.get(k) is True
                for k in (
                    "BlockPublicAcls",
                    "IgnorePublicAcls",
                    "BlockPublicPolicy",
                    "RestrictPublicBuckets",
                )
            ):
                raise PrivateBucketRequired()
            try:
                status = self.gateway.call("s3", "get_bucket_policy_status", **params)
                if status.get("PolicyStatus", {}).get("IsPublic") is not False:
                    raise PrivateBucketRequired()
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "NoSuchBucketPolicy":
                    raise
            acl = self.gateway.call("s3", "get_bucket_acl", **params)
            owner = acl.get("Owner", {}).get("ID")
            if not owner or any(
                g.get("Grantee", {}).get("Type") != "CanonicalUser"
                or g["Grantee"].get("ID") != owner
                for g in acl.get("Grants", [])
            ):
                raise PrivateBucketRequired()
        except (ClientError, KeyError) as exc:
            raise PrivateBucketRequired() from exc

    def publish(self, report_id, payload, digest):
        self.validate()
        params = {
            "Bucket": self.settings.s3_export_bucket,
            "Key": "shadowchain-reports/" + report_id + ".json",
            "ExpectedBucketOwner": self.settings.aws_account_id,
        }

        def existing():
            try:
                head = self.gateway.call("s3", "head_object", **params)
                if head.get("Metadata", {}).get("sha256") != digest or head.get(
                    "ContentLength"
                ) != len(payload):
                    raise ReportConflict()
                return True
            except ClientError as exc:
                if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                    return False
                raise

        try:
            try:
                self.gateway.call(
                    "s3",
                    "put_object",
                    **params,
                    Body=payload,
                    ContentType="application/json",
                    ServerSideEncryption="AES256",
                    IfNoneMatch="*",
                    ChecksumSHA256=base64.b64encode(hashlib.sha256(payload).digest()).decode(),
                    Metadata={"sha256": digest, "redaction": "allowlist-summary-v1"},
                )
            except ClientError as exc:
                # Conditional creation avoids needing ListBucket merely to detect a missing key.
                if exc.response["Error"]["Code"] != "PreconditionFailed" or not existing():
                    raise
            return "s3://" + params["Bucket"] + "/" + params["Key"]
        except ClientError as exc:
            raise AwsOperationError() from exc
