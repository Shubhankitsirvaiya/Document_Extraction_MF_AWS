"""One-time AWS trust boundary for GitHub Actions deployments.

This stack is intentionally separate from the application stacks. It is deployed once
from an already authenticated developer terminal, then GitHub Actions receives temporary
AWS credentials through OpenID Connect (OIDC). No long-lived AWS access key is created.
"""

from __future__ import annotations

from aws_cdk import Aws, CfnOutput, Duration, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct


class GitHubOidcStack(Stack):
    """Trust one repository's main branch and grant bounded deployment capabilities."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        github_repository: str,
        project_name: str,
        environment_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # GitHub signs each workflow identity token with this provider. The audience
        # condition ensures the token was issued for AWS STS rather than another service.
        provider = iam.OpenIdConnectProvider(
            self,
            "GitHubProvider",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
        )

        # The subject condition is deliberately exact: only workflow runs from the
        # repository's main branch can request this role. Pull requests, forks, tags,
        # other branches, and other repositories cannot assume it.
        github_principal = iam.WebIdentityPrincipal(
            provider.open_id_connect_provider_arn,
            conditions={
                "StringEquals": {
                    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    "token.actions.githubusercontent.com:sub": (
                        f"repo:{github_repository}:ref:refs/heads/main"
                    ),
                }
            },
        )
        deployment_role = iam.Role(
            self,
            "GitHubDeploymentRole",
            role_name=f"{project_name}-{environment_name}-github-deploy",
            assumed_by=github_principal,
            max_session_duration=Duration.hours(1),
            description=(
                "Short-lived GitHub Actions role for the MF prospectus CDK deployment"
            ),
        )

        # CDK's modern bootstrap creates dedicated lookup, file-publishing,
        # image-publishing, and CloudFormation deployment roles. GitHub may assume only
        # those account-local CDK roles instead of receiving AdministratorAccess itself.
        deployment_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[
                    f"arn:{Aws.PARTITION}:iam::{Aws.ACCOUNT_ID}:role/cdk-hnb659fds-*"
                ],
            )
        )

        # The workflow reads stack outputs and the CDK bootstrap version before/after
        # assuming CDK's deployment role.
        deployment_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudformation:DescribeStacks"],
                resources=[
                    f"arn:{Aws.PARTITION}:cloudformation:{Aws.REGION}:{Aws.ACCOUNT_ID}:"
                    f"stack/{project_name}-{environment_name}-*/*"
                ],
            )
        )
        deployment_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:{Aws.PARTITION}:ssm:{Aws.REGION}:{Aws.ACCOUNT_ID}:"
                    "parameter/cdk-bootstrap/hnb659fds/version"
                ],
            )
        )

        # Docker logs in with a registry-wide authorization token, then pushes only to
        # the deterministic Streamlit repository created by the core application stack.
        deployment_role.add_to_policy(
            iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
        )
        deployment_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:BatchGetImage",
                    "ecr:CompleteLayerUpload",
                    "ecr:DescribeImages",
                    "ecr:DescribeRepositories",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:InitiateLayerUpload",
                    "ecr:PutImage",
                    "ecr:UploadLayerPart",
                ],
                resources=[
                    f"arn:{Aws.PARTITION}:ecr:{Aws.REGION}:{Aws.ACCOUNT_ID}:"
                    f"repository/{project_name}-{environment_name}-ui"
                ],
            )
        )

        CfnOutput(self, "GitHubDeploymentRoleArn", value=deployment_role.role_arn)
        CfnOutput(self, "TrustedGitHubRepository", value=github_repository)

