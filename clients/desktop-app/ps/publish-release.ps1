# Publishes the built installer as a GitHub release, which every installed app then offers as an update (they ask
# first). Run ps\build-windows.ps1 first. Needs the GitHub CLI, signed in with rights to the repo (gh auth login).
# Run from clients\desktop-app:
#   powershell -ExecutionPolicy Bypass -File ps\build-windows.ps1
#   powershell -ExecutionPolicy Bypass -File ps\publish-release.ps1 [-Notes "What changed"] [-Repo owner/name] [-Draft]
#
# What it does:
#   1. reads the version from Directory.Build.props and finds dist\Mnemerynne-Desktop-Setup-<version>.exe
#   2. writes dist\SHA256SUMS (the app also checks GitHub's own record of the upload; this is the fallback)
#   3. creates the release desktop-v<version> on the commit main is at, with the installer and SHA256SUMS attached
# The tag must be new: a version is published once. Raise <Version> in Directory.Build.props for the next one.
# -Draft makes it a draft (apps don't see drafts): publish it on GitHub when ready.
param([string]$Notes = "", [string]$Repo = "AELovelace/erinne", [switch]$Draft)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)        # clients\desktop-app

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "The GitHub CLI isn't installed: winget install GitHub.cli" }
gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { throw "The GitHub CLI isn't signed in: gh auth login" }

$version = ([xml](Get-Content Directory.Build.props)).Project.PropertyGroup.Version
$name = "Mnemerynne-Desktop-Setup-$version.exe"
$setup = Join-Path "dist" $name
if (-not (Test-Path $setup)) { throw "$setup isn't built yet: run ps\build-windows.ps1 first" }
$tag = "desktop-v$version"

# A release is made once per version: stop before anything is uploaded if this one exists.
gh release view $tag --repo $Repo 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { throw "$tag is already released on $Repo. Raise <Version> in Directory.Build.props and build again." }

# The checksum file (two spaces between hash and name, as sha256sum writes it).
$sha = (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLowerInvariant()
$sums = Join-Path "dist" "SHA256SUMS"
Set-Content -Path $sums -Value "$sha  $name" -NoNewline -Encoding ascii

if (-not $Notes) { $Notes = "Mnemerynne Desktop $version for Windows (64-bit). Installed apps offer it as an update." }
$ghArgs = @("release", "create", $tag, $setup, $sums, "--repo", $Repo, "--target", "main",
          "--title", "Mnemerynne Desktop $version", "--notes", $Notes)
if ($Draft) { $ghArgs += "--draft" }   # apps ignore drafts until published
gh @ghArgs
if ($LASTEXITCODE -ne 0) { throw "gh release create failed" }

Write-Output "Released $tag on $Repo"
Write-Output "Installer SHA-256: $sha"
Write-Output "Installed apps see it at their next check (start, daily, or ⚙ → Check for updates)."
