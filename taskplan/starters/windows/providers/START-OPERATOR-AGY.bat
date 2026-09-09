@echo off
setlocal
REM Configure models in %%USERPROFILE%%\.taskplan\taskplan.toml.
REM Optional: TASKPLAN_OPERATOR_MODE=rotation^|subagents (default rotation),
REM TASKPLAN_WORKDIR, TASKPLAN_CLAUDE_MCP_CONFIG, trusted automation.
python -m taskplan launch --role operator --provider agy
exit /b %ERRORLEVEL%
