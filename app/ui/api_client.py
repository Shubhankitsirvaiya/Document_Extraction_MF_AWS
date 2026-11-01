from __future__ import annotations

import json
import os
from typing import Any

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from app.common.config import Settings


class SignedApiClient:
    """Server-side SigV4 client; browser code never receives AWS credentials."""

    def __init__(self, settings: Settings, session: boto3.Session | None = None) -> None:
        if not settings.api_base_url:
            raise ValueError("API_BASE_URL is required for AWS mode")
        self.settings = settings
        running_with_aws_role = bool(
            os.getenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
            or os.getenv("AWS_CONTAINER_CREDENTIALS_FULL_URI")
            or os.getenv("AWS_EXECUTION_ENV")
        )
        self.session = session or boto3.Session(
            # On a developer machine, use the one-time configured named profile.
            # In any AWS-managed runtime, omit it so boto3 uses that runtime's IAM role.
            profile_name=None if running_with_aws_role else settings.aws_profile,
            region_name=settings.aws_region,
        )

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict:
        url = self.settings.api_base_url.rstrip("/") + "/" + path.lstrip("/")
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        credentials = self.session.get_credentials()
        if credentials is None:
            raise RuntimeError("No AWS credentials are available for SigV4 signing")
        frozen = credentials.get_frozen_credentials()
        aws_request = AWSRequest(
            method=method,
            url=url,
            data=body,
            headers={"content-type": "application/json"},
        )
        SigV4Auth(frozen, "execute-api", self.settings.aws_region).add_auth(aws_request)
        response = requests.request(
            method,
            url,
            data=body,
            headers=dict(aws_request.headers.items()),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
