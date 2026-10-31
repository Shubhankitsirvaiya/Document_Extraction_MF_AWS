from __future__ import annotations

import boto3
from botocore.session import Session as BotocoreSession

from app.common.config import Settings


def local_boto3_session(settings: Settings) -> boto3.Session:
    """Create a laptop session with the selected named profile and Region."""

    return boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)


def runtime_boto3_session(settings: Settings) -> boto3.Session:
    """Create a cloud-runtime session using IAM role credentials, never AWS_PROFILE."""

    return boto3.Session(region_name=settings.aws_region, botocore_session=BotocoreSession())
