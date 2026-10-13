@echo off
chcp 65001 >nul
setlocal
set "PYTHONIOENCODING=utf-8"
python -m taskplan startcenter %*
exit /b %ERRORLEVEL%
