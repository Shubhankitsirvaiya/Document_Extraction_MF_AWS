<#
.SYNOPSIS
Configures the one-time developer connection used to deploy this AWS-only project.

.DESCRIPTION
The script creates or reuses one named AWS CLI profile, verifies the target account,
and writes only non-secret deployment selectors to the project's ignored .env file.
AWS credentials remain in the standard per-user AWS CLI location. After deployment,
Lambda and AgentCore use IAM roles; no local key is copied into deployed workloads.

Run this script by dot-sourcing it so the AWS_PROFILE variables remain in the current
PowerShell session:

  . .\scripts\setup_aws_connection.ps1 -Region ap-southeast-2 `
      -ExpectedAccountId "123456789012" -AuthenticationMode login
#>

[CmdletBinding()]
param(
    [string]$ProfileName = "mf-prospectus-deployer",

    [Parameter(Mandatory = $true)]
    [string]$Region,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9]{12}$")]
    [string]$ExpectedAccountId,

    [ValidateSet("login", "sso", "access-key")]
    [string]$AuthenticationMode = "login"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    throw "AWS CLI v2 is required. Install it, open a new terminal, and rerun this script."
}

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$envPath = Join-Path $projectRoot ".env"
$envExamplePath = Join-Path $projectRoot ".env.example"

# Configure credentials only when this named profile does not already exist.
$profiles = @(aws configure list-profiles)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read AWS CLI profiles."
}

if ($profiles -notcontains $ProfileName) {
    if ($AuthenticationMode -eq "login") {
        Write-Host "Creating browser-authenticated profile '$ProfileName'..."
    }
    elseif ($AuthenticationMode -eq "sso") {
        Write-Host "Creating IAM Identity Center profile '$ProfileName'..."
        aws configure sso --profile $ProfileName
    }
    else {
        Write-Warning "Access keys are long-lived. Prefer -AuthenticationMode sso when available."
        aws configure --profile $ProfileName
    }
    if ($LASTEXITCODE -ne 0) {
        throw "AWS profile configuration did not complete successfully."
    }
}

if ($AuthenticationMode -eq "login") {
    # AWS Project accounts use browser authentication and short-lived credentials.
    # The CLI securely caches and refreshes the session; no access key enters .env.
    aws sts get-caller-identity --profile $ProfileName --region $Region 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        aws login --profile $ProfileName --region $Region
        if ($LASTEXITCODE -ne 0) {
            throw "AWS browser login failed. Confirm that the project supports this Region."
        }
    }
    else {
        Write-Host "Reusing the active browser-authenticated session for '$ProfileName'."
    }
}
elseif ($AuthenticationMode -eq "sso") {
    # The profile is persistent; SSO login refreshes a temporary local session token.
    aws sso login --profile $ProfileName
    if ($LASTEXITCODE -ne 0) {
        throw "AWS IAM Identity Center login failed."
    }
}

aws configure set region $Region --profile $ProfileName
aws configure set output json --profile $ProfileName

$identityJson = aws sts get-caller-identity --profile $ProfileName --region $Region
if ($LASTEXITCODE -ne 0) {
    throw "AWS credentials could not call sts:GetCallerIdentity."
}
$identity = $identityJson | ConvertFrom-Json

if ($identity.Account -ne $ExpectedAccountId) {
    throw "Account mismatch. Expected $ExpectedAccountId but authenticated to $($identity.Account)."
}
$verifiedAccountId = $identity.Account

if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath $envExamplePath -Destination $envPath
}

function Set-ProjectEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in [System.IO.File]::ReadAllLines($envPath)) {
        $lines.Add($line)
    }
    $prefix = "$Name="
    $replaced = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index].StartsWith($prefix, [System.StringComparison]::Ordinal)) {
            $lines[$index] = "$prefix$Value"
            $replaced = $true
            break
        }
    }
    if (-not $replaced) {
        $lines.Add("$prefix$Value")
    }
    [System.IO.File]::WriteAllLines($envPath, $lines)
}

# These are deployment selectors, not credentials. The ignored .env file is safe to
# regenerate, but it must still never be committed.
Set-ProjectEnvValue -Name "APP_MODE" -Value "aws"
Set-ProjectEnvValue -Name "AWS_PROFILE" -Value $ProfileName
Set-ProjectEnvValue -Name "AWS_REGION" -Value $Region
Set-ProjectEnvValue -Name "EXPECTED_AWS_ACCOUNT_ID" -Value $verifiedAccountId

# Because this script is dot-sourced, these variables remain available to AWS CLI,
# boto3, and CDK commands executed later in the same PowerShell window.
$env:AWS_PROFILE = $ProfileName
$env:AWS_REGION = $Region
$env:CDK_DEFAULT_REGION = $Region
$env:CDK_DEFAULT_ACCOUNT = $verifiedAccountId
$env:EXPECTED_AWS_ACCOUNT_ID = $verifiedAccountId

Write-Host "AWS connection configured successfully."
Write-Host "Profile : $ProfileName"
Write-Host "Account : $verifiedAccountId"
Write-Host "Region  : $Region"
Write-Host "Caller  : $($identity.Arn)"
Write-Host "Config  : $envPath (contains no AWS secret key)"
Write-Host "Next: set BEDROCK_MODEL_ID in .env, then follow docs/AWS_DEPLOYMENT.md."
