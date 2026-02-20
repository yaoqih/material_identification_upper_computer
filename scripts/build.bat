@echo off
set NAME=MaterialUpper

rem Remove stale spec to avoid appending old datas
if exist "%NAME%.spec" del /f /q "%NAME%.spec"

rem Optional resources inclusion (skip if folder not present)
set ADD_RES=
if exist resources (
  set ADD_RES=--add-data "resources;resources"
)

uv run pyinstaller --noconfirm --clean --name %NAME% --onedir --console --paths . %ADD_RES% app/main.py

rem Keep configs as external files (not bundled into executable archive)
if not exist configs\default.json (
  echo ERROR: configs\default.json not found.
  exit /b 1
)
if exist "dist\%NAME%\configs" rmdir /s /q "dist\%NAME%\configs"
xcopy /E /I /Y "configs" "dist\%NAME%\configs" >nul

echo Build done.
