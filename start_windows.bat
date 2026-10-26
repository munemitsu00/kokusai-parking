@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   PARK OKINAWA - ローカル起動
 echo ========================================
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  set "PYTHON=py"
) else (
  set "PYTHON=python"
)
if not exist ".venv\Scripts\python.exe" (
  %PYTHON% -m venv .venv
  if errorlevel 1 goto :error
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error
".venv\Scripts\python.exe" manage.py init
if errorlevel 1 goto :error
".venv\Scripts\python.exe" manage.py seed
if errorlevel 1 goto :error
echo.
echo ブラウザから http://127.0.0.1:8000 を開いてください。
echo 停止するには、この画面で Ctrl+C を押します。
echo 管理者の初回登録は create_admin_windows.bat から。
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0
:error
echo 起動できませんでした。Python 3.11 以上がインストールされているか確認してください。
pause
exit /b 1
