"""CDK application entry point.

Run this module through ``uv run python -m infra.app`` or the helper scripts.  It
creates the core serverless stack and the ECS-hosted Streamlit UI. Local credentials
are needed only by CDK during deployment; deployed services use IAM roles.
"""

from __future__ import annotations

import os
from pathlib import Path

import aws_cdk as cdk
from dotenv import load_dotenv

from infra.core_stack import ProspectusCoreStack
from infra.ui_stack import ProspectusUiStack

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

core = ProspectusCoreStack(
    app,
    f"{project_name}-{environment_name}-core",
    project_name=project_name,
    environment_name=environment_name,
    env=deployment_environment,
)

enable_ecs_ui_context = app.node.try_get_context("enable_ecs_ui")
enable_ecs_ui_value = (
    enable_ecs_ui_context
    if enable_ecs_ui_context is not None
    else os.getenv("ENABLE_ECS_UI", "false")
)
enable_ecs_ui = str(enable_ecs_ui_value).lower() == "true"
if enable_ecs_ui:
    image_tag = str(app.node.try_get_context("ui_image_tag") or os.getenv("UI_IMAGE_TAG", ""))
    if not image_tag:
        raise ValueError(
            "UI_IMAGE_TAG is required when ENABLE_ECS_UI=true. "
            "GitHub Actions sets it to the immutable commit SHA after pushing to ECR."
        )
    ProspectusUiStack(
        app,
        f"{project_name}-{environment_name}-ui",
        api_url=core.api_url,
        api_id=core.api_id,
        image_repository=core.ui_repository,
        image_tag=image_tag,
        project_name=project_name,
        environment_name=environment_name,
        env=deployment_environment,
    )

app.synth()
