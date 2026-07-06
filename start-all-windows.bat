@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ====================================
echo Start MCP, backend, and frontend
echo ====================================
echo.

set PYTHON_CMD=.venv\Scripts\python.exe

echo [1/6] Checking Python virtual environment...
if not exist "%PYTHON_CMD%" (
    echo [INFO] .venv not found. Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv. Please install Python 3.11+ first.
        pause
        exit /b 1
    )

    echo [INFO] Installing backend dependencies...
    "%PYTHON_CMD%" -m pip install --upgrade pip
    "%PYTHON_CMD%" -m pip install -e .
    if errorlevel 1 (
        echo [ERROR] Failed to install backend dependencies.
        pause
        exit /b 1
    )
) else (
    echo [OK] Python virtual environment exists.
)
echo.

echo [2/6] Checking frontend dependencies...
if not exist "frontend\node_modules" (
    echo [INFO] frontend\node_modules not found. Running npm install...
    pushd frontend
    npm install
    if errorlevel 1 (
        popd
        echo [ERROR] npm install failed.
        pause
        exit /b 1
    )
    popd
) else (
    echo [OK] Frontend dependencies exist.
)
echo.

echo [3/6] Starting CLS MCP server...
netstat -ano | findstr ":8003" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Port 8003 is already in use. Skipping CLS MCP startup.
) else (
    start "CLS MCP Server" /min cmd /k "%PYTHON_CMD% mcp_servers\cls_server.py"
    timeout /t 2 /nobreak >nul
    echo [OK] CLS MCP startup command sent: http://127.0.0.1:8003/mcp
)
echo.

echo [4/6] Starting Monitor MCP server...
netstat -ano | findstr ":8004" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Port 8004 is already in use. Skipping Monitor MCP startup.
) else (
    start "Monitor MCP Server" /min cmd /k "%PYTHON_CMD% mcp_servers\monitor_server.py"
    timeout /t 2 /nobreak >nul
    echo [OK] Monitor MCP startup command sent: http://127.0.0.1:8004/mcp
)
echo.

echo [5/6] Starting FastAPI backend...
curl.exe -s -f http://localhost:9900/health >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Backend is already running: http://localhost:9900
) else (
    start "FastAPI Backend" /min cmd /k "%PYTHON_CMD% -m uvicorn app.main:app --host 0.0.0.0 --port 9900 --reload"
    echo [INFO] Waiting for backend health check...
    set BACKEND_READY=0
    for /L %%i in (1,1,30) do (
        if "!BACKEND_READY!"=="0" (
            curl.exe -s -f http://localhost:9900/health >nul 2>&1
            if not errorlevel 1 set BACKEND_READY=1
            if "!BACKEND_READY!"=="0" timeout /t 1 /nobreak >nul
        )
    )

    if "!BACKEND_READY!"=="1" (
        echo [OK] Backend is ready: http://localhost:9900
    ) else (
        echo [WARN] Backend did not pass health check yet. Check the FastAPI Backend window.
    )
)
echo.

echo [6/6] Starting Vite frontend...
netstat -ano | findstr ":5173" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Port 5173 is already in use. Skipping frontend startup.
) else (
    start "Frontend Vite" /min cmd /k "cd /d frontend && npm run dev"
    timeout /t 2 /nobreak >nul
    echo [OK] Frontend startup command sent: http://localhost:5173
)
echo.

echo ====================================
echo Startup commands finished
echo ====================================
echo Frontend:      http://localhost:5173
echo Backend API:   http://localhost:9900
echo API docs:      http://localhost:9900/docs
echo CLS MCP:       http://127.0.0.1:8003/mcp
echo Monitor MCP:   http://127.0.0.1:8004/mcp
echo.
echo Stop services by closing these windows:
echo   CLS MCP Server, Monitor MCP Server, FastAPI Backend, Frontend Vite
echo ====================================
pause
