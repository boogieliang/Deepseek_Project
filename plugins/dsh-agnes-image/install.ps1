<#
.SYNOPSIS
    Install (or reinstall) dsh-agnes-image into a DeepSeek Harness profile.

.DESCRIPTION
    Adds this plugin directory as a profile dependency and, when -ApiKey is
    supplied, stores it under the AGNES_API_KEY credential reference. The key
    is never echoed. Re-run after editing plugin sources: pnpm keeps a copy
    under the profile's node_modules, so a fresh add is what refreshes it.

.PARAMETER ApiKey
    Agnes API key. Omit to keep the existing credential reference untouched.

.PARAMETER Profile
    DSH profile name. Defaults to desktop.

.PARAMETER DshCli
    Path to dsh.cmd. Auto-detected from PATH or the standard Desktop install.

.PARAMETER Reinstall
    Remove the package first so pnpm copies the current sources. Use after
    editing index.js or lib/.

.EXAMPLE
    .\install.ps1 -ApiKey "sk-..."

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 -Reinstall
#>
param(
    [string]$ApiKey = $env:AGNES_API_KEY,
    [string]$Profile = 'desktop',
    [string]$DshCli = '',
    [switch]$Reinstall
)

# Native pnpm output on stderr should be reported, not abort the script.
$ErrorActionPreference = 'Continue'

if (-not $DshCli) {
    $command = Get-Command dsh -ErrorAction SilentlyContinue
    if ($command) {
        $DshCli = $command.Source
    } else {
        $candidate = Join-Path $env:LOCALAPPDATA 'Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd'
        if (Test-Path -LiteralPath $candidate) { $DshCli = $candidate }
    }
}
if (-not $DshCli -or -not (Test-Path -LiteralPath $DshCli)) {
    throw 'dsh CLI not found; pass -DshCli <path to dsh.cmd>.'
}

$pluginRoot = $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $pluginRoot 'index.js'))) {
    throw "index.js not found under $pluginRoot"
}

$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$credentialsPath = Join-Path $dshHome '.credentials.yaml'

if ($ApiKey) {
    if ($ApiKey -match '\s') { throw 'ApiKey must not contain whitespace.' }
    if (-not (Test-Path -LiteralPath $credentialsPath)) {
        throw "credentials file not found: $credentialsPath (start DeepSeek Harness Desktop once)"
    }
    $text = [System.IO.File]::ReadAllText($credentialsPath)
    if ($text -match '(?m)^\s*AGNES_API_KEY:') {
        Write-Host 'credential AGNES_API_KEY already present; leaving it unchanged.'
    } else {
        if ($text -notmatch '(?m)^refs:\s*$') { throw "no refs section in $credentialsPath; refusing to guess the layout." }
        $key = $ApiKey.Trim()
        $newText = [regex]::Replace($text, '(?m)^(refs:\s*\r?\n)', {
            param($match)
            $match.Value + "  AGNES_API_KEY: $key`n"
        }, 1)
        [System.IO.File]::WriteAllText($credentialsPath, $newText, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host 'credential AGNES_API_KEY written.'
    }
} else {
    Write-Host 'no ApiKey supplied; existing credential reference left as is.'
}

Write-Host "installing $pluginRoot into profile '$Profile' ..."
if ($Reinstall) {
    Write-Host 'removing the existing package first so current sources are copied ...'
    & $DshCli plugin --profile $Profile remove dsh-agnes-image
}
& $DshCli plugin --profile $Profile add "file:$pluginRoot"
if ($LASTEXITCODE -ne 0) { throw "dsh plugin add failed with exit code $LASTEXITCODE" }

Write-Host ''
Write-Host 'Done. If agnes_generate_image does not appear, fully quit and restart DeepSeek Harness.'
Write-Host 'A browser refresh is not a restart; profile bundles mount at process start.'
