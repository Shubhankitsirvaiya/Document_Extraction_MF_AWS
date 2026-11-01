"""CDK application entry point.

Run this module through ``uv run python -m infra.app`` or the helper scripts. It creates
only the serverless AWS backend. Streamlit runs locally and signs API requests with the
developer's temporary AWS profile; deployed services use IAM roles.
"""

from __future__ import annotations

import os
from pathlib import Path

import aws_cdk as cdk
from dotenv import load_dotenv

from infra.core_stack import ProspectusCoreStack

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)

app = cdk.App()
environment_name = app.node.try_get_context("environment") or os.getenv("ENVIRONMENT", "dev")
project_name = app.node.try_get_context("project_name") or os.getenv(
    "PROJECT_NAME", "mf-prospectus"
)
deployment_environment = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT") or os.getenv("EXPECTED_AWS_ACCOUNT_ID"),
    region=os.getenv("CDK_DEFAULT_REGION") or os.getenv("AWS_REGION", "ap-southeast-2"),
)

ProspectusCoreStack(
    app,
    f"{project_name}-{environment_name}-core",
    project_name=project_name,
    environment_name=environment_name,
    env=deployment_environment,
)

app.synth()
