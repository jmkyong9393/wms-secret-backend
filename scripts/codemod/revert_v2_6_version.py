"""
====================================================================
[버전 버전 표기 보정 스크립트]
- ver 2.7.0.0 표기를 v2.6.0.0 (또는 v2.6.1.0 세부 수정)으로 통합 보정
- 개인개발가이드 및 WMS_docs 전체 동기화
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\개인개발가이드')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')

doc_files = [
    'YOLO_Model_History_Internal.md',
    'YOLOv8_Recall_Optimization_Report.md',
    'YOLOv8_모델_학습결과_분석보고서.md',
    'LangGraph_MultiAgent_Vision_Architecture_Internal.md'
]

for doc_name in doc_files:
    p = pm_dir / doc_name
    if p.exists():
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # v2.7.0.0 표기를 v2.6.1.0 (또는 v2.6.0.0 세부 보정)으로 변경
        new_content = content.replace('[v2.7.0.0]', '[v2.6.1.0]').replace('ver 2.7.0.0', 'ver 2.6.1.0')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"[Adjusted Version] {doc_name} -> v2.6.1.0")

# WMS_docs 동기화
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History_Internal.md')
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History.md')
shutil.copy2(pm_dir / 'YOLOv8_Recall_Optimization_Report.md', wms_dir / 'YOLOv8_Recall_Optimization_Report.md')
shutil.copy2(pm_dir / 'YOLOv8_모델_학습결과_분석보고서.md', wms_dir / 'YOLOv8_모델_학습결과_분석보고서.md')
shutil.copy2(pm_dir / 'LangGraph_MultiAgent_Vision_Architecture_Internal.md', wms_dir / 'LangGraph_MultiAgent_Vision_Architecture.md')

print("[SUCCESS] Version headers reverted to v2.6.1.0 and synced to WMS_docs!")
