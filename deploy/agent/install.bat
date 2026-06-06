@echo off
REM =============================================================
REM   希沃教室端 Agent 安装脚本 (Windows)
REM   需以管理员身份运行
REM =============================================================
setlocal ENABLEDELAYEDEXPANSION

set ROOT=C:\ProgramData\SeewofAgent
set PY=%ROOT%\python\python.exe
set CONFIG=%ROOT%\agent\agent.json

echo.
echo [1/6] 创建目录...
if not exist "%ROOT%" mkdir "%ROOT%"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
if not exist "%ROOT%\data" mkdir "%ROOT%\data"

echo.
echo [2/6] 检查 Python...
if not exist "%PY%" (
    echo !! 未检测到 Python 在 %PY%
    echo !! 请先把 embeddable Python 解压到 %ROOT%\python
    pause
    exit /b 1
)
"%PY%" --version

echo.
echo [3/6] 安装依赖 (可能需要 1-2 分钟)...
"%PY%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
"%PY%" -m pip install -r "%ROOT%\requirements-agent.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if errorlevel 1 (
    echo !! 依赖安装失败
    pause
    exit /b 1
)

echo.
echo [4/6] 检查配置 %CONFIG%...
if not exist "%CONFIG%" (
    echo !! 配置文件不存在, 请先把 agent.example.json 复制为 %CONFIG% 并修改
    pause
    exit /b 1
)
"%PY%" -m agent.main --config "%CONFIG%" --check
if errorlevel 1 (
    echo !! 配置校验失败
    pause
    exit /b 1
)

echo.
echo [5/6] 注册并启动 Windows 服务...
"%PY%" -m agent.service install
sc config SeewofAgent start= auto
sc start SeewofAgent
sc query SeewofAgent
if errorlevel 1 (
    echo !! 服务启动失败, 检查日志: %ROOT%\logs\seewof.log
)

echo.
echo [6/6] 注册 watchdog 计划任务...
schtasks /Delete /TN "SeewofWatchdog" /F >nul 2>&1
schtasks /Create /SC ONSTART /TN "SeewofWatchdog" /RL HIGHEST /F ^
    /TR "\"%PY%\" -m agent.watchdog --config \"%CONFIG%\""
schtasks /Run /TN "SeewofWatchdog"

echo.
echo ============================================
echo   安装完成!
echo   - 服务名: SeewofAgent
echo   - 日志:   %ROOT%\logs\seewof.log
echo   - 配置:   %CONFIG%
echo   - Watchdog: 任务计划 SeewofWatchdog
echo ============================================
pause
