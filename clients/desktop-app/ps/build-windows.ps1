# Builds the Mnemerynne Desktop installer: dist\Mnemerynne-Desktop-Setup-<version>.exe (per user, no admin rights).
#
# Needs: the .NET 10 SDK (winget install Microsoft.DotNet.SDK.10) and Inno Setup 6
#        (winget install JRSoftware.InnoSetup --scope user).
# Run from clients\desktop-app:
#   powershell -ExecutionPolicy Bypass -File ps\build-windows.ps1 [-SkipTests]
#
# What it does, in order:
#   1. runs the unit tests (dotnet test), unless -SkipTests
#   2. publishes the app self-contained for 64-bit Windows into publish\ (no .NET needed on the PC that installs it)
#   3. runs Inno Setup on packaging\installer.iss with the version from Directory.Build.props
#   4. prints the installer's path and SHA-256 (the server announces the same hash to the app's updater)
param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)        # clients\desktop-app, wherever it was started from

function Find-Iscc {
    # Inno Setup's compiler: on PATH, or where a per-user / machine-wide install puts it.
    $found = Get-Command iscc -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    foreach ($p in "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                   "$env:ProgramFiles\Inno Setup 6\ISCC.exe") {
        if (Test-Path $p) { return $p }
    }
    throw "Inno Setup 6 isn't installed: winget install JRSoftware.InnoSetup --scope user"
}

# The one version number (Directory.Build.props <Version>): the installer's name, the app's About, the updater.
$version = ([xml](Get-Content Directory.Build.props)).Project.PropertyGroup.Version
if (-not $version) { throw "No <Version> in Directory.Build.props" }
Write-Output "Building Mnemerynne Desktop $version"

$iscc = Find-Iscc                                       # fail early, before the long build

if (-not $SkipTests) {
    dotnet test tests\Mnemerynne.Core.Tests -c Release --nologo
    if ($LASTEXITCODE -ne 0) { throw "Unit tests failed" }
}

if (Test-Path publish) { Remove-Item -Recurse -Force publish }   # never ship leftovers from an older build
dotnet publish src\Mnemerynne.Desktop -c Release -r win-x64 --self-contained true -p:PublishReadyToRun=true -o publish --nologo
if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed" }

& $iscc /Q "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$setup = Resolve-Path "dist\Mnemerynne-Desktop-Setup-$version.exe"
$sha = (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLowerInvariant()
# Sign it if you have a code-signing certificate (unsigned, SmartScreen warns once per new version):
#   signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 "$setup"
Write-Output "Installer: $setup"
Write-Output "SHA-256:   $sha"
Write-Output "Offer it: copy it into <MNEMERYNNE_DATA_DIR>\desktop-app\ on the server (see README.md, Publishing)."
