param([switch]$BuildOnly)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodePath = if ($nodeCommand) { $nodeCommand.Source } else {
    Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
}
if (!(Test-Path -LiteralPath $nodePath)) { throw 'Node is not available. Install Node 24 or open the project in local Codex.' }
if (!(Test-Path -LiteralPath (Join-Path $root 'node_modules\vite\bin\vite.js'))) { throw 'Frontend dependencies are absent. Run npm ci from the project folder.' }
Push-Location $root
try {
    if ($BuildOnly) { & $nodePath 'node_modules/vite/bin/vite.js' build }
    else { & $nodePath 'scripts/dev.mjs' --demo }
    if ($LASTEXITCODE -ne 0) { throw 'Local frontend command failed.' }
} finally { Pop-Location }
