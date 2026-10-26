# ADAM local check gate.
# Run from the repository root:  pwsh -File scripts/check.ps1
$ErrorActionPreference = "Stop"

Write-Host "== compileall =="
python -m compileall -q adam scripts tools
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== pytest =="
python -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "OK"
