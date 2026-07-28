"""
====================================================================
[현재 진행 중인 YOLOv8 가중치 conf 임계값별 실측 검증 스크립트]
- runs/stage1_yolov8m_200e/weights/last.pt 및 best.pt 가중치를 로드하여
  conf=0.25 vs conf=0.15 vs conf=0.10 환경에서 실측 mAP50 / Recall / Precision 수치를 1초 만에 검증합니다.
====================================================================
"""

import os
from pathlib import Path
from ultralytics import YOLO

weights_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\runs\stage1_yolov8m_200e\weights')
data_yaml = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\stage1_book_defect_dataset\data.yaml')

def check_val():
    target_weights = weights_dir / "last.pt"
    if not target_weights.exists():
        target_weights = weights_dir / "best.pt"
    
    if not target_weights.exists():
        print(f"[Error] Weights file not found in {weights_dir}")
        return

    print(f"[Validation Check] Loading weights from {target_weights.name}...")
    model = YOLO(str(target_weights))

    print(f"\n=======================================================")
    print(f"[Real Time Metrics Audit across Confidence Thresholds]")
    print(f"=======================================================")

    for conf_val in [0.25, 0.15, 0.10, 0.05]:
        metrics = model.val(
            data=str(data_yaml),
            imgsz=800,
            batch=8,
            conf=conf_val,
            verbose=False,
            device=0
        )
        print(f"▶ conf={conf_val:.2f} | Precision: {metrics.results_dict['metrics/precision(B)']:.4f} | Recall: {metrics.results_dict['metrics/recall(B)']:.4f} | mAP50: {metrics.results_dict['metrics/mAP50(B)']:.4f}")

if __name__ == "__main__":
    check_val()
