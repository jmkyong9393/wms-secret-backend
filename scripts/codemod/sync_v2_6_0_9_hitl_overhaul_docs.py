"""
====================================================================
[Nexus 프로젝트 ver 2.6.0.9 HITL 관제 센터 디자인 전면 리팩토링 수록]
1. 개인개발가이드 내 4주차 개발 기록 및 마스터 산출물 ver 2.6.0.9 업데이트:
   - HITL 수동 검수 관제 센터 전면 디자인 개편 (/admin/inspections & /admin/hitl)
   - Bounding Box 결함 스캔 오버레이, 4대 AI 에이전트 교차 검증 투표 매트릭스
2. WMS_docs 이원화 배포 자동 복사 동기화
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\개인개발가이드')
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
        shutil.copy2(p, archive_dir / f"2026-07-27_{p.stem}_pre_v2609.md")

hitl_overhaul_dev_section = """
* **HITL 수동 검수 관제 센터 전면 디자인 리팩토링 (`/admin/inspections`, `/admin/hitl`)**:
  - 엔터프라이즈 WMS 관제 센터 디자인 스펙 적용 (Sleek Slate-950 다크 테마).
  - 도서 표지 원본 위 결함 위치 동적 박싱: **실시간 Bounding Box (BBox) 오버레이 및 Relative Ratio 면적 비율 태그 표시**.
  - **4대 AI 에이전트 교차 검증 투표 매트릭스**: Vision YOLOv8, Policy Agent, Critic Agent, Restock Agent의 4인 4색 투표 결과, 신뢰도 스코어(%), 코멘트 그리드 관제 출력.
"""

for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        content = content.replace('ver 2.6.0.8', 'ver 2.6.0.9').replace('[v2.6.0.8]', '[v2.6.0.9]')
        if 'HITL 수동 검수 관제 센터 전면 디자인' not in content:
            content += f"\n{hitl_overhaul_dev_section}\n"
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)

# WMS_docs 동기화
for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        clean_name = fname.replace('_Confidential', '').replace('_Internal', '')
        shutil.copy2(p, wms_dir / clean_name)

print("[SUCCESS] HITL Command Center design overhaul specifications updated to ver 2.6.0.9 and synced to WMS_docs!")
