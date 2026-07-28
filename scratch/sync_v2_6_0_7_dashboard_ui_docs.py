"""
====================================================================
[Nexus 프로젝트 ver 2.6.0.7 최고 관리자 대시보드 & HITL 검수 UI 고도화 명세 수록 스크립트]
1. PM_정답지_백업 내 4주차 개발 기록 및 마스터 산출물 ver 2.6.0.7 업데이트:
   - 최고 관리자 전용 대시보드 (/admin/dashboard): Recharts 3대 차트 (일별 입출고 추이, 카테고리별 재고 막대, UBCI 품질 도넛) + 4대 미니 도넛 KPI 위젯
   - 사이드바 네비게이션 고도화 (Sidebar.tsx): [승인 대기 32] 뱃지, [🛒 발주 관리], [🛡️ 품질 리포트] 메뉴 수록
   - HITL 수동 검수 서브 패널 (/admin/inspections): 원본 결함 사진, 에이전트 대화 요약, [✓ 승인], [✕ 반려], [🔄 재검토] 3색 버튼
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
        shutil.copy2(p, archive_dir / f"2026-07-27_{p.stem}_pre_v2607.md")

dashboard_ui_dev_section = """
* **최고 관리자 종합 통계 대시보드 (`/admin/dashboard`)**:
  - Recharts 기반 3대 핵심 차트: 일별 입출고 물동량 듀얼 영역 차트(AreaChart), 카테고리별 재고 보유 막대 차트(BarChart), AI UBCI 품질 등급 비율 도넛 차트(PieChart).
  - 상단 4대 실시간 KPI 미니 도넛 위젯: 금일 총 입출고 물동량(12,842건), 실시간 자동 승인율(91.7%), 에이전트 반려율(4.8%), HITL 검수 재확인 건수(278건).
* **사이드바 메타 뱃지 & 확장 네비게이션 (`Sidebar.tsx`)**:
  - `[승인 대기 32]` 실시간 카운트 뱃지, `[🛒 발주 관리 (AI)]`, `[🛡️ 품질 리포트]`, `[👥 사용자 관리]` 메뉴 확장 연동.
* **HITL 수동 검수 칸반 & 서브 슬라이드 패널 (`/admin/inspections`)**:
  - `To Do`, `In Progress`, `Resolved` 칸반 보드 및 티켓 선택 시 우측 슬라이딩 패널에 원본 결함 사진, AI 4대 에이전트 대화 요약, `[✓ 승인(정상)]` / `[✕ 반려(결함)]` / `[🔄 재검토]` 3색 처리 버튼 탑재.
"""

for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        content = content.replace('ver 2.6.0.6', 'ver 2.6.0.7').replace('[v2.6.0.6]', '[v2.6.0.7]')
        if '최고 관리자 종합 통계 대시보드' not in content:
            content += f"\n{dashboard_ui_dev_section}\n"
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)

# WMS_docs 동기화
for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        clean_name = fname.replace('_Confidential', '').replace('_Internal', '')
        shutil.copy2(p, wms_dir / clean_name)

print("[SUCCESS] Master Dashboard & HITL UI specifications updated to ver 2.6.0.7 and synced to WMS_docs!")
