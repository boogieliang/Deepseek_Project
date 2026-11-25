# Build the standalone Windows executable.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Build environment missing. Run: python -m venv .venv; .\.venv\Scripts\pip install pyinstaller websocket-client pillow"
}

if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "SmartFormFiller" `
    --icon "assets\icon.ico" `
    --add-data "assets;assets" `
    --version-file "version_info.txt" `
    --hidden-import "websocket" `
    --hidden-import "tkinter" `
    --exclude-module "tests" `
    --runtime-tmpdir "." `
    app.py

if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

# A portable folder version starts instantly and does not need a writable
# system TEMP directory, which matters on locked-down or managed machines.
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name "SmartFormFillerPortable" `
    --icon "assets\icon.ico" `
    --add-data "assets;assets" `
    --version-file "version_info.txt" `
    --hidden-import "websocket" `
    --hidden-import "tkinter" `
    --exclude-module "tests" `
    app.py

if ($LASTEXITCODE -ne 0) { throw "Portable PyInstaller failed with exit code $LASTEXITCODE" }

Copy-Item "README.md" "dist\使用说明.md" -ErrorAction SilentlyContinue
Copy-Item "README.md" "dist\SmartFormFillerPortable\使用说明.md" -Force -ErrorAction SilentlyContinue
Copy-Item "assets\welcome.html" "dist\示例页面.html" -ErrorAction SilentlyContinue
Copy-Item "assets\welcome.html" "dist\SmartFormFillerPortable\示例页面.html" -Force -ErrorAction SilentlyContinue
if (Test-Path "docs") {
    Copy-Item "docs" "dist\docs" -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item "docs" "dist\SmartFormFillerPortable\docs" -Recurse -Force -ErrorAction SilentlyContinue
}
Compress-Archive -Path "dist\SmartFormFillerPortable" -DestinationPath "dist\SmartFormFillerPortable.zip" -Force

Write-Host ""
Write-Host "Build complete: $Root\dist\SmartFormFiller.exe"
Write-Host "Portable build: $Root\dist\SmartFormFillerPortable\SmartFormFillerPortable.exe"
Write-Host "Portable zip:   $Root\dist\SmartFormFillerPortable.zip"
Write-Host "Run selftest:   .\dist\SmartFormFiller.exe --selftest .\selftest-result.json"
exit 0
