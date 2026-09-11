$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $projectRoot ".build\windows-build-venv"
$python = Join-Path $venv "Scripts\python.exe"
$releaseRoot = Join-Path $projectRoot "public_release"
$backendRoot = Join-Path $releaseRoot "_FACTS_backend"

if (Test-Path -LiteralPath $releaseRoot) {
    Remove-Item -LiteralPath $releaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Path `
    $releaseRoot, `
    $backendRoot, `
    (Join-Path $backendRoot "configs"), `
    (Join-Path $backendRoot "source\windows"), `
    (Join-Path $backendRoot "source\packaging") | Out-Null

if (-not (Test-Path -LiteralPath $python)) {
    py -3 -m venv $venv
}

& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $PSScriptRoot "requirements-desktop.txt")
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "Launch FACTS Dashboard" `
    --distpath $releaseRoot `
    --workpath (Join-Path $projectRoot ".build\pyinstaller-facts-native") `
    --specpath (Join-Path $projectRoot ".build\spec-facts-native") `
    (Join-Path $projectRoot "windows\facts_desktop.py")

Copy-Item -LiteralPath `
    (Join-Path $projectRoot "facts_dashboard.py"), `
    (Join-Path $projectRoot "facts-dashboard"), `
    (Join-Path $projectRoot "run_facts_slr.sh"), `
    (Join-Path $projectRoot "setup_facts.sh"), `
    (Join-Path $projectRoot "Launch FACTS Dashboard.cmd"), `
    (Join-Path $projectRoot "README.md"), `
    (Join-Path $projectRoot "THIRD_PARTY_NOTICES.md") `
    -Destination $backendRoot -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "configs\location_48gauges.lst") `
    -Destination (Join-Path $backendRoot "configs") -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "windows\facts_desktop.py") `
    -Destination (Join-Path $backendRoot "source\windows") -Force
Copy-Item -LiteralPath `
    (Join-Path $projectRoot "packaging\build_windows.ps1"), `
    (Join-Path $projectRoot "packaging\requirements-desktop.txt") `
    -Destination (Join-Path $backendRoot "source\packaging") -Force

Write-Host "Release created at $releaseRoot"
