@echo off
echo ===========================================
echo   Insta-Quote Feature Pipeline Server
echo ===========================================
echo.
echo This script starts the Flask backend server
echo with the PythonOCC conda environment.
echo.
echo Server will run on: http://localhost:5000
echo.

:: Activate conda environment and start server
call conda activate occ
python "%~dp0quote_pipeline_server.py"

:: If server exits, keep window open
echo.
echo Server stopped. Press any key to exit...
pause > nul
