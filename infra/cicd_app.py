"""CDK entry point for the one-time GitHub OIDC integration stack."""

from __future__ import annotations

import os
import re
from pathlib import Path

import aws_cdk as cdk
from dotenv import load_dotenv

from infra.cicd_stack import GitHubOidcStack

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)

app = cdk.App()
project_name = os.getenv("PROJECT_NAME", "mf-prospectus")
environment_name = os.getenv("ENVIRONMENT", "dev")
github_repository = os.getenv(
    "GITHUB_REPOSITORY", "Shubhankitsirvaiya/Document_Extraction_MF_AWS"
)

# Reject malformed repository values before they can weaken the OIDC subject condition.
if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", github_repository):
    raise ValueError("GITHUB_REPOSITORY must use the exact owner/repository format")

deployment_environment = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT") or os.getenv("EXPECTED_AWS_ACCOUNT_ID"),
    region=os.getenv("CDK_DEFAULT_REGION") or os.getenv("AWS_REGION", "ap-southeast-2"),
)

GitHubOidcStack(
    app,
    f"{project_name}-{environment_name}-github",
    github_repository=github_repository,
    project_name=project_name,
    environment_name=environment_name,
    env=deployment_environment,
)

app.synth()

