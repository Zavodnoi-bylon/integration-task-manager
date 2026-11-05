param(
    [Parameter(Mandatory=$true)][string]$SshAlias,
    [string]$SshConfig = (Join-Path $env:USERPROFILE '.ssh\config'),
    [ValidateRange(1024,65535)][int]$LocalPort = 8480,
    [ValidateRange(1024,65535)][int]$RemotePort = 8480
)
$ErrorActionPreference = 'Stop'
if ($SshAlias -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') { throw 'Invalid SSH alias.' }
Write-Output "Dashboard: http://127.0.0.1:$LocalPort ; keep this terminal open. Ctrl+C closes the tunnel."
& ssh -F $SshConfig -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -N -L "127.0.0.1:${LocalPort}:127.0.0.1:${RemotePort}" $SshAlias
if ($LASTEXITCODE -ne 0) { throw 'Dashboard tunnel stopped with an error.' }
