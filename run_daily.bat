@echo off
REM Windows 사내 PC/서버용. 작업 스케줄러에 등록:
REM   schtasks /create /tn "BrazilPromoMonitor" /tr "C:\brazil-promo-monitor\run_daily.bat" /sc daily /st 18:00
REM   (한국 18:00 = 브라질 06:00)
cd /d %~dp0
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python run_daily.py %* >> run.log 2>&1
