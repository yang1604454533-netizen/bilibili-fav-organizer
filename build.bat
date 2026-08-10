@echo off
REM ============================================
REM  B站收藏夹整理助手 - 打包脚本
REM  用法: 双击运行，或命令行执行 build.bat
REM  产物: dist\B站收藏夹整理助手.exe
REM  注: 需先安装依赖 (pip install -r requirements.txt pyinstaller)
REM ============================================
cd /d %~dp0

set PY=.venv312\Scripts\python.exe
if not exist %PY% (
    echo [错误] 未找到虚拟环境 .venv312
    echo        请先运行: py -3.12 -m venv .venv312   (建议 Python 3.12+，见 README)
    echo        然后:   .venv312\Scripts\pip install -r requirements.txt pyinstaller
    pause
    exit /b 1
)

set ICON=
if exist app\icon.ico set ICON=--icon=app\icon.ico
if exist icon.ico set ICON=--icon=icon.ico

echo [1/2] 开始打包...
%PY% -m PyInstaller --noconfirm --clean ^
    --onefile --windowed --name "BilibiliFavOrganizer" ^
    %ICON% ^
    --collect-data customtkinter ^
    --exclude-module matplotlib ^
    --exclude-module numpy ^
    --exclude-module tkinter.test ^
    main.py
if errorlevel 1 (
    echo [错误] 打包失败，请查看上方日志
    pause
    exit /b 1
)

echo [2/2] 打包完成: dist\BilibiliFavOrganizer.exe
echo       常见问题: 若杀软误报，右键 exe - 属性 - 解除锁定
pause