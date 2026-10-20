param(
    [string]$Region = 'us-east-1',
    [string]$Profile = 'default'
)

$ErrorActionPreference = 'Stop'

# Resolve the Windows installation even if VS Code still has an older PATH.
$awsCommand = Get-Command aws -ErrorAction SilentlyContinue
if ($awsCommand) {
    $awsExecutable = $awsCommand.Source
} else {
    $awsExecutable = Join-Path $env:ProgramFiles 'Amazon\AWSCLIV2\aws.exe'
    if (-not (Test-Path -LiteralPath $awsExecutable)) {
        throw 'Install AWS CLI v2, then run this script again.'
    }
}

$awsOptions = @(
    '--profile', $Profile, '--region', $Region,
    '--no-cli-pager', '--no-cli-auto-prompt',
    '--cli-connect-timeout', '10', '--cli-read-timeout', '20'
)

Write-Host "Checking AWS profile '$Profile' in $Region. These checks create no resources."

$identityJson = & $awsExecutable sts get-caller-identity @awsOptions --output json
if ($LASTEXITCODE -ne 0) {
    Write-Host 'AWS login failed. Start Learner Lab and copy the current AWS Details > AWS CLI credentials'
    Write-Host "into $env:USERPROFILE\.aws\credentials under [$Profile], including aws_session_token."
    Write-Host 'Refresh those credentials if the lab session has expired.'
    exit 1
}

$identity = $identityJson | ConvertFrom-Json
Write-Host "Account: $($identity.Account)"
Write-Host "Identity: $($identity.Arn)"

$checksPassed = $true
& $awsExecutable ecr describe-repositories @awsOptions --max-items 1 --output json
if ($LASTEXITCODE -ne 0) {
    Write-Host 'ECR repository read failed. Check the error above and the services allowed by your lab.'
    $checksPassed = $false
} else {
    Write-Host 'ECR repository read: OK (an empty list is fine).'
}

& $awsExecutable eks list-clusters @awsOptions --max-items 1 --output json
if ($LASTEXITCODE -ne 0) {
    Write-Host 'EKS cluster list failed. Check the error above and your lab Readme service restrictions.'
    Write-Host 'If EKS is denied, ask your instructor for EKS access; the proposal requires EKS.'
    $checksPassed = $false
} else {
    Write-Host 'EKS cluster list: OK (an empty list is fine).'
}

if (-not $checksPassed) {
    exit 1
}

Write-Host 'Read checks passed. Next: publish the sandbox image to ECR, then configure an EKS Job.'
Write-Host 'These checks do not prove permission to create repositories, clusters, or IAM roles.'
