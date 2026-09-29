@echo off
REM Build dist\VeilTunnel-Server.exe and dist\VeilTunnel-Client.exe
REM Prerequisite:  py -m pip install -r requirements-build.txt
REM (A clean virtualenv gives the smallest, most reproducible build.)
setlocal
cd /d "%~dp0"
set "ICON=%~dp0assets\veiltunnel.ico"
set "COMMON=--noconfirm --clean --onefile --windowed --icon "%ICON%" --add-data "%ICON%;assets" --specpath build --workpath build"
REM Optional imports that PyInstaller would otherwise drag in from a busy environment.
set "EXCLUDE=--exclude-module IPython --exclude-module jedi --exclude-module parso --exclude-module matplotlib --exclude-module numpy --exclude-module PIL --exclude-module pygments --exclude-module rich --exclude-module markdown_it --exclude-module yaml --exclude-module jinja2 --exclude-module setuptools --exclude-module pkg_resources --exclude-module watchfiles --exclude-module httptools --exclude-module dotenv"

py -m PyInstaller %COMMON% %EXCLUDE% --name VeilTunnel-Server --collect-submodules uvicorn veiltunnel_server.py || goto :fail
py -m PyInstaller %COMMON% %EXCLUDE% --name VeilTunnel-Client veiltunnel_client.py || goto :fail

echo.
echo Done: dist\VeilTunnel-Server.exe  dist\VeilTunnel-Client.exe
exit /b 0

:fail
echo Build failed.
exit /b 1
