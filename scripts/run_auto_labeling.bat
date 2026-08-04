@echo off
setlocal enabledelayedexpansion
:: [중요] 배치파일이 어느 위치에서 실행되더라도 백엔드 루트 폴더로 자동 이동
cd /d "%~dp0.."

cls
echo ===================================================
echo   WMS YOLOv8 Auto Labeling Tool (Pre-Annotation)
echo ===================================================
echo.

set "TARGET_DIR=%~1"

if "%TARGET_DIR%"=="" (
    echo [Instruction] Drag and drop a photo folder onto this batch file,
    echo               or enter the target folder path below.
    echo.
    set /p "TARGET_DIR=Enter Image Folder Path: "
)

if "%TARGET_DIR%"=="" (
    echo.
    echo [Error] No folder path provided. Exiting...
    pause
    exit /b 1
)

echo.
echo Processing Auto-Labeling for: "%TARGET_DIR%"
echo.

.venv\Scripts\python.exe scratch/auto_labeling_with_yolo.py "%TARGET_DIR%"

echo.
echo ===================================================
echo   Auto Labeling Completed!
echo   Open labelImg and load "%TARGET_DIR%"
echo ===================================================
echo.
pause
