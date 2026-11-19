@echo off
rem Igual que abrir_interfaz.bat pero como administrador (para cuando el juego corre como administrador).
cd /d "%~dp0"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0..\metin-bot\.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
    echo No se encontro el entorno virtual: ni .venv ni ..\metin-bot\.venv
    pause
    exit /b 1
)
rem Ruta completa a gui.py: al elevar, Windows suele ignorar -WorkingDirectory y arranca en System32.
powershell -NoProfile -Command "Start-Process -FilePath '%PYW%' -ArgumentList '\"%~dp0gui.py\"' -WorkingDirectory '%~dp0' -Verb RunAs"
