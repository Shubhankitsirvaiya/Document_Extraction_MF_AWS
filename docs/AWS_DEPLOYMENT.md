# AWS deployment runbook

These commands start after the repository has been cloned. They do not deploy until the
explicit `cdk deploy` step. Use a sandbox account and review the synthesized template.

## 1. Configure the AWS connection once

```powershell
. .\scripts\setup_aws_connection.ps1 `
  -Region ap-southeast-2 `
  -ExpectedAccountId "123456789012" `
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
- `APP_MODE=aws`: use the real AWS backend.

Edit `.env` once more and provide:

- `BEDROCK_MODEL_ID`: an exact vision-capable Bedrock model/inference-profile ID that
  you have enabled in that region. The application deliberately does not guess one.
- `BEDROCK_MODEL_RESOURCE_ARNS`: the comma-separated inference-profile and underlying
  foundation-model ARNs returned by `aws bedrock get-inference-profile`. These are
  non-secret identifiers used to avoid wildcard model permissions.

The AWS CLI stores browser-login metadata and temporary credentials in the Windows user
profile. They must never be copied into `.env`, Git, or Lambda configuration. Deployed
services use IAM roles automatically.

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

`uv` resolves Linux x86-64 wheels for the Lambda packages and Linux ARM64 wheels for
the AgentCore direct-code package, even when the command runs on Windows. The output
under `generated/packages` is ignored by Git. Do not use
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
> can apply organization Service Control Policies that deny external IAM OIDC providers.
> This account currently has that restriction, so local profile-based CDK deployment is
> the supported path until AWS Support enables the capability.

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
only the account-local CDK bootstrap roles needed for serverless deployment.

- `AWS_ACCOUNT_ID`: the 12-digit project account.
- `AWS_DEPLOY_ROLE_ARN`: the repository-scoped GitHub OIDC role.
- `BEDROCK_MODEL_ID`: the verified Bedrock model or inference-profile ID.
- `BEDROCK_MODEL_RESOURCE_ARNS`: comma-separated exact profile/model ARNs.

The repository identity is required before creating the OIDC trust policy. Never use a
wildcard repository owner and never store a permanent AWS access key in GitHub Secrets.

## 6. Automatic CI versus manual deployment

Every push and pull request runs `.github/workflows/ci.yml`. It runs lint/tests and
builds Linux Lambda/AgentCore packages. It does not log into AWS or deploy resources.

To deploy, open **Actions → Manual - Deploy Serverless AWS Backend → Run workflow**,
enter `DEPLOY`, and run it. The manual workflow:

1. authenticates to AWS with GitHub OIDC;
2. runs tests and builds Linux Lambda/AgentCore packages;
3. deploys the serverless core stack.

The explicit confirmation is intentional because AWS resources and model calls can
incur charges.

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

This command deploys only the serverless backend. The application stack has no container
registry or container service. Standard CDK bootstrap tooling may create an empty asset
ECR repository, but this application does not use it at runtime.

## 8. Run the local UI against AWS

```powershell
uv run python scripts/export_outputs.py generated/core-outputs.json
$env:AWS_PROFILE = "mf-prospectus-deployer"
$env:AWS_REGION = "ap-southeast-2"
uv run mf-ui
```

Open the localhost URL printed by Streamlit. The server-side UI reads the exported API
URL, loads temporary credentials from the named AWS profile, and signs requests with
SigV4. The browser never receives AWS credentials.

## 9. Observe and evaluate

Use the stack output `DashboardName` in CloudWatch. Also inspect:

- SQS intake queue age and DLQ depth;
- API/dispatch/validator/reconcile Lambda logs and errors;
- AgentCore managed runtime logs and Lambda X-Ray traces;
- EventBridge completion log group;
- DynamoDB job item status, attempt ID, heartbeat, and error fields.

Run offline evaluation without a paid call:

```powershell
uv run mf-evaluate generated/predictions.json data/human-labels.json
```

Any live Bedrock evaluation must be an explicit, separately labelled operation.

## 10. Stop costs and destroy

The core path has no always-on EC2/ECS server, but requests, retained S3/DynamoDB data,
logs, and model calls can still cost money. Remove the core infrastructure with:

```powershell
npx cdk destroy "mf-prospectus-dev-core"
```

S3 and DynamoDB use `RETAIN` to prevent accidental data loss. After stack deletion,
review and manually delete those retained resources only when you are certain their data
is no longer needed.
