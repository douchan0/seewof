@echo off
REM 卸载脚本 (管理员)
setlocal
schtasks /Delete /TN "SeewofWatchdog" /F
sc stop SeewofAgent
timeout /t 3 /nobreak >nul
sc delete SeewofAgent
echo.
echo 已卸载服务. 如需彻底清除, 删除 C:\ProgramData\SeewofAgent 即可.
echo (建议先保留 logs/ 和 data/ 一段时间, 以便排查问题)
pause
