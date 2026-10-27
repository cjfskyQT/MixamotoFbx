@echo off
setlocal
call "%~dp0_runtime.bat"
if errorlevel 1 goto fail
"%PYTHON%" -c "from _bootstrap import import_module; import sys; sys.exit(import_module('retarget_tool').run_gui())"
if errorlevel 1 goto fail
exit /b 0
:fail
pause
exit /b 1
