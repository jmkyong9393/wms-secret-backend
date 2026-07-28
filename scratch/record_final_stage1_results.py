"""
====================================================================
[Stage 1 훈련 최종 완료 실측 결과(Epoch 170 Early Stopping) 문서 누적 기록 스크립트]
- 170 에포크 조기 종료(Early Stopping) 실측 수치 반영:
  * mAP50: 16.8% (Epoch 140 Best)
  * Recall: 27.1%
  * Precision: 21.6%
- 조장님의 예리한 직관(수직 상승 불가 예측) 및 WBF 앙상블 전환 결정 팩트 기록
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')
arc_dir = pm_dir / 'archive'

targets = [
    ('YOLO_Model_History_Internal.md', 'YOLO_Model_History_Internal.md'),
    ('YOLOv8_Recall_Optimization_Report.md', 'YOLOv8_Recall_Optimization_Report.md'),
    ('YOLOv8_모델_학습결과_분석보고서.md', 'YOLOv8_모델_학습결과_분석보고서.md'),
    ('LangGraph_MultiAgent_Vision_Architecture_Internal.md', 'LangGraph_MultiAgent_Vision_Architecture.md')
]

def record_results():
    print(f"[Final Result Record] Updating all docs with Epoch 170 Early Stopping metrics...")

    # 1. YOLO_Model_History_Internal.md 누적 업데이트
    history_p = pm_dir / 'YOLO_Model_History_Internal.md'
    with open(history_p, 'r', encoding='utf-8') as f:
        history_text = f.read()

    v251_history_section = """

### B. Stage 1 YOLOv8m 훈련 최종 결과 (Epoch 170 Early Stopping 실측)
* **훈련 완료 시각**: 2026-07-27 (총 1.742시간 소요)
* **조기 종료 조건**: `EarlyStopping(patience=30)` 170 에포크 발동 (Best Epoch 140)
* **실측 지표 (Conf 0.25 기준)**:
  * **mAP50**: **`16.8%`** (Wornout: 14.5%, ripped: 19.1%)
  * **Recall**: **`27.1%`** (Wornout: 24.6%, ripped: 29.5%)
  * **Precision**: **`21.6%`** (Wornout: 23.4%, ripped: 19.7%)
* **결론 및 교선**: 조장님의 예리한 수치 지적대로 패치 경계선 착시로 인한 mAP50 수직 상승 불가 현상이 실측 증명됨에 따라, 단일 신규 모델 사용을 폐기하고 **검증된 WBF 앙상블 파이프라인(`v6_recall` Recall 84.1% + `base_model` Precision 91.2%)으로 서빙 파이프라인 100% 확정**.
"""
    full_history = history_text + v251_history_section
    with open(history_p, 'w', encoding='utf-8') as f:
        f.write(full_history)

    # 2. YOLOv8_모델_학습결과_분석보고서.md 누적 업데이트
    analysis_p = pm_dir / 'YOLOv8_모델_학습결과_분석보고서.md'
    with open(analysis_p, 'r', encoding='utf-8') as f:
        analysis_text = f.read()

    v251_analysis_section = """

### B. Stage 1 YOLOv8m 훈련 최종 종료 실측 지표 (Epoch 170 Early Stopping)
* **Best Epoch**: 140 (1.742시간 구동 후 EarlyStopping 발동)
* **Wornout 실측**: Precision 23.4%, Recall 24.6%, mAP50 14.5%
* **ripped 실측**: Precision 19.7%, Recall 29.5%, mAP50 19.1%
* **전체 종합**: Precision 21.6%, Recall 27.1%, mAP50 16.8%
* **결정**: WBF 앙상블 파이프라인으로 전환하여 Recall 84.1% & Precision 91.2% 서빙 달성.
"""
    full_analysis = analysis_text + v251_analysis_section
    with open(analysis_p, 'w', encoding='utf-8') as f:
        f.write(full_analysis)

    # 3. WMS_docs 배포용 폴더로 이중 동기화 (Copy)
    for src_name, dst_name in targets:
        src_file = pm_dir / src_name
        dst_file = wms_dir / dst_name
        shutil.copy2(src_file, dst_file)
        print(f"  [WMS_docs Sync] Copied {src_name} -> {dst_file.name}")

    print(f"\n=======================================================")
    print(f"[SUCCESS] Final Training Results Recorded & Synced to WMS_docs!")
    print(f"=======================================================")

if __name__ == "__main__":
    record_results()
