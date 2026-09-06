@echo off
setlocal
REM Provider-neutral: asks for provider, model and reasoning at start.
REM Configure defaults in %%USERPROFILE%%\.taskplan\taskplan.toml.
REM Optional: TASKPLAN_WORKDIR, TASKPLAN_TRUSTED_AUTOMATION, TASKPLAN_STARTER_PROBE=0.
REM One provider per file lives in the providers subfolder.
python -m taskplan launch --role tasksolver --interactive %*
exit /b %ERRORLEVEL%
