@echo off
cd /d "%~dp0"
title Nexus 프린트 브리지 - 진단

echo  현재 네트워크 상태와 프린터 위치를 진단합니다.
echo  (대역 스캔에 30초 정도 걸릴 수 있습니다)
echo.

REM --- 실행파일 우선(파이썬 불필요). 없으면 파이썬으로 폴백 ---
if exist print_bridge.exe (
    print_bridge.exe --doctor
    goto :done
)

python --version > nul 2>&1
if errorlevel 1 (
    echo  [X] print_bridge.exe 도 없고 파이썬도 설치되어 있지 않습니다.
    echo      이 폴더에 print_bridge.exe 를 넣거나, 파이썬을 설치하세요.
    echo.
    pause
    exit /b 1
)
python print_bridge_agent.py --doctor

:done
echo.
pause
