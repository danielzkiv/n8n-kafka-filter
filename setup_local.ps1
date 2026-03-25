$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

Write-Host ""
Write-Host "=== n8n-kafka-filter local setup ===" -ForegroundColor Cyan

# 1. Find or install Python
function Find-Python {
    foreach ($cmd in @("python", "python3", "py")) {
        try {
            $ver = & $cmd --version 2>&1
            if ($ver -match "Python 3") { return $cmd }
        } catch {}
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host "Python not found. Installing via winget..." -ForegroundColor Yellow
    try {
        winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        $python = Find-Python
    } catch {}
}

if (-not $python) {
    Write-Host "Trying direct download..." -ForegroundColor Yellow
    $installer = "$env:TEMP\python-installer.exe"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe" -OutFile $installer
    Start-Process -FilePath $installer -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1" -Wait
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    $python = Find-Python
}

if (-not $python) {
    Write-Host "Could not install Python automatically." -ForegroundColor Red
    Write-Host "Please install manually from https://www.python.org/downloads/" -ForegroundColor White
    Write-Host "Check 'Add Python to PATH', then re-run this script." -ForegroundColor White
    exit 1
}

$ver = & $python --version 2>&1
Write-Host "Python: OK ($ver)" -ForegroundColor Green

# 2. Install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
& $python -m pip install -r requirements.txt --quiet
Write-Host "Dependencies: OK" -ForegroundColor Green

# 3. Get webhook.site URL
Write-Host "Getting webhook URL..." -ForegroundColor Yellow
$webhookUrl = "https://webhook.site/REPLACE-ME"
try {
    $r = Invoke-RestMethod -Uri "https://webhook.site/token" -Method POST
    $webhookUrl = "https://webhook.site/" + $r.uuid
    Write-Host "Webhook URL: $webhookUrl" -ForegroundColor Green
} catch {
    Write-Host "Could not auto-create URL. Go to https://webhook.site and paste your URL into .env" -ForegroundColor Yellow
}

# 4. Write .env
$pipelines = '[{"name":"dev","ingest_secret":"dev-secret","n8n_webhook_url":"' + $webhookUrl + '","filter_mode":"none","filter_rules":[]},{"name":"stage","ingest_secret":"stage-secret","n8n_webhook_url":"' + $webhookUrl + '","filter_mode":"none","filter_rules":[]},{"name":"prod","ingest_secret":"prod-secret","n8n_webhook_url":"' + $webhookUrl + '","filter_mode":"none","filter_rules":[]}]'
$lines = @("LOG_LEVEL=DEBUG", "SERVICE_NAME=kafka-n8n-forwarder", "", "PIPELINES_JSON=$pipelines")
$lines | Set-Content -Path ".env" -Encoding UTF8
Write-Host ".env written" -ForegroundColor Green

# 5. Done
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Ready to test!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "STEP 1 - Run in THIS terminal:" -ForegroundColor White
Write-Host "  $python -m app.main" -ForegroundColor Yellow
Write-Host ""
Write-Host "STEP 2 - Open a NEW terminal and run:" -ForegroundColor White
Write-Host '  curl -X POST http://localhost:8080/ingest/dev -H "Content-Type: application/json" -H "X-Ingest-Secret: dev-secret" -d "{\"event_type\":\"order.created\",\"payload\":{\"amount\":100}}"' -ForegroundColor Yellow
Write-Host ""
Write-Host "STEP 3 - See the event arrive at:" -ForegroundColor White
Write-Host "  $webhookUrl" -ForegroundColor Cyan
Write-Host ""
