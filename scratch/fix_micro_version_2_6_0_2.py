"""
====================================================================
[마이크로 버전 2.6.0.2 정밀 변경 스크립트]
- batch=8 OOM 완전 안전 보정 내역 기록 및 ver 2.6.0.2 업데이트
- PM_정답지_백업 및 WMS_docs 전체 동기화
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업')
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
        
        new_content = (content.replace('ver 2.6.0.1', 'ver 2.6.0.2')
                              .replace('[v2.6.0.1]', '[v2.6.0.2]')
                              .replace('batch=16', 'batch=8'))
        
        with open(p, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"[Fixed Micro Version] {doc_name} -> v2.6.0.2")

# WMS_docs 동기화
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History_Internal.md')
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History.md')
shutil.copy2(pm_dir / 'YOLOv8_Recall_Optimization_Report.md', wms_dir / 'YOLOv8_Recall_Optimization_Report.md')
shutil.copy2(pm_dir / 'YOLOv8_모델_학습결과_분석보고서.md', wms_dir / 'YOLOv8_모델_학습결과_분석보고서.md')
shutil.copy2(pm_dir / 'LangGraph_MultiAgent_Vision_Architecture_Internal.md', wms_dir / 'LangGraph_MultiAgent_Vision_Architecture.md')

print("[SUCCESS] All document version tags updated to ver 2.6.0.2 and synced to WMS_docs!")
