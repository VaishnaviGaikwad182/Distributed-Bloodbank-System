@echo off
title Distributed Blood Bank System - Launcher
echo ========================================================
echo Starting Distributed Blood Bank Network (5 Nodes)
echo ========================================================

start "Blood Bank A (Port 5001)" cmd /k "python bloodbank/app.py --id A --port 5001 --peers B:5002,C:5003"
start "Blood Bank B (Port 5002)" cmd /k "python bloodbank/app.py --id B --port 5002 --peers A:5001,C:5003"
start "Blood Bank C (Port 5003)" cmd /k "python bloodbank/app.py --id C --port 5003 --peers A:5001,B:5002"

timeout /t 2 /nobreak >nul

start "Hospital Node (Port 5000)" cmd /k "python hospital/app.py --port 5000 --banks A:5001,B:5002,C:5003"
start "Admin Node (Port 5010)" cmd /k "python admin/app.py --port 5010 --hospital 5000 --banks A:5001,B:5002,C:5003"

echo.
echo All 5 nodes have been launched in separate windows:
echo   1. Hospital Node : http://localhost:5000
echo   2. Blood Bank A  : http://localhost:5001
echo   3. Blood Bank B  : http://localhost:5002
echo   4. Blood Bank C  : http://localhost:5003
echo   5. Admin Node    : http://localhost:5010
echo.
pause
