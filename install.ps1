# Mike installer for Windows 10/11.
#
# One line, in PowerShell:
#   irm https://raw.githubusercontent.com/navdeepcodes/mike/main/install.ps1 | iex
# or, from a downloaded copy of the repo:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# What it does: finds Python 3.10-3.12 (offers to install 3.12 with winget), puts Mike in
# %LOCALAPPDATA%\Mike (unless run from a copy of the repo), installs his packages in a private .venv,
# downloads the voice models (~150 MB), and adds Mike to the Start menu and desktop.
# Options (environment variables): MIKE_DIR=folder, MIKE_AUTOSTART=1 (start at login),
# MIKE_NO_MODELS=1, MIKE_NO_SHORTCUTS=1.
$Dir = "$env:MIKE_DIR"
$NoShortcuts = $env:MIKE_NO_SHORTCUTS -eq "1"
$NoModels = $env:MIKE_NO_MODELS -eq "1"
$Autostart = $env:MIKE_AUTOSTART -eq "1"
# Native tools print warnings on stderr; Windows PowerShell 5.1 would treat those as fatal under "Stop".
# Failures are checked explicitly instead ($LASTEXITCODE, -ErrorAction Stop).
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Repo = "navdeepcodes/mike"
$Branch = "main"

function Say($m) { Write-Host "-> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "x $m" -ForegroundColor Red; exit 1 }

# ---- where Mike lives ---------------------------------------------------------------------
$here = if ($PSScriptRoot) { $PSScriptRoot } else { "" }
if (-not $Dir) {
    if ($here -and (Test-Path (Join-Path $here "server.py"))) { $Dir = $here }
    else { $Dir = Join-Path $env:LOCALAPPDATA "Mike" }
}
if (-not (Test-Path (Join-Path $Dir "server.py"))) {
    Say "Downloading Mike to $Dir"
    $zip = Join-Path $env:TEMP "mike-$Branch.zip"
    Invoke-WebRequest "https://github.com/$Repo/archive/refs/heads/$Branch.zip" -OutFile $zip -UseBasicParsing -ErrorAction Stop
    $tmp = Join-Path $env:TEMP "mike-src"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip $tmp -ErrorAction Stop
    $src = Get-ChildItem $tmp | Select-Object -First 1
    New-Item -ItemType Directory -Force $Dir | Out-Null
    Copy-Item (Join-Path $src.FullName "*") $Dir -Recurse -Force
    Remove-Item $zip, $tmp -Recurse -Force
}
Set-Location $Dir

# ---- Python ---------------------------------------------------------------------------------
function Find-Python {
    foreach ($v in "3.12", "3.11", "3.10") {
        try {
            $p = (& py "-$v" -c "import sys; print(sys.executable)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
        } catch {}
    }
    foreach ($name in "python", "python3") {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike "*WindowsApps*") {
            $ok = & $cmd.Source -c "import sys; print(int((3,10) <= sys.version_info[:2] <= (3,12)))" 2>$null
            if ($ok -eq "1") { return $cmd.Source }
        }
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $a = Read-Host "Mike needs Python 3.12. Install it now with winget? [Y/n]"
        if ($a -eq "" -or $a -match "^[Yy]") {
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
            $py = Find-Python
        }
    }
    if (-not $py) { Fail "Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add to PATH'), then run this again." }
}
Say "Using Python: $py"

# ---- packages -------------------------------------------------------------------------------
$venvPy = Join-Path $Dir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Say "Creating Mike's private Python environment"
    & $py -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Couldn't create the virtual environment." }
}
Say "Installing Mike's packages (a few minutes the first time)"
& $venvPy -m pip install --upgrade pip -q --disable-pip-version-check
& $venvPy -m pip install -r requirements.txt -q
if ($LASTEXITCODE -ne 0) { Fail "Package install failed. Scroll up for the error." }

if (-not $NoModels) {
    Say "Downloading the voice models (~150 MB, once)"
    & $venvPy models.py
    if ($LASTEXITCODE -ne 0) { Write-Host "  (Mike will retry the download when he starts.)" -ForegroundColor Yellow }
}

# ---- shortcuts ------------------------------------------------------------------------------
if (-not $NoShortcuts) {
    Say "Adding Mike to the Start menu and desktop"
    New-Item -ItemType Directory -Force (Join-Path $Dir "data") | Out-Null
    $ico = Join-Path $Dir "data\mike.ico"
    & $venvPy -c "from PIL import Image; Image.open(r'app\icon-1024.png').save(r'$ico', sizes=[(16,16),(32,32),(48,48),(256,256)])" 2>$null
    $pyw = Join-Path $Dir ".venv\Scripts\pythonw.exe"
    $launch = Join-Path $Dir "launch.py"
    $shell = New-Object -ComObject WScript.Shell
    $links = @(
        (Join-Path ([Environment]::GetFolderPath("Programs")) "Mike.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "Mike.lnk")
    )
    foreach ($t in $links) {
        $s = $shell.CreateShortcut($t)
        $s.TargetPath = $pyw
        $s.Arguments = "`"$launch`""
        $s.WorkingDirectory = $Dir
        if (Test-Path $ico) { $s.IconLocation = $ico }
        $s.Description = "Mike, your voice assistant"
        $s.Save()
    }
    if ($Autostart) {
        $s = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Startup")) "Mike.lnk"))
        $s.TargetPath = $pyw
        $s.Arguments = "`"$launch`" --background"
        $s.WorkingDirectory = $Dir
        $s.Save()
    }
}

Write-Host ""
Write-Host "Mike is installed in $Dir" -ForegroundColor Green
Write-Host "Start him from the Start menu or the desktop (Mike), allow the microphone, and say 'Hey Mike'."
Write-Host "Add a free model key in Settings (Groq, Gemini, OpenRouter or NVIDIA), or install Claude Code to use your Claude plan."
