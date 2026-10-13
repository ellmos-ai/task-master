@echo off
chcp 65001 >nul
setlocal
set "PYTHONIOENCODING=utf-8"
REM Required: PINGPONG_SYNC_ROOT and PINGPONG_SLOT, or --sync-root and --slot.
python -m taskplan startcenter --role pingpong %*
exit /b %ERRORLEVEL%
