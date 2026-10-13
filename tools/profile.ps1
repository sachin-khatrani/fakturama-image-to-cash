<#
.SYNOPSIS
    Snapshot and restore a Fakturama working profile, so a run can start from a
    known database every time.

.DESCRIPTION
    Fakturama keeps everything -- contacts, products, documents, VAT rates,
    payment terms -- in one working directory. Copying that directory is a far
    more reliable reset than deleting records through the UI, which means
    clicking inside a canvas-rendered list.

    Note that a BRAND NEW profile is not usable: Fakturama refuses to create an
    Order until a default Shipping exists ("No default value found for
    Shippings"), and a fresh database has none. So the useful baseline is
    "defaults configured, no test records" -- which is what `snapshot` is for.
    Take it once, after configuring Fakturama and before booking anything.

.EXAMPLE
    # once, after the profile is configured and still empty of test data
    .\tools\profile.ps1 snapshot

    # before each demo or test run
    .\tools\profile.ps1 reset

    .\tools\profile.ps1 status
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('snapshot', 'reset', 'status')]
    [string]$Action = 'status',

    [string]$Profile  = "$env:USERPROFILE\Fakturama2",
    [string]$Baseline = "$env:USERPROFILE\Fakturama2-baseline",
    [string]$Exe      = "C:\Program Files\Fakturama2\Fakturama.exe"
)

$ErrorActionPreference = 'Stop'

function Stop-Fakturama {
    $running = Get-Process -Name 'Fakturama*' -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host "  stopping Fakturama ..."
        $running | Stop-Process -Force
        Start-Sleep -Seconds 3
    }
}

function Start-Fakturama {
    if (-not (Test-Path $Exe)) {
        Write-Warning "  Fakturama not found at $Exe - start it yourself"
        return
    }
    Write-Host "  starting Fakturama ..."
    Start-Process -FilePath $Exe
}

switch ($Action) {

    'status' {
        Write-Host "profile : $Profile"
        if (Test-Path $Profile) {
            $size = '{0:N1} MB' -f ((Get-ChildItem $Profile -Recurse -File -EA SilentlyContinue |
                                     Measure-Object Length -Sum).Sum / 1MB)
            Write-Host "          exists, $size"
        } else { Write-Host "          MISSING" }

        Write-Host "baseline: $Baseline"
        if (Test-Path $Baseline) {
            Write-Host ("          taken " + (Get-Item $Baseline).LastWriteTime)
        } else {
            Write-Host "          none yet - run: .\tools\profile.ps1 snapshot"
        }
    }

    'snapshot' {
        if (-not (Test-Path $Profile)) { throw "no profile at $Profile" }
        Stop-Fakturama
        if (Test-Path $Baseline) { Remove-Item $Baseline -Recurse -Force }
        Copy-Item $Profile $Baseline -Recurse
        Write-Host "  baseline taken from the current profile -> $Baseline"
        Write-Host "  reset with: .\tools\profile.ps1 reset"
    }

    'reset' {
        if (-not (Test-Path $Baseline)) {
            throw "no baseline at $Baseline. Configure Fakturama, clear any test records, then run: .\tools\profile.ps1 snapshot"
        }
        Stop-Fakturama
        if (Test-Path $Profile) {
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            Move-Item $Profile "$Profile-discarded-$stamp"
            Write-Host "  previous profile kept as $Profile-discarded-$stamp"
        }
        Copy-Item $Baseline $Profile -Recurse
        Write-Host "  profile restored from the baseline"
        Start-Fakturama
    }
}
