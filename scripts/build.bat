@echo off
set NAME=MaterialUpper

rem Remove stale spec to avoid appending old datas
if exist "%NAME%.spec" del /f /q "%NAME%.spec"

rem Optional resources inclusion (skip if folder not present)
set ADD_RES=
if exist resources (
  set ADD_RES=--add-data "resources;resources"
)

rem Always include configs
set ADD_CFG=--add-data "configs;configs"

uv run pyinstaller --noconfirm --clean --name %NAME% --onedir --console --paths . %ADD_CFG% %ADD_RES% app/main.py
echo Build done.