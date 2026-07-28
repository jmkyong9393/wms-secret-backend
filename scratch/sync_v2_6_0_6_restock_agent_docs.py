"""
====================================================================
[Nexus 프로젝트 ver 2.6.0.6 AI Restock Agent 자동 발주 파이프라인 수록 스크립트]
1. PM_정답지_백업 내 4주차 개발 기록 및 마스터 산출물 ver 2.6.0.6 업데이트:
   - LLM Restock Agent (app/ai/agents/restock.py) 기반 AI 자동 발주 추천 파이프라인
   - 입고 반려(DMG_EXT_WET 등) 발생 시 판매량 및 재고 연동 대체 발주 수량 및 AI 사유 자동 생성 (POST /api/v1/orders/auto-po)
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
        shutil.copy2(p, archive_dir / f"2026-07-27_{p.stem}_pre_v2606.md")

restock_dev_section = """
* **LLM Restock Agent 기반 AI 자동 발주(Auto-PO) 파이프라인 (`app/ai/agents/restock.py`)**:
  - 입고 검수 시 파손(DMG_EXT_WET 등)으로 반려 처리 발생 시, 재고 편입을 차단하고 해당 도서의 30일간 판매 출고량 및 창고 가용 재고를 자동 집계.
  - LLM Restock Agent가 최적 대체 발주 수량(Reorder Quantity)과 긴급도(`CRITICAL`/`HIGH`) 및 발주 추천 사유 코멘트를 JSON으로 생성하여 `POST /api/v1/orders/auto-po` 엔드포인트를 통해 `order_proposals` DB 테이블로 자동 적재.
"""

for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        content = content.replace('ver 2.6.0.5', 'ver 2.6.0.6').replace('[v2.6.0.5]', '[v2.6.0.6]')
        if 'LLM Restock Agent 기반 AI 자동 발주' not in content:
            content += f"\n{restock_dev_section}\n"
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)

# WMS_docs 동기화
for fname in doc_files:
    p = pm_dir / fname
    if p.exists():
        clean_name = fname.replace('_Confidential', '').replace('_Internal', '')
        shutil.copy2(p, wms_dir / clean_name)

print("[SUCCESS] Restock Agent Auto-PO pipeline specifications updated to ver 2.6.0.6 and synced to WMS_docs!")
