@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
 echo Instale Python 3.11 ou superior em https://www.python.org/downloads/windows/
 pause
 exit /b 1
)
py -m pip install -r requirements.txt
if errorlevel 1 (echo Erro instalando dependencias & pause & exit /b 1)
py -m PyInstaller --noconfirm --clean --onefile --windowed --name "Novel para EPUB" app.py
if errorlevel 1 (echo Erro compilando & pause & exit /b 1)
echo.
echo PRONTO! O programa esta na pasta dist\Novel para EPUB.exe
pause
