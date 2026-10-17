"""Synthetic SDK responses only; no profile, credentials or AWS network is used."""

import copy
from datetime import timedelta

from app.core.time import utc_now
from app.integrations.aws_client import AwsGateway

ACCOUNT = "123456789012"
ROLE_ARN = "arn:aws:iam::123456789012:role/SYNTHETIC-analyst"
POLICY_ARN = "arn:aws:iam::123456789012:policy/SYNTHETIC-read"


def iam_responses():
    trust = {
        "Statement": [
            {"Effect": "Allow", "Principal": {"AWS": ROLE_ARN}, "Action": "sts:AssumeRole"}
        ]
    }
    doc = {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": "arn:aws:s3:::SYNTHETIC-private/report.txt",
                "Condition": {"StringEquals": {"sts:ExternalId": "SHOULD_NEVER_PERSIST"}},
            }
        ]
    }
    role = {
        "RoleId": "SYNTHETIC-ROLE-ID",
        "RoleName": "SYNTHETIC-analyst",
        "Arn": ROLE_ARN,
        "AssumeRolePolicyDocument": trust,
        "Tags": [{"Key": "secret", "Value": "SHOULD_NEVER_PERSIST"}],
    }
    return {
        ("sts", "get_caller_identity"): {"Account": ACCOUNT},
        ("iam", "list_roles"): {"Roles": [role], "IsTruncated": False},
        ("iam", "get_role"): {"Role": role},
        ("iam", "list_attached_role_policies"): {"AttachedPolicies": [{"PolicyArn": POLICY_ARN}]},
        ("iam", "get_policy"): {"Policy": {"Arn": POLICY_ARN, "DefaultVersionId": "v2"}},
        ("iam", "list_policy_versions"): {"Versions": [{"VersionId": "v1"}, {"VersionId": "v2"}]},
        ("iam", "get_policy_version"): lambda params: {
            "PolicyVersion": {
                "VersionId": params["VersionId"],
                "Document": doc,
                "IsDefaultVersion": params["VersionId"] == "v2",
            }
        },
        ("iam", "list_role_policies"): {"PolicyNames": ["SYNTHETIC-inline"]},
        ("iam", "get_role_policy"): {
            "RoleName": role["RoleName"],
            "PolicyName": "SYNTHETIC-inline",
            "PolicyDocument": doc,
        },
    }


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls, self.configs = responses, [], []

    def client(self, service, *, config):
        self.configs.append(config)
        outer = self

        class FakeClient:
            def close(self):
                pass

            def __getattr__(self, operation):
                def call(**params):
                    outer.calls.append((service, operation, params))
                    response = outer.responses[(service, operation)]
                    if isinstance(response, Exception):
                        raise response
                    return copy.deepcopy(response(params) if callable(response) else response)

                return call

        return FakeClient()

    def gateway(self, settings):
        return AwsGateway(settings, session_factory=lambda **_: self, sleeper=lambda _: None)


def trail_event():
    stamp = (utc_now() - timedelta(minutes=10)).replace(microsecond=0)
    return stamp, {
        "eventID": "11111111-1111-1111-1111-111111111111",
        "recipientAccountId": ACCOUNT,
        "eventSource": "iam.amazonaws.com",
        "eventName": "PutRolePolicy",
        "awsRegion": "us-east-1",
        "eventTime": stamp.isoformat(),
        "managementEvent": True,
        "userIdentity": {
            "type": "AssumedRole",
            "arn": ROLE_ARN,
            "accountId": ACCOUNT,
            "accessKeyId": "AKIA" + "X" * 16,
        },
        "requestParameters": {"secretAccessKey": "SHOULD_NEVER_PERSIST"},
        "responseElements": {"sessionToken": "SHOULD_NEVER_PERSIST"},
    }
