"""AWS-hosted ECS/Fargate Streamlit UI stack."""

from __future__ import annotations

import os

from aws_cdk import Aws, CfnOutput, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as patterns
from aws_cdk import aws_iam as iam
from constructs import Construct


class ProspectusUiStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        api_url: str,
        api_id: str,
        image_repository: ecr.IRepository,
        image_tag: str,
        project_name: str,
        environment_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        allowed_cidr = str(
            self.node.try_get_context("ui_allowed_cidr") or os.getenv("UI_ALLOWED_CIDR", "")
        )
        if not allowed_cidr:
            raise ValueError(
                "AWS-hosted UI requires UI_ALLOWED_CIDR in .env, for example 203.0.113.10/32"
            )

        # Public subnets avoid a NAT gateway in this teaching stack.  The security
        # group restricts the load balancer to the explicit classroom/admin CIDR.
        vpc = ec2.Vpc(
            self,
            "UiVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC)
            ],
        )
        cluster = ecs.Cluster(self, "UiCluster", vpc=vpc)
        task_role = iam.Role(
            self,
            "UiTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Lets the Streamlit task invoke only this project's IAM-authenticated API",
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["execute-api:Invoke"],
                resources=[
                    f"arn:{Aws.PARTITION}:execute-api:{Aws.REGION}:{Aws.ACCOUNT_ID}:{api_id}/*/*/*"
                ],
            )
        )
        service = patterns.ApplicationLoadBalancedFargateService(
            self,
            "UiService",
            cluster=cluster,
            desired_count=1,
            public_load_balancer=True,
            assign_public_ip=True,
            task_image_options=patterns.ApplicationLoadBalancedTaskImageOptions(
                # The tag is the immutable Git commit SHA produced by GitHub Actions.
                # Fargate pulls from ECR; CDK does not invoke Docker on this machine.
                image=ecs.ContainerImage.from_ecr_repository(image_repository, image_tag),
                container_port=8501,
                task_role=task_role,
                environment={
                    "APP_MODE": "aws",
                    "API_BASE_URL": api_url,
                    "PROJECT_NAME": project_name,
                    "ENVIRONMENT": environment_name,
                    "AWS_REGION": Aws.REGION,
                },
            ),
        )
        CfnOutput(
            self,
            "UiImage",
            value=f"{image_repository.repository_uri}:{image_tag}",
        )
        service.load_balancer.connections.allow_from(
            ec2.Peer.ipv4(allowed_cidr), ec2.Port.tcp(80), "Explicit UI client CIDR"
        )
        CfnOutput(self, "UiUrl", value=f"http://{service.load_balancer.load_balancer_dns_name}")
