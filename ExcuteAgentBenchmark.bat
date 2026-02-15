@echo off
REM Script to configure and run the benchmark_agent (Windows version)

REM Change to script directory
cd /d "%~dp0"

REM Check if virtual environment exists
if not exist ".venv" (
    echo Creating new virtual environment...
    python -m venv .venv
    .venv/Scripts/activate.bat
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
) else (
    echo Activating existing virtual environment...
    .venv/Scripts/activate.bat
)

echo Running benchmark_agent...
python -m agent.benchmark_agent
