@echo off
cd /d "%~dp0"
title Nexus 프린트 브리지

echo ============================================
echo   Nexus 라벨 프린터 브리지
echo ============================================
echo.

REM --- 설정 파일 확인 (없으면 예시에서 자동 생성) ---
if not exist bridge_config.json (
    if exist bridge_config.example.json (
        copy bridge_config.example.json bridge_config.json > nul
        echo  [!] 설정 파일이 없어 bridge_config.json 을 새로 만들었습니다.
        echo      메모장이 열리면 값을 채우고 저장한 뒤, 이 창을 닫고 다시 실행하세요.
        echo.
        pause
        notepad bridge_config.json
        exit /b 0
    )
    echo  [X] bridge_config.json 이 없습니다. 같은 폴더에 설정 파일을 두세요.
    echo.
    pause
    exit /b 1
)

echo  시연이 끝날 때까지 이 창을 닫지 마세요.
echo  중지하려면 Ctrl+C 를 누르세요.
echo.
echo --------------------------------------------

REM --- 실행파일 우선(파이썬 불필요). 없으면 파이썬으로 폴백 ---
if exist print_bridge.exe (
    print_bridge.exe
    goto :done
)

python --version > nul 2>&1
if errorlevel 1 (
    echo  [X] print_bridge.exe 도 없고 파이썬도 설치되어 있지 않습니다.
    echo.
    echo      해결 방법 둘 중 하나:
    echo      1) 이 폴더에 print_bridge.exe 를 넣는다 (권장, 설치 불필요)
    echo      2) https://www.python.org/downloads/ 에서 파이썬 설치
    echo         (설치 화면 맨 아래 "Add python.exe to PATH" 반드시 체크)
    echo.
    pause
    exit /b 1
)
python print_bridge_agent.py

:done
echo --------------------------------------------
echo.
echo  브리지가 종료되었습니다.
pause
