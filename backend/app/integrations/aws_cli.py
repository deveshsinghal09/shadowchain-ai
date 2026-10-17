"""Explicit opt-in live read check only. Never provisions infrastructure or changes IAM."""

import argparse
import json

from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.core.errors import AppError
from app.integrations.aws_client import AwsGateway


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorize-read-only", action="store_true", required=True)
    parser.add_argument("--confirm-account-id", required=True)
    args = parser.parse_args()
    gateway = AwsGateway(get_settings())
    try:
        gateway.validate_account(args.confirm_account_id)
        response = gateway.call("iam", "list_roles", MaxItems=1)
        print(
            json.dumps(
                {
                    "account_verified": True,
                    "read_only_check": True,
                    "roles_in_sample": len(response.get("Roles", [])),
                    "iam_writes": 0,
                }
            )
        )
    except (AppError, ClientError) as exc:
        print(
            json.dumps({"error": exc.code if isinstance(exc, AppError) else "aws_operation_failed"})
        )
        raise SystemExit(1) from None
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
