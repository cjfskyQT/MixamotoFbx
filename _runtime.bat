@echo off
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PYTHONDONTWRITEBYTECODE=1"
if not "%PYTHON%"=="" exit /b 0
if not "%MAYAPY%"=="" set "PYTHON=%MAYAPY%"
if not "%PYTHON%"=="" exit /b 0
for /d %%D in ("%ProgramFiles%\Autodesk\Maya*") do if exist "%%~fD\bin\mayapy.exe" set "PYTHON=%%~fD\bin\mayapy.exe"
if "%PYTHON%"=="" (
 echo Maya was not found. Install Maya or set MAYAPY to mayapy.exe.
 exit /b 1
)
exit /b 0
