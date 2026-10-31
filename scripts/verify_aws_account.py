"""Verify profile, region, and account before any deploy/destroy command."""

from __future__ import annotations

import argparse

import boto3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-account", required=True)
    arguments = parser.parse_args()

    session = boto3.Session(profile_name=arguments.profile, region_name=arguments.region)
    identity = session.client("sts").get_caller_identity()
    actual_account = identity["Account"]
    if actual_account != arguments.expected_account:
        raise SystemExit(
            f"Refusing deployment: expected account {arguments.expected_account}, "
            f"but credentials belong to {actual_account}."
        )
    print(f"Verified account={actual_account} region={arguments.region} arn={identity['Arn']}")


if __name__ == "__main__":
    main()
