"""
====================================================================
[YOLOv8 Class 2 'doodle' Transfer Learning & Fine-Tuning Script]
- 기존 최우수 가중치(yolov8_high_recall_best.pt)에 Class 2 'doodle' 헤드를 추가하여
  322장의 낙서 데이터셋으로 전이 학습을 가동합니다.
====================================================================
"""

import os
from pathlib import Path
from ultralytics import YOLO

def run_doodle_training():
    # 1. 경로 정의
    data_yaml = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\doodle_augmented_dataset\data.yaml')
    base_weights = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend\app\ai\yolov8_high_recall_best.pt')
    
    if not data_yaml.exists():
        print(f"[Error] data.yaml 파일이 없습니다: {data_yaml}")
        return

    if not base_weights.exists():
        print(f"[Error] 베이스 가중치 파일이 없습니다: {base_weights}")
        return

    print(f"=======================================================")
    print(f"[YOLOv8 v7_doodle Fine-Tuning] Starting Transfer Learning...")
    print(f"  - Base Model: {base_weights.name}")
    print(f"  - Dataset YAML: {data_yaml}")
    print(f"=======================================================")

    # 2. YOLO 모델 로딩 및 전이 학습 실행
    model = YOLO(str(base_weights))

    results = model.train(
        data=str(data_yaml),
        epochs=30,           # 전이 학습 30 에포크 (빠르고 고속 수렴)
        imgsz=640,
        batch=8,
        name="train_yolo_v7_doodle",
        project=r"E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend\runs\detect",
        amp=True,
        workers=2,
        exist_ok=True
    )

    print(f"\n=======================================================")
    print(f"[SUCCESS] YOLOv8 v7_doodle Training Complete!")
    print(f"=======================================================")

if __name__ == "__main__":
    run_doodle_training()
