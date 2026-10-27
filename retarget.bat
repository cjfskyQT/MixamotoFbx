@echo off
setlocal
call "%~dp0_runtime.bat"
if errorlevel 1 exit /b 1
if "%TARGET_FBX%"=="" (
 echo Set TARGET_FBX to a trusted target FBX before running.
 exit /b 1
)
if not exist "%TARGET_FBX%" (
 echo Target FBX not found.
 exit /b 1
)
if "%MIXAMO_DIR%"=="" (
 echo Set MIXAMO_DIR to the source animation folder before running.
 exit /b 1
)
if not exist "%MIXAMO_DIR%\*.fbx" (
 echo No FBX files found in MIXAMO_DIR.
 exit /b 1
)
if "%OUT_DIR%"=="" set "OUT_DIR=%MIXAMO_DIR%\_retargeted_adv"
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
set "FAIL=0"
for %%F in ("%MIXAMO_DIR%\*.fbx") do (
 if not exist "%OUT_DIR%\%%~nF.fbx" (
  set "CAPOEIRA_BATCH_SOURCE=%%~fF"
  set "CAPOEIRA_BATCH_OUTPUT=%OUT_DIR%\%%~nF.fbx"
  "%PYTHON%" -c "import os,sys; from _bootstrap import import_module; sys.argv=['retarget_tool','--source',os.environ['CAPOEIRA_BATCH_SOURCE'],'--target',os.environ['TARGET_FBX'],'--output',os.environ['CAPOEIRA_BATCH_OUTPUT'],'--mode','adv']; sys.exit(import_module('retarget_tool')._run_cli())"
  if errorlevel 1 set /a FAIL+=1
 )
)
echo Failed files: %FAIL%
exit /b %FAIL%
