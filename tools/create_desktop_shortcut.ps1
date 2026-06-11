# Creates a Desktop shortcut that launches tools/main.py via run_main.bat,
# using whichever virtual environment (.venv or venv) exists in the repo.
# Run this script from any clone of the repo on any Windows machine.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $repoRoot "tools\run_main.bat"

if (-not (Test-Path $launcher)) {
    throw "Launcher not found at $launcher"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "PLC Sim Tools.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $repoRoot
$shortcut.IconLocation = "shell32.dll,137"
$shortcut.Description = "Launch PLC Sim Tools (tools/main.py)"
$shortcut.Save()

Write-Host "Shortcut created: $shortcutPath"
