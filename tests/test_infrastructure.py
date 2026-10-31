from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_ecr as ecr
from aws_cdk.assertions import Match, Template

from infra.core_stack import ProspectusCoreStack
from infra.ui_stack import ProspectusUiStack


def synthesized_template() -> Template:
    app = cdk.App(
        context={
            "artifact_retention_days": 7,
            "max_upload_mib": 20,
            "bedrock_model_id": "test-model-id",
        }
    )
    stack = ProspectusCoreStack(
        app,
        "TestCore",
        project_name="mf-prospectus",
        environment_name="test",
        env=cdk.Environment(account="123456789012", region="ap-southeast-2"),
    )
    return Template.from_stack(stack)


def test_expected_serverless_boundaries_exist():
    template = synthesized_template()
    template.resource_count_is("AWS::Lambda::Function", 5)
    template.resource_count_is("AWS::BedrockAgentCore::Runtime", 1)
    template.resource_count_is("AWS::SQS::Queue", 2)
    template.resource_count_is("AWS::DynamoDB::Table", 1)
    template.resource_count_is("AWS::ApiGatewayV2::Route", 3)
    template.all_resources_properties(
        "AWS::ApiGatewayV2::Route", Match.object_like({"AuthorizationType": "AWS_IAM"})
    )


def test_excluded_architectures_are_not_synthesized():
    template = synthesized_template()
    assert template.find_resources("AWS::StepFunctions::StateMachine") == {}
    assert template.find_resources("AWS::EKS::Cluster") == {}
    assert template.find_resources("AWS::EC2::NatGateway") == {}


def test_artifact_bucket_is_private_and_encrypted():
    synthesized_template().has_resource_properties(
        "AWS::S3::Bucket",
        Match.object_like(
            {
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True,
                    "BlockPublicPolicy": True,
                    "IgnorePublicAcls": True,
                    "RestrictPublicBuckets": True,
                },
                "BucketEncryption": Match.any_value(),
                "VersioningConfiguration": {"Status": "Enabled"},
            }
        ),
    )


def test_streamlit_ui_is_an_aws_fargate_service_with_an_api_task_role():
    app = cdk.App(context={"ui_allowed_cidr": "203.0.113.10/32"})
    repository_stack = cdk.Stack(
        app,
        "RepositoryStack",
        env=cdk.Environment(account="123456789012", region="ap-southeast-2"),
    )
    repository = ecr.Repository(repository_stack, "UiRepository")
    stack = ProspectusUiStack(
        app,
        "TestUi",
        api_url="https://example.execute-api.ap-southeast-2.amazonaws.com",
        api_id="example123",
        image_repository=repository,
        image_tag="0123456789abcdef",
        project_name="mf-prospectus",
        environment_name="test",
        env=cdk.Environment(account="123456789012", region="ap-southeast-2"),
    )
    template = Template.from_stack(stack)
    template.resource_count_is("AWS::ECS::Service", 1)
    template.resource_count_is("AWS::ElasticLoadBalancingV2::LoadBalancer", 1)
    template.has_resource_properties(
        "AWS::IAM::Policy",
        Match.object_like(
            {
                "PolicyDocument": Match.object_like(
                    {
                        "Statement": Match.array_with(
                            [
                                Match.object_like(
                                    {
                                        "Action": "execute-api:Invoke",
                                        "Effect": "Allow",
                                    }
                                )
                            ]
                        )
                    }
                )
            }
        ),
    )


def test_core_stack_creates_an_immutable_ecr_repository_for_ci_images():
    synthesized_template().has_resource_properties(
        "AWS::ECR::Repository",
        Match.object_like(
            {
                "ImageScanningConfiguration": {"ScanOnPush": True},
                "ImageTagMutability": "IMMUTABLE",
            }
        ),
    )
