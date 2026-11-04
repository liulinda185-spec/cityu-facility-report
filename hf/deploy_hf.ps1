# Upload hf_deploy/ folders to two Hugging Face Spaces via git.
# Usage: .\deploy_hf.ps1 -User <HF-username>
# You will be prompted for a Hugging Face Access Token (Settings -> Access Tokens,
# create with "write" permission). The token is used only for the git push.
param([Parameter(Mandatory = $true)][string]$User)
$ErrorActionPreference = 'Stop'

$token = Read-Host "Hugging Face Access Token (write permission)" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($token))

function Push-Space {
    param([string]$Name, [string]$SrcDir)
    $tmp = Join-Path $env:TEMP "hf_$Name"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    git clone "https://user:$plain@huggingface.co/spaces/$User/$Name" $tmp | Out-Null
    Copy-Item -Recurse -Force (Join-Path $SrcDir '*') $tmp
    Push-Location $tmp
    git add -A
    git -c user.name="$User" -c user.email="$User@users.noreply.huggingface.co" commit -m "deploy $Name"
    git push
    Pop-Location
    Remove-Item -Recurse -Force $tmp
}

Push-Space -Name 'cityu-facility-report' -SrcDir (Join-Path $PSScriptRoot 'cityu-facility-report')
Push-Space -Name 'cityu-llm-demo' -SrcDir (Join-Path $PSScriptRoot 'cityu-llm-demo')
Write-Host "Done. Check https://huggingface.co/spaces/$User/cityu-facility-report and .../cityu-llm-demo"
