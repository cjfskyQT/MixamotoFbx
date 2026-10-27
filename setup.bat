@echo off
setlocal
call "%~dp0_runtime.bat"
if errorlevel 1 goto runtime_fail
"%PYTHON%" -c "from _bootstrap import import_module; runtime=import_module('maya_runtime'); qt=import_module('qt_compat'); print('Ready:',runtime.find_mayapy())"
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="0" echo Open retarget_gui.bat to start. No separate FBX SDK is required.
goto finish
:runtime_fail
set "RESULT=1"
:finish
if /i not "%~1"=="--check" pause
exit /b %RESULT%
