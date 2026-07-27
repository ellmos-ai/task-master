@echo off
setlocal
REM Configure models in %%USERPROFILE%%\.taskplan\taskplan.toml.
REM Optional: set TASKPLAN_WORKDIR and TASKPLAN_TRUSTED_AUTOMATION=1.
python -m taskplan launch --role tasksolver --provider codex
exit /b %ERRORLEVEL%
