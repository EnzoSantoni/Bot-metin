@echo off
cd /d "%~dp0"
set "PYW=.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=..\metin-bot\.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
    echo No se encontro el entorno virtual: ni .venv ni ..\metin-bot\.venv
    echo Crealo con: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
start "" "%PYW%" gui.py
