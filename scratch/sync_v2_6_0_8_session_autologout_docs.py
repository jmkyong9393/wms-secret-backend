"""
====================================================================
[Nexus 프로젝트 ver 2.6.0.8 세션 자동 로그아웃 명세 수록 스크립트]
1. PM_정답지_백업 내 4주차 개발 기록 및 마스터 산출물 ver 2.6.0.8 업데이트:
   - 탭/브라우저 종료 및 웹사이트 이탈 시 자동 로그아웃 파이프라인 (SessionAutoLogout.tsx)
   - sessionStorage 기반 Jotai userAtom 및 beforeunload/pagehide 이벤트 수신 쿠키/토큰 파기 로직
2. WMS_docs 이원화 배포 자동 복사 동기화
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')
archive_dir = pm_dir / 'archive'

doc_files = [
    '4주차_개인_개발_기록_대외비.md',
    '01_엔지니어링_산출물.md',
    'B2B_WMS_AI_Platform_기획서_ver2.0.0.0.md',
    'B2B_WMS_AI_Platform_총개발문서_Master.md'
]

# 백업
for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        shutil.copy2(p, archive_dir / f"2026-07-27_{p.stem}_pre_v2608.md")

autologout_dev_section = """
* **웹사이트 이탈 & 탭 종료 시 세션 자동 로그아웃 보안 파이프라인 (`src/components/auth/SessionAutoLogout.tsx`)**:
  - `localStorage` 기반 전역 상태의 무한 로그인 세션 유지 문제를 해결하기 위해 Jotai `userAtom`을 `sessionStorage` 기반으로 전환.
  - 브라우저 탭/창 종료 및 사이트 외부 이탈 시 `beforeunload` / `pagehide` 이벤트를 수신하여 JWT AccessToken/RefreshToken 쿠키 및 사용자 상태를 즉시 파기(Purge)하는 자동 로그아웃 보안 강화.
"""

for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        content = content.replace('ver 2.6.0.7', 'ver 2.6.0.8').replace('[v2.6.0.7]', '[v2.6.0.8]')
        if '세션 자동 로그아웃 보안 파이프라인' not in content:
            content += f"\n{autologout_dev_section}\n"
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)

# WMS_docs 동기화
for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        clean_name = fname.replace('_Confidential', '').replace('_Internal', '')
        shutil.copy2(p, wms_dir / clean_name)

print("[SUCCESS] Session Auto-Logout security specifications updated to ver 2.6.0.8 and synced to WMS_docs!")
