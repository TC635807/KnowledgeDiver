@echo off
chcp 65001 >nul 2>&1
set PYTHONUTF8=1
title KnowledgeDiver

echo ========================================
echo   KnowledgeDiver - Starting Services
echo ========================================
echo/

set D=%~dp0

if not exist "%D%.env" echo 提示: 未找到 .env，建议先执行 copy .env.example .env 并设置 JWT_SECRET

if not exist "%D%.venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv "%D%.venv"
)
call "%D%.venv\Scripts\activate.bat"

echo Installing backend dependencies...
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r "%D%requirements.txt" --no-deps 2>nul
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r "%D%requirements.txt"

echo Installing Playwright browsers...
set PLAYWRIGHT_BROWSERS_PATH=%D%.playwright
python -m playwright install chromium

echo [1/2] Starting Backend...
rem kill leftover instances from previous runs (port 8000 conflicts)
taskkill /FI "WINDOWTITLE eq KD-Backend*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq KD-Frontend*" /F >nul 2>&1
timeout /t 2 /nobreak >nul
set PYTHONPATH=%D%
start "KD-Backend" cmd /c "cd /d %D% && call .venv\Scripts\activate.bat && set PYTHONPATH=%D% && uvicorn backend.main:app --port 8000"
timeout /t 3 /nobreak >nul

echo [2/2] Starting Frontend...
cd /d "%D%frontend"
call npm install --registry=https://registry.npmmirror.com
if not exist "%D%frontend\node_modules\vite" (
    echo ERROR: Frontend dependencies not installed. Please check npm install output.
    pause
    exit /b 1
)
cd /d "%D%"
start "KD-Frontend" cmd /c "cd /d %D%frontend && npm run dev || (echo Frontend startup failed. Check errors above. && pause)"

echo/
echo ========================================
echo   Services started!
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:3000
echo ========================================
echo/
pause
