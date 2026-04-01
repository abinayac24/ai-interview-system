$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$speechSetupScript = Join-Path $projectRoot "setup_speech_service.ps1"
$mainVenvPython = Join-Path $projectRoot "venv\Scripts\python.exe"
$backendBaseUrl = "http://127.0.0.1:8000"
$frontendBaseUrl = "http://127.0.0.1:5000"

function Write-Step($message) {
    Write-Host "`n==> $message" -ForegroundColor Cyan
}

function Write-Success($message) {
    Write-Host "[OK] $message" -ForegroundColor Green
}

function Write-Warn($message) {
    Write-Host "[WARN] $message" -ForegroundColor Yellow
}

function Test-Http($url) {
    try {
        Invoke-WebRequest -UseBasicParsing $url | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Start-Backend {
    if (-not (Test-Path $mainVenvPython)) {
        throw "Main project venv not found at $mainVenvPython"
    }

    if (Test-Http "$backendBaseUrl/docs") {
        Write-Success "Backend already running"
        return
    }

    Start-Process -FilePath $mainVenvPython -ArgumentList @(
        "-m",
        "uvicorn",
        "app.main:app",
        "--port",
        "8000"
    ) -WorkingDirectory $projectRoot | Out-Null

    for ($i = 0; $i -lt 20; $i += 1) {
        Start-Sleep -Seconds 2
        if (Test-Http "$backendBaseUrl/docs") {
            Write-Success "Backend running"
            return
        }
    }

    throw "Backend did not start on $backendBaseUrl"
}

function Start-Frontend {
    if (-not (Test-Path $mainVenvPython)) {
        throw "Main project venv not found at $mainVenvPython"
    }

    if (Test-Http "$frontendBaseUrl/") {
        Write-Success "Frontend already running"
        return
    }

    Start-Process -FilePath $mainVenvPython -ArgumentList @("app.py") -WorkingDirectory $projectRoot | Out-Null

    for ($i = 0; $i -lt 20; $i += 1) {
        Start-Sleep -Seconds 2
        if (Test-Http "$frontendBaseUrl/") {
            Write-Success "Frontend running"
            return
        }
    }

    throw "Frontend did not start on $frontendBaseUrl"
}

function Show-Routes {
    $routeDump = & $mainVenvPython -c "from app.main import app; [print('{0:12} {1}'.format(','.join(sorted(getattr(route, 'methods', []) or [])), getattr(route, 'path', ''))) for route in app.routes]"
    Write-Host $routeDump
}

function Find-InterviewStartEndpoint {
    $candidates = @(
        "$backendBaseUrl/api/interviews/domain/start",
        "$backendBaseUrl/interviews/domain/start"
    )
    $body = '{"candidate_name":"TestUser","domain":"Python"}'

    foreach ($candidate in $candidates) {
        try {
            $response = Invoke-RestMethod -Uri $candidate -Method Post -ContentType "application/json" -Body $body
            return [PSCustomObject]@{
                Url = $candidate
                Response = $response
            }
        } catch {
        }
    }

    throw "Could not find a working interview start endpoint."
}

try {
    Write-Step "Start and verify speech service"
    & $speechSetupScript

    Write-Step "Start and verify backend"
    Start-Backend

    Write-Step "List registered FastAPI routes"
    Show-Routes

    Write-Step "Detect working interview-start endpoint"
    $endpointCheck = Find-InterviewStartEndpoint
    Write-Success "API endpoint verified: $($endpointCheck.Url)"

    $sessionId = $endpointCheck.Response.session_id
    if (-not $sessionId) {
        throw "Interview start endpoint did not return a session_id."
    }
    Write-Success "session_id generated: $sessionId"

    Write-Step "Start and verify Flask frontend"
    Start-Frontend

    $interviewUrl = "$frontendBaseUrl/interview?session_id=$sessionId"
    try {
        Start-Process $interviewUrl | Out-Null
        Write-Success "Browser opened"
    } catch {
        Write-Warn "Could not open the browser automatically. Open this URL manually: $interviewUrl"
    }

    Write-Host ""
    Write-Host "[OK] Backend running" -ForegroundColor Green
    Write-Host "[OK] API endpoint verified" -ForegroundColor Green
    Write-Host "[OK] session_id generated" -ForegroundColor Green
    Write-Host "[OK] Speech service running (port 9000)" -ForegroundColor Green
    Write-Host "[OK] Frontend running (port 5000)" -ForegroundColor Green
    Write-Host "[OK] System ready for interview" -ForegroundColor Green
    Write-Host ""
    Write-Host "Interview URL: $interviewUrl" -ForegroundColor Cyan
} catch {
    Write-Host ""
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    throw
}
