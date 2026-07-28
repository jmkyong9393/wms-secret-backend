"""
====================================================================
[프로젝트명 Nexus 공식적용 및 문서 업데이트 스크립트]
- 프로젝트명을 "Nexus" (Project Nexus / Nexus WMS)로 전면 적용
- PM_정답지_백업 및 WMS_docs 전체 문서 갱신
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')
archive_dir = pm_dir / 'archive'

doc_files = [
    'YOLO_Model_History_Internal.md',
    'YOLOv8_Recall_Optimization_Report.md',
    'YOLOv8_모델_학습결과_분석보고서.md',
    'LangGraph_MultiAgent_Vision_Architecture_Internal.md'
]

# 원본 백업
for doc_name in doc_files:
    src_p = pm_dir / doc_name
    if src_p.exists():
        stem = src_p.stem.replace('_Internal', '')
        bak_name = f"2026-07-27_{stem}_pre_nexus.md"
        shutil.copy2(src_p, archive_dir / bak_name)
        print(f"[Backup] {doc_name} -> archive/{bak_name}")

# 프로젝트명 Nexus 전면 교체 및 서문 갱신
nexus_header_badge = """
> **프로젝트명**: Nexus (Nexus AI Smart WMS Platform)
> **소유자/총괄 설계자**: 장문경 (Lead Architect & Project Owner)
"""

for doc_name in doc_files:
    p = pm_dir / doc_name
    if p.exists():
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # 기존 일반 'WMS' 단어를 'Nexus WMS' 또는 'Nexus' 프로젝트로 명시적 변경
        new_content = content.replace('스마트 WMS', 'Nexus 스마트 WMS').replace('WMS 비전', 'Nexus WMS 비전')
        
        if 'Nexus (Nexus AI Smart WMS Platform)' not in new_content:
            lines = new_content.split('\n')
            # 1행 제목 바로 밑에 Nexus 프로젝트 정보 삽입
            lines.insert(1, nexus_header_badge)
            new_content = '\n'.join(lines)
            
        with open(p, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"[Applied Project Name 'Nexus'] {doc_name}")

# WMS_docs 동기화
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History_Internal.md')
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History.md')
shutil.copy2(pm_dir / 'YOLOv8_Recall_Optimization_Report.md', wms_dir / 'YOLOv8_Recall_Optimization_Report.md')
shutil.copy2(pm_dir / 'YOLOv8_모델_학습결과_분석보고서.md', wms_dir / 'YOLOv8_모델_학습결과_분석보고서.md')
shutil.copy2(pm_dir / 'LangGraph_MultiAgent_Vision_Architecture_Internal.md', wms_dir / 'LangGraph_MultiAgent_Vision_Architecture.md')

print("[SUCCESS] Project Name 'Nexus' officially applied across all master docs and synced to WMS_docs!")
