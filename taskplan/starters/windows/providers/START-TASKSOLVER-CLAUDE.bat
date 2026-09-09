@echo off
setlocal
REM Configure models in %%USERPROFILE%%\.taskplan\taskplan.toml.
REM Optional: TASKPLAN_WORKDIR, TASKPLAN_CLAUDE_MCP_CONFIG, trusted automation.
python -m taskplan launch --role tasksolver --provider claude
exit /b %ERRORLEVEL%
