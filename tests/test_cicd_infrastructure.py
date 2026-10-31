from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from infra.cicd_stack import GitHubOidcStack


def synthesized_template() -> Template:
    app = cdk.App()
    stack = GitHubOidcStack(
        app,
        "TestGitHub",
        github_repository="Shubhankitsirvaiya/Document_Extraction_MF_AWS",
        project_name="mf-prospectus",
        environment_name="test",
        env=cdk.Environment(account="123456789012", region="ap-southeast-2"),
    )
    return Template.from_stack(stack)


def test_oidc_trust_is_limited_to_exact_repository_main_branch():
    synthesized_template().has_resource_properties(
        "AWS::IAM::Role",
        Match.object_like(
            {
                "AssumeRolePolicyDocument": Match.object_like(
                    {
                        "Statement": Match.array_with(
                            [
                                Match.object_like(
                                    {
                                        "Action": "sts:AssumeRoleWithWebIdentity",
                                        "Condition": {
                                            "StringEquals": {
                                                "token.actions.githubusercontent.com:aud": (
                                                    "sts.amazonaws.com"
                                                ),
                                                "token.actions.githubusercontent.com:sub": (
                                                    "repo:Shubhankitsirvaiya/"
                                                    "Document_Extraction_MF_AWS:"
                                                    "ref:refs/heads/main"
                                                ),
                                            }
                                        },
                                    }
                                )
                            ]
                        )
                    }
                )
            }
        ),
    )


def test_github_role_does_not_receive_administrator_access():
    template = synthesized_template()
    roles = template.find_resources("AWS::IAM::Role")
    for role in roles.values():
        managed = role.get("Properties", {}).get("ManagedPolicyArns", [])
        assert all("AdministratorAccess" not in str(policy) for policy in managed)

