# AWS deployment runbook

These commands start after the repository has been cloned. They do not deploy until the
explicit `cdk deploy` step. Use a sandbox account and review the synthesized template.

## 1. Configure the AWS connection once

```powershell
. .\scripts\setup_aws_connection.ps1 `
  -Region ap-southeast-2 `
  -ExpectedAccountId "123456789012" `
  -UiAllowedCidr "YOUR.PUBLIC.IP/32" `
  -AuthenticationMode login

uv sync --group dev
npm install
```

The leading dot is important: it keeps the verified profile variables in the current
PowerShell session. For an AWS Project account, the script reuses or starts `aws login`,
verifies the resulting temporary session with STS, and writes these non-secret values to
the ignored `.env` file:

- `AWS_PROFILE`: the named deployment profile.
- `AWS_REGION`: the deployment region.
- `EXPECTED_AWS_ACCOUNT_ID`: the verified 12-digit account.
- `UI_ALLOWED_CIDR`: the public client address allowed to reach the AWS load balancer.
- `APP_MODE=aws` and `ENABLE_ECS_UI=false` for local CDK review.

Edit `.env` once more and provide:

- `BEDROCK_MODEL_ID`: an exact vision-capable Bedrock model/inference-profile ID that
  you have enabled in that region. The application deliberately does not guess one.

The AWS CLI stores browser-login metadata and temporary credentials in the Windows user
profile. They must never be copied into `.env`, Git, Docker, ECS, or Lambda
configuration. Deployed services use IAM roles automatically.

For a later terminal session, the profile remains configured. Refresh login and restore
the process selectors with:

```powershell
aws login --profile mf-prospectus-deployer --region ap-southeast-2
$env:AWS_PROFILE = "mf-prospectus-deployer"
$env:AWS_REGION = "ap-southeast-2"
```

## 2. Verify tools, credentials, and tests

```powershell
uv run python scripts/check_prerequisites.py
uv run python scripts/verify_aws_account.py `
  --profile mf-prospectus-deployer `
  --region ap-southeast-2 `
  --expected-account 123456789012
uv run pytest
```

Account verification is a hard safety gate. Re-run it before deployment or destruction.

## 3. Build deployment packages

```powershell
uv run python scripts/build_packages.py
```

`uv` resolves Linux x86-64 wheels for Python 3.12 even when this command runs on
Windows. The output under `generated/packages` is ignored by Git. Do not use
`--skip-dependencies` for a real deployment; that option exists only for CDK tests.

## 4. Bootstrap and review CDK

```powershell
$env:AWS_PROFILE = "mf-prospectus-deployer"
$env:AWS_REGION = "ap-southeast-2"
$env:CDK_DEFAULT_REGION = $env:AWS_REGION
$env:CDK_DEFAULT_ACCOUNT = "123456789012"

npx cdk bootstrap "aws://$env:CDK_DEFAULT_ACCOUNT/$env:CDK_DEFAULT_REGION"
npx cdk synth -c environment=dev
npx cdk diff --all -c environment=dev
```

Inspect the diff. Confirm there is no `AWS::StepFunctions::StateMachine`, EKS cluster,
NAT gateway, or public S3 policy. The API routes must use `AWS_IAM` authorization.

## 5. Configure GitHub OIDC once

The workflows use temporary GitHub OIDC credentials, not AWS access-key secrets. Create
one AWS deployment role whose trust policy is restricted to the exact GitHub repository,
then add these GitHub **Actions variables**:

> **AWS Project account prerequisite:** the simplified AWS Project/free-plan environment
> can apply organization Service Control Policies that deny CloudFormation stack
> creation, ECR repositories, S3 buckets, and external IAM OIDC providers. In that case,
> CDK bootstrap and GitHub OIDC cannot run until the account owner upgrades the account
> and activates advanced AWS features. AWS documents that activation as irreversible;
> it removes project spend limits and makes the owner responsible for normal usage
> charges. Review billing, budgets, and idle resources before activating it.

After advanced features are active, deploy the repository-scoped trust stack once:

```powershell
$env:AWS_PROFILE = "mf-prospectus-deployer"
$env:AWS_REGION = "ap-southeast-2"
$env:CDK_DEFAULT_REGION = $env:AWS_REGION
$env:CDK_DEFAULT_ACCOUNT = "YOUR_12_DIGIT_ACCOUNT_ID"
$env:GITHUB_REPOSITORY = "Shubhankitsirvaiya/Document_Extraction_MF_AWS"

npx cdk bootstrap "aws://$env:CDK_DEFAULT_ACCOUNT/$env:CDK_DEFAULT_REGION"
npx cdk deploy `
  --app "uv run python -m infra.cicd_app" `
  "mf-prospectus-dev-github" `
  --require-approval broadening
```

The resulting `GitHubDeploymentRoleArn` output becomes the
`AWS_DEPLOY_ROLE_ARN` repository variable. Its trust policy accepts only the exact
repository `main` branch. The role has no `AdministratorAccess` policy; it can assume
the account-local CDK bootstrap roles and push only to the project's UI ECR repository.

- `AWS_ACCOUNT_ID`: the 12-digit project account.
- `AWS_DEPLOY_ROLE_ARN`: the repository-scoped GitHub OIDC role.
- `BEDROCK_MODEL_ID`: the verified Bedrock model or inference-profile ID.
- `UI_ALLOWED_CIDR`: the administrator/classroom public address with `/32`.

The repository identity is required before creating the OIDC trust policy. Never use a
wildcard repository owner and never store a permanent AWS access key in GitHub Secrets.

## 6. Automatic CI versus manual deployment

Every push and pull request runs `.github/workflows/ci.yml`. It runs lint/tests and
builds `Dockerfile.ui` on a GitHub-hosted Docker runner. It does not log into AWS, push
an image, or deploy resources.

To publish and deploy, open **Actions → Manual - Publish Image and Deploy AWS → Run
workflow**, enter `DEPLOY`, and run it. The manual workflow:

1. authenticates to AWS with GitHub OIDC;
2. runs tests and builds Linux Lambda/AgentCore packages;
3. deploys the core stack, which creates the ECR repository;
4. builds and pushes `repository:<full-git-commit-sha>`;
5. deploys Fargate with that immutable image tag.

The explicit confirmation is intentional because the core stack, ALB, and Fargate
service can incur AWS charges.

## 7. Optional local core deployment

```powershell
npx cdk deploy "mf-prospectus-dev-core" `
  -c environment=dev `
  --outputs-file generated/core-outputs.json `
  --require-approval broadening

uv run python scripts/export_outputs.py generated/core-outputs.json
```

Optional cost controls:

```powershell
npx cdk deploy "mf-prospectus-dev-core" `
  -c environment=dev `
  -c budget_email="you@example.com" `
  -c budget_usd=10
```

This command deploys the serverless core and empty ECR repository. It does not build or
deploy the UI. Docker is unnecessary locally because image building happens in GitHub.

## 8. Open the AWS-hosted UI

Read the `UiUrl` stack output:

```powershell
$outputs = Get-Content generated/ui-outputs.json | ConvertFrom-Json
$outputs.'mf-prospectus-dev-ui'.UiUrl
```

Open that URL from an IP inside `UI_ALLOWED_CIDR`. The Streamlit container receives
short-lived ECS task-role credentials and signs API calls server-side. The browser never
receives AWS credentials.

## 9. Observe and evaluate

Use the stack output `DashboardName` in CloudWatch. Also inspect:

- SQS intake queue age and DLQ depth;
- API/dispatch/validator/reconcile Lambda logs and errors;
- AgentCore managed runtime logs and X-Ray traces;
- EventBridge completion log group;
- DynamoDB job item status, attempt ID, heartbeat, and error fields.

Run offline evaluation without a paid call:

```powershell
uv run mf-evaluate generated/predictions.json data/human-labels.json
```

Any live Bedrock evaluation must be an explicit, separately labelled operation.

## 10. Stop costs and destroy

The core path has no always-on server, but retained S3/DynamoDB data and logs can still
cost money. First remove any optional UI stack, then core infrastructure:

```powershell
npx cdk destroy "mf-prospectus-dev-ui"
npx cdk destroy "mf-prospectus-dev-core"
```

S3 and DynamoDB use `RETAIN` to prevent accidental data loss. After stack deletion,
review and manually delete those retained resources only when you are certain their data
is no longer needed.
