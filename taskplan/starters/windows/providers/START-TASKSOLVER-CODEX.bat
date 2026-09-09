@echo off
setlocal
REM Optional role overrides live in %%USERPROFILE%%\.taskplan\taskplan.toml.
REM Without them Codex uses its own canonical CLI configuration.
REM Optional: set TASKPLAN_WORKDIR and TASKPLAN_TRUSTED_AUTOMATION=1.
python -m taskplan launch --role tasksolver --provider codex
exit /b %ERRORLEVEL%
