@echo off
title Job Search Agent - leave this window open while you use the app (close it to stop)
cd /d "%~dp0"
where python >nul 2>nul && (python run.py) || (py run.py)
if errorlevel 1 (
  echo.
  echo The app stopped because of the message above.
  pause
)
