"""Core AWS architecture expressed as one reviewable CDK stack.

The stack intentionally uses managed/serverless services only.  There is no VPC,
NAT gateway, EKS cluster, Step Functions state machine, or vector database.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from aws_cdk import (
    Aws,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_apigatewayv2 as apigwv2,
)
from aws_cdk import (
    aws_apigatewayv2_authorizers as apigw_authorizers,
)
from aws_cdk import (
    aws_apigatewayv2_integrations as apigw_integrations,
)
from aws_cdk import (
    aws_bedrockagentcore as agentcore,
)
from aws_cdk import (
    aws_budgets as budgets,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_ecr as ecr,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_lambda_event_sources as event_sources,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_scheduler as scheduler,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from aws_cdk import (
    aws_sqs as sqs,
)
from constructs import Construct

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "generated" / "packages"


class ProspectusCoreStack(Stack):
    """Private storage, async dispatch, AgentCore runtime, API, and monitoring."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project_name: str,
        environment_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        prefix = f"{project_name}-{environment_name}"
        retention_days = int(self.node.try_get_context("artifact_retention_days") or 7)
        max_upload_mib = int(self.node.try_get_context("max_upload_mib") or 20)
        model_id = str(
            self.node.try_get_context("bedrock_model_id") or os.getenv("BEDROCK_MODEL_ID", "")
        )
        if not model_id:
            raise ValueError(
                "BEDROCK_MODEL_ID must be set in .env before synthesizing the AWS-only stack"
            )

        # Data boundary: private, encrypted artifacts expire automatically; the job
        # table is on-demand and uses the same TTL timestamp as the application.
        artifact_bucket = s3.Bucket(
            self,
            "ArtifactBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireDemoArtifacts",
                    enabled=True,
                    expiration=Duration.days(retention_days),
                    noncurrent_version_expiration=Duration.days(retention_days),
                )
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )
        jobs_table = dynamodb.Table(
            self,
            "JobsTable",
            partition_key=dynamodb.Attribute(name="job_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.RETAIN,
        )

        # GitHub Actions builds the Streamlit image and pushes a commit-SHA tag to
        # this repository. CDK therefore never needs a local Docker daemon. Keeping
        # the repository in the core stack makes it available before the optional
        # Fargate UI stack is synthesized and deployed.
        ui_repository = ecr.Repository(
            self,
            "UiRepository",
            repository_name=f"{prefix}-ui",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep only the latest 20 commit images",
                    max_image_count=20,
                )
            ],
            empty_on_delete=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Queue boundary: failed dispatches are isolated in a DLQ instead of being
        # retried forever.  The visibility timeout exceeds the short dispatch call.
        dead_letter_queue = sqs.Queue(
            self,
            "DispatchDlq",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(14),
        )
        intake_queue = sqs.Queue(
            self,
            "IntakeQueue",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            visibility_timeout=Duration.minutes(5),
            dead_letter_queue=sqs.DeadLetterQueue(queue=dead_letter_queue, max_receive_count=3),
        )

        common_environment = {
            "APP_MODE": "aws",
            "PROJECT_NAME": project_name,
            "ENVIRONMENT": environment_name,
            "ARTIFACT_BUCKET_NAME": artifact_bucket.bucket_name,
            "JOBS_TABLE_NAME": jobs_table.table_name,
            "INTAKE_QUEUE_URL": intake_queue.queue_url,
            "MAX_UPLOAD_BYTES": str(max_upload_mib * 1024 * 1024),
            "MAX_PDF_PAGES": "20",
            "MAX_UPLOAD_MIB": str(max_upload_mib),
            "MODEL_PAGE_BATCH_SIZE": "2",
            "MAX_MODEL_CALLS": "12",
            "MAX_JOB_SECONDS": "1200",
            "MAX_ACTIVE_JOBS": "1",
            "ARTIFACT_RETENTION_DAYS": str(retention_days),
            "BEDROCK_MODEL_ID": model_id,
        }

        def function(
            logical_id: str,
            package_name: str,
            handler: str,
            *,
            timeout: Duration | None = None,
            memory_size: int = 512,
            environment: dict[str, str] | None = None,
        ) -> lambda_.Function:
            """Create a Lambda from a package produced by scripts/build_packages.py."""

            log_group = logs.LogGroup(
                self,
                f"{logical_id}LogGroup",
                retention=logs.RetentionDays.ONE_WEEK,
                removal_policy=RemovalPolicy.DESTROY,
            )
            return lambda_.Function(
                self,
                logical_id,
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.X86_64,
                code=lambda_.Code.from_asset(str(PACKAGES / package_name)),
                handler=handler,
                timeout=timeout or Duration.seconds(30),
                memory_size=memory_size,
                tracing=lambda_.Tracing.ACTIVE,
                environment={**common_environment, **(environment or {})},
                log_group=log_group,
            )

        api_function = function("ApiFunction", "api", "lambdas.api.handler.handler")
        input_validator = function(
            "InputValidatorFunction",
            "validate_input",
            "lambdas.validate_input.handler.handler",
            timeout=Duration.minutes(2),
            memory_size=2048,
        )
        output_validator = function(
            "OutputValidatorFunction",
            "validate_output",
            "lambdas.validate_output.handler.handler",
            timeout=Duration.minutes(1),
            memory_size=1024,
        )
        reconcile_function = function(
            "ReconcileFunction",
            "reconcile",
            "lambdas.reconcile.handler.handler",
            timeout=Duration.minutes(2),
        )

        # Completion events are deliberately decoupled from the worker.  A log target
        # gives the class a visible event trail without another application service.
        completion_bus = events.EventBus(self, "CompletionBus")
        completion_log = logs.LogGroup(
            self,
            "CompletionEventLog",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        logs.CfnResourcePolicy(
            self,
            "CompletionLogResourcePolicy",
            policy_name=f"{prefix}-event-log-policy",
            policy_document=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "events.amazonaws.com"},
                            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                            "Resource": f"{completion_log.log_group_arn}:*",
                        }
                    ],
                }
            ),
        )
        events.CfnRule(
            self,
            "CompletionLogRule",
            event_bus_name=completion_bus.event_bus_name,
            event_pattern={"source": ["mf.prospectus"]},
            state="ENABLED",
            targets=[
                events.CfnRule.TargetProperty(
                    arn=completion_log.log_group_arn,
                    id="CompletionLog",
                )
            ],
        )
        reconcile_function.add_environment("COMPLETION_BUS_NAME", completion_bus.event_bus_name)

        # AgentCore receives a Linux-compatible package assembled before CDK deploy.
        # IAM is the inbound authorizer; the runtime itself writes only to this stack's
        # bucket/table and may invoke only the two validator functions.
        runtime_role = iam.Role(
            self,
            "AgentRuntimeRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        runtime_artifact = agentcore.AgentRuntimeArtifact.from_code_asset(
            path=str(PACKAGES / "agentcore"),
            runtime=agentcore.AgentCoreRuntime.PYTHON_3_12,
            entrypoint=["python", "runtime_entrypoint.py"],
        )
        runtime = agentcore.Runtime(
            self,
            "ExtractionRuntime",
            runtime_name=f"{project_name}_{environment_name}".replace("-", "_")[:48],
            description="Asynchronous mutual-fund prospectus restriction extractor",
            agent_runtime_artifact=runtime_artifact,
            execution_role=runtime_role,
            environment_variables={
                **common_environment,
                "INPUT_VALIDATOR_NAME": input_validator.function_name,
                "OUTPUT_VALIDATOR_NAME": output_validator.function_name,
                "HEARTBEAT_SECONDS": "30",
            },
            tracing_enabled=True,
        )

        dispatch_function = function(
            "DispatchFunction",
            "dispatch",
            "lambdas.dispatch.handler.handler",
            timeout=Duration.minutes(2),
            environment={"AGENTCORE_RUNTIME_ARN": runtime.agent_runtime_arn},
        )
        dispatch_function.add_event_source(
            event_sources.SqsEventSource(
                intake_queue,
                batch_size=1,
                report_batch_item_failures=True,
            )
        )

        # Least-privilege grants connect the resources; none of the browser-facing
        # code receives AWS credentials or direct S3/DynamoDB permissions.
        artifact_bucket.grant_read_write(api_function)
        artifact_bucket.grant_read(input_validator)
        artifact_bucket.grant_read_write(input_validator)
        artifact_bucket.grant_read_write(output_validator)
        artifact_bucket.grant_read_write(runtime_role)
        jobs_table.grant_read_write_data(api_function)
        jobs_table.grant_read_write_data(dispatch_function)
        jobs_table.grant_read_write_data(reconcile_function)
        jobs_table.grant_read_write_data(runtime_role)
        intake_queue.grant_send_messages(api_function)
        intake_queue.grant_send_messages(reconcile_function)
        input_validator.grant_invoke(runtime_role)
        output_validator.grant_invoke(runtime_role)
        completion_bus.grant_put_events_to(reconcile_function)
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:Converse", "bedrock:ConverseStream"],
                resources=[f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}::foundation-model/*"],
            )
        )
        dispatch_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[runtime.agent_runtime_arn, f"{runtime.agent_runtime_arn}/*"],
            )
        )

        # IAM-authenticated HTTP API.  Streamlit signs requests server-side with
        # SigV4; it never places AWS access keys in the browser.
        http_api = apigwv2.HttpApi(
            self,
            "JobsApi",
            api_name=f"{prefix}-api",
            create_default_stage=True,
        )
        integration = apigw_integrations.HttpLambdaIntegration("ApiIntegration", api_function)
        iam_authorizer = apigw_authorizers.HttpIamAuthorizer()
        http_api.add_routes(
            path="/jobs",
            methods=[apigwv2.HttpMethod.POST],
            integration=integration,
            authorizer=iam_authorizer,
        )
        http_api.add_routes(
            path="/jobs/{job_id}/submit",
            methods=[apigwv2.HttpMethod.POST],
            integration=integration,
            authorizer=iam_authorizer,
        )
        http_api.add_routes(
            path="/jobs/{job_id}",
            methods=[apigwv2.HttpMethod.GET],
            integration=integration,
            authorizer=iam_authorizer,
        )

        # Scheduler role is separate so only the scheduler service can invoke the
        # reconciliation Lambda on the one-minute recovery cadence.
        scheduler_role = iam.Role(
            self,
            "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        reconcile_function.grant_invoke(scheduler_role)
        scheduler.CfnSchedule(
            self,
            "ReconcileSchedule",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression="rate(1 minute)",
            state="ENABLED",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=reconcile_function.function_arn,
                role_arn=scheduler_role.role_arn,
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                    maximum_event_age_in_seconds=300,
                    maximum_retry_attempts=2,
                ),
            ),
        )

        # Lightweight native observability: CloudWatch dashboard and alarms only.
        dashboard = cloudwatch.Dashboard(self, "OperationsDashboard", dashboard_name=f"{prefix}")
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Queue depth and age",
                left=[intake_queue.metric_approximate_number_of_messages_visible()],
                right=[intake_queue.metric_approximate_age_of_oldest_message()],
            ),
            cloudwatch.GraphWidget(
                title="Lambda errors",
                left=[
                    api_function.metric_errors(),
                    dispatch_function.metric_errors(),
                    input_validator.metric_errors(),
                    output_validator.metric_errors(),
                    reconcile_function.metric_errors(),
                ],
            ),
            cloudwatch.GraphWidget(
                title="DynamoDB throttles",
                left=[jobs_table.metric_throttled_requests_for_operation("UpdateItem")],
            ),
        )
        dead_letter_queue.metric_approximate_number_of_messages_visible().create_alarm(
            self,
            "DlqNotEmptyAlarm",
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )
        intake_queue.metric_approximate_age_of_oldest_message().create_alarm(
            self,
            "QueueAgeAlarm",
            threshold=300,
            evaluation_periods=2,
        )

        if str(self.node.try_get_context("enable_secret_demo") or "false").lower() == "true":
            secret = secretsmanager.Secret(
                self,
                "DemoSecret",
                description="Optional demo secret; model IDs and normal settings are not secrets",
                generate_secret_string=secretsmanager.SecretStringGenerator(
                    secret_string_template='{"purpose":"class-demo"}',
                    generate_string_key="token",
                ),
            )
            secret.grant_read(runtime_role)
            CfnOutput(self, "AppSecretArn", value=secret.secret_arn)

        budget_email = str(self.node.try_get_context("budget_email") or "")
        if budget_email:
            budgets.CfnBudget(
                self,
                "DemoBudget",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(
                        amount=float(self.node.try_get_context("budget_usd") or 10),
                        unit="USD",
                    ),
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            comparison_operator="GREATER_THAN",
                            notification_type="FORECASTED",
                            threshold=80,
                            threshold_type="PERCENTAGE",
                        ),
                        subscribers=[
                            budgets.CfnBudget.SubscriberProperty(
                                address=budget_email,
                                subscription_type="EMAIL",
                            )
                        ],
                    )
                ],
            )

        self.api_url = http_api.api_endpoint
        self.api_id = http_api.api_id
        self.ui_repository = ui_repository
        CfnOutput(self, "ApiBaseUrl", value=http_api.api_endpoint)
        CfnOutput(self, "ArtifactBucketName", value=artifact_bucket.bucket_name)
        CfnOutput(self, "JobsTableName", value=jobs_table.table_name)
        CfnOutput(self, "IntakeQueueUrl", value=intake_queue.queue_url)
        CfnOutput(self, "AgentCoreRuntimeArn", value=runtime.agent_runtime_arn)
        CfnOutput(self, "InputValidatorArn", value=input_validator.function_arn)
        CfnOutput(self, "OutputValidatorArn", value=output_validator.function_arn)
        CfnOutput(self, "DashboardName", value=dashboard.dashboard_name)
        CfnOutput(self, "UiRepositoryName", value=ui_repository.repository_name)
        CfnOutput(self, "UiRepositoryUri", value=ui_repository.repository_uri)
