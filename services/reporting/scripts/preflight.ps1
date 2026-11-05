param(
    [string]$SshAlias,
    [string]$SshConfig = (Join-Path $env:USERPROFILE '.ssh\config'),
    [switch]$LocalOnly
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This entry point requires the local Windows executor.' }
$root = Split-Path -Parent $PSScriptRoot
foreach ($name in @('AGENTS.md','sync.py','compose.yaml')) {
    if (!(Test-Path -LiteralPath (Join-Path $root $name))) { throw "Wrong project directory: missing $name" }
}
foreach ($name in @('git','ssh','scp')) { Get-Command $name -ErrorAction Stop | Out-Null }
Write-Output "Local project: $root"
& git -C $root rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0) { throw 'Project is not initialized as Git.' }
if ($LocalOnly) { Write-Output 'Local preflight OK; SSH not checked.'; return }
if (!$SshAlias -or $SshAlias -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') { throw 'Pass a configured SSH alias using -SshAlias.' }
if (!(Test-Path -LiteralPath $SshConfig)) { throw 'SSH config is not visible to this executor.' }
& ssh -F $SshConfig -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 $SshAlias 'id -un'
if ($LASTEXITCODE -ne 0) { throw 'SSH failed. Do not claim server access or ask the user to copy code before diagnosing this result.' }
Write-Output 'Local executor and SSH verified.'
