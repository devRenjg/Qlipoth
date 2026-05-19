Write-Host "=== Qlipoth 启动脚本 ===" -ForegroundColor Cyan

# Install backend dependencies
Write-Host "`n[1/4] Installing backend dependencies..." -ForegroundColor Yellow
Set-Location "$PSScriptRoot\backend"
pip install -r requirements.txt -q

# Install frontend dependencies
Write-Host "[2/4] Installing frontend dependencies..." -ForegroundColor Yellow
Set-Location "$PSScriptRoot\frontend"
if (-not (Test-Path "node_modules")) {
    npm install
} else {
    Write-Host "  node_modules exists, skipping."
}

# Start backend
Write-Host "[3/4] Starting backend (port 8000)..." -ForegroundColor Yellow
Set-Location "$PSScriptRoot\backend"
$backend = Start-Process -FilePath "python" -ArgumentList "main.py" -PassThru -NoNewWindow

# Start frontend
Write-Host "[4/4] Starting frontend (port 3000)..." -ForegroundColor Yellow
Set-Location "$PSScriptRoot\frontend"
$frontend = Start-Process -FilePath "cmd" -ArgumentList "/c", "npm run dev" -PassThru -NoNewWindow

Write-Host "`n=== Services started ===" -ForegroundColor Green
Write-Host "  Frontend: http://localhost:3000" -ForegroundColor Cyan
Write-Host "  Backend:  http://localhost:8000" -ForegroundColor Cyan
Write-Host "`nPress Ctrl+C to stop all services.`n" -ForegroundColor Gray

try {
    Wait-Process -Id $backend.Id
} finally {
    if (!$backend.HasExited) { Stop-Process -Id $backend.Id -Force }
    if (!$frontend.HasExited) { Stop-Process -Id $frontend.Id -Force }
    Write-Host "Services stopped." -ForegroundColor Yellow
}
