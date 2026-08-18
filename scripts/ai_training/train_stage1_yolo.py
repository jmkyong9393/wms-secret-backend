"""
====================================================================
[RTX 2070 Super (8GB VRAM) 시간 절약 최적화 Stage 1 YOLOv8m 훈련 스크립트]
- epochs=200 (150~200 에포크로 충분한 수렴 달성)
- patience=30 (30 에포크 동안 mAP 미개선 시 자동 조기 종료 Early Stopping)
- batch=8, imgsz=800
====================================================================
"""

import os
from pathlib import Path
from ultralytics import YOLO

dataset_yaml = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\stage1_book_defect_dataset\data.yaml')
project_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\runs')

def main():
    print(f"[Training Optimization] Initializing YOLOv8m for RTX 2070 Super (8GB VRAM)...")
    print(f"[Training Optimization] Dataset YAML: {dataset_yaml}")
    
    model = YOLO("yolov8m.pt")
    
    results = model.train(
        data=str(dataset_yaml),
        epochs=200,        # 200 에포크로 시간 50% 절감 (약 5~6시간)
        patience=30,       # 30 에포크 동안 mAP 미개선 시 자동 조기 종료 (Early Stopping)
        imgsz=800,
        batch=8,           # 8GB VRAM OOM 방지
        workers=2,         # System RAM/CPU 최적화
        device=0,
        project=str(project_dir),
        name="stage1_yolov8m_200e",
        mosaic=1.0,
        mixup=0.15,
        save=True,
        plots=True,
        exist_ok=True
    )
    print(f"\n[SUCCESS] Stage 1 Training Completed! Results saved in {project_dir / 'stage1_yolov8m_200e'}")

if __name__ == "__main__":
    main()
