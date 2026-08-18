"""
====================================================================
[YOLOv8 Auto-Labeling (Pre-annotation) Script for labelImg]
- 이 스크립트는 이미지 폴더를 지정하면, 우리가 학습시킨 YOLOv8 모델(yolov8_high_recall_best.pt)을
  사용하여 자동으로 YOLO 라벨 파일(.txt)을 생성해 줍니다.
- 생성된 라벨 파일은 labelImg(windows_v1.8.0)에서 열어서 1초 만에 확인/수정할 수 있습니다.
====================================================================
"""

import os
from pathlib import Path
from ultralytics import YOLO

# 1. 클래스 매핑 정의
CLASSES = ["ripped", "Wornout"]

def auto_label_images(image_dir: str, output_dir: str = None, conf: float = 0.12):
    model_path = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend\app\ai\yolov8_high_recall_best.pt')
    if not model_path.exists():
        print(f"[Error] 모델 가중치를 찾을 수 없습니다: {model_path}")
        return

    img_dir_path = Path(image_dir)
    if not img_dir_path.exists():
        print(f"[Error] 이미지 폴더가 존재하지 않습니다: {image_dir}")
        return

    if output_dir is None:
        output_dir = image_dir
    os.makedirs(output_dir, exist_ok=True)

    # classes.txt 자동 생성 (labelImg 인식용)
    classes_file = Path(output_dir) / "classes.txt"
    with open(classes_file, "w", encoding="utf-8") as f:
        f.write("\n".join(CLASSES) + "\n")

    model = YOLO(str(model_path))
    image_files = list(img_dir_path.glob("*.jpg")) + list(img_dir_path.glob("*.jpeg")) + list(img_dir_path.glob("*.png"))

    print(f"[Auto Labeling] {len(image_files)}개 이미지에 대해 YOLOv8 자동 라벨링 시작...")

    for img_p in image_files:
        results = model.predict(source=str(img_p), conf=conf, verbose=False)
        txt_p = Path(output_dir) / f"{img_p.stem}.txt"
        
        lines = []
        for r in results:
            w_img, h_img = r.orig_shape[1], r.orig_shape[0]
            for box in r.boxes:
                cls_id = int(box.cls[0])
                # YOLO format: class_id x_center y_center width height (normalized 0~1)
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                x_center = ((x1 + x2) / 2.0) / w_img
                y_center = ((y1 + y2) / 2.0) / h_img
                width = (x2 - x1) / w_img
                height = (y2 - y1) / h_img
                
                lines.append(f"{cls_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}")
        
        with open(txt_p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"  - Generated label for {img_p.name} ({len(lines)} BBoxes)")

    print(f"\n[SUCCESS] 자동 라벨링 완료! labelImg에서 '{image_dir}' 폴더를 열어서 확인하세요.")

if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else r"E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\Damaged Books.v3i.yolov8\valid\images"
    auto_label_images(target)
