from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from infra.core_stack import ProspectusCoreStack


def synthesized_template() -> Template:
    app = cdk.App(
        context={
            "artifact_retention_days": 7,
            "max_upload_mib": 20,
            "bedrock_model_id": "test-model-id",
            "bedrock_model_resource_arns": (
                "arn:aws:bedrock:ap-southeast-2:123456789012:"
                "inference-profile/test-model-id"
            ),
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
    template.has_resource_properties(
        "AWS::BedrockAgentCore::Runtime",
        Match.object_like(
            {
                "AgentRuntimeArtifact": {
                    "CodeConfiguration": {
                        "EntryPoint": ["runtime_entrypoint.py"]
                    }
                }
            }
        ),
    )


def test_excluded_architectures_are_not_synthesized():
    template = synthesized_template()
    assert template.find_resources("AWS::StepFunctions::StateMachine") == {}
    assert template.find_resources("AWS::EKS::Cluster") == {}
    assert template.find_resources("AWS::EC2::NatGateway") == {}
    assert template.find_resources("AWS::ECS::Service") == {}
    assert template.find_resources("AWS::ECR::Repository") == {}
    assert template.find_resources("AWS::ElasticLoadBalancingV2::LoadBalancer") == {}


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
