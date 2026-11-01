# Mutual Fund Prospectus Restriction Extractor

This AWS-first application extracts fund-level investment restrictions and source
evidence from mutual-fund prospectuses. A local Streamlit UI submits jobs to an
IAM-protected AWS API. Processing uses S3, DynamoDB, SQS, five Lambdas, Amazon Bedrock
AgentCore Runtime, Bedrock Converse, EventBridge, and CloudWatch.

The project does **not** require Docker, EC2, ECS/Fargate, ECR, RAG, a vector database,
Step Functions, EKS, or a NAT gateway. Tests use deterministic mocks, but the deployed
processing backend is AWS-only.

## One-time AWS connection

```powershell
. .\scripts\setup_aws_connection.ps1 `
  -Region ap-southeast-2 `
  -ExpectedAccountId "123456789012" `
  -AuthenticationMode login
```

This persistent named profile is saved by the AWS CLI in your Windows user profile,
not in the repository. `.env` stores only non-secret selectors such as profile name,
account, region, the exact `BEDROCK_MODEL_ID`, and its non-secret resource ARNs.

## AWS deployment overview

Deployment is intentionally explicit. Read [docs/AWS_DEPLOYMENT.md](docs/AWS_DEPLOYMENT.md)
before running any AWS command. At a high level you will:

1. configure one isolated AWS profile, region, and account ID;
2. choose a Bedrock vision-capable model ID available in that region;
3. verify credentials and run tests;
4. build Linux-compatible Lambda and AgentCore packages;
5. bootstrap, synthesize, review, and deploy the serverless CDK stack;
6. export stack outputs for the local UI;
7. run `uv run mf-ui` and submit prospectuses to the AWS backend.

## GitHub automation

`.github/workflows/ci.yml` runs on pushes and pull requests. It runs lint, tests, and
builds the Linux Lambda/AgentCore packages, but never authenticates to AWS or deploys.

`.github/workflows/deploy.yml` runs only through **Run workflow**. It requires the
operator to type `DEPLOY`, authenticates with short-lived GitHub OIDC credentials, and
deploys only the serverless core stack. No permanent AWS access key is stored in GitHub,
the repository, or the application. The current AWS Project account blocks creation of
the OIDC provider; local profile-based CDK deployment remains the supported path until
AWS enables that IAM capability.

The architecture and state/ownership rules are documented in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Important safety limits

- PDF only, maximum 20 pages and 20 MiB by default.
- Encrypted, corrupt, oversized, and pathological PDFs are rejected before Bedrock.
- Model calls are batched at at most two pages with a call and wall-clock circuit breaker.
- Browser clients never receive AWS access keys; API calls are signed server-side.
- Every runtime write is fenced by `attempt_id`, preventing stale workers from winning.
- Terminal jobs are immutable; a retry creates a new job ID.
- LLM bounding boxes are always `UNVERIFIED` until a human confirms or rejects them.

## Main folders

- `app/agent`: LangGraph pipeline, prompts, mock/Bedrock adapters, and graph state.
- `app/parsing`: PyMuPDF validation, extraction, rendering, and page selection.
- `app/validation`: deterministic input and output validation.
- `app/ui`: local Streamlit application and SigV4 API client.
- `lambdas`: API, dispatch, input validator, output validator, and reconciler.
- `infra`: AWS CDK stacks.
- `scripts`: local run, evaluation, packaging, account verification, output export.
- `tests`: no-cost unit and local integration tests.
- `generated`: ignored runtime/deployment artifacts; only `.gitkeep` files are committed.
