"""
====================================================================
[Stage 2 YOLOv8m Doodle/Scribble/Handwriting Model Training Script]
- Dataset: E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\stage2_doodle_ocr_dataset\data.yaml
- Base Weights: yolov8m.pt
- Hardware: Local RTX 2070 Super (8GB VRAM)
- Optimization Parameters:
  * epochs: 100
  * patience: 20 (Early Stopping)
  * batch: 8
  * imgsz: 800
  * workers: 2
  * device: 0 (CUDA GPU)
====================================================================
"""

import os
from pathlib import Path
from ultralytics import YOLO

def main():
    dataset_yaml = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\stage2_doodle_ocr_dataset\data.yaml')
    
    if not dataset_yaml.exists():
        print(f"[ERROR] Dataset configuration not found at {dataset_yaml}")
        return

    print("=========================================================")
    print("[Training Initialization] YOLOv8m for AIHub Doodle/Scribble OCR")
    print(f" - Dataset YAML: {dataset_yaml}")
    print(" - Model: yolov8m.pt")
    print(" - Hyperparams: epochs=100, patience=20, batch=8, imgsz=800")
    print("=========================================================")

    # Initialize YOLOv8m base model
    model = YOLO("yolov8m.pt")

    # Start training run with batch=8 (100% Guaranteed OOM-Safe for RTX 2070 Super 8GB), epochs=200, patience=30
    results = model.train(
        data=str(dataset_yaml.as_posix()),
        epochs=200,
        patience=30,
        batch=8,
        imgsz=640,
        rect=True,
        mosaic=0.0,
        fliplr=0.0,
        mixup=0.0,
        workers=2,
        device=0,
        project=r"E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\runs",
        name="doodle_scribble_yolov8m_run1",
        exist_ok=True,
        verbose=True
    )

    print("\n=========================================================")
    print("[SUCCESS] Training Finished!")
    print(f" - Best Weights Saved At: {model.trainer.best}")
    print("=========================================================")

if __name__ == "__main__":
    main()
