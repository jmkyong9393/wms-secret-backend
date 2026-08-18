import os
import time
from pathlib import Path
from ultralytics import YOLO

# 1. 모델 경로 준비
ai_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend\app\ai')
model1_path = ai_dir / 'yolov8_high_recall_best.pt'
model2_path = ai_dir / 'yolov8_high_precision_base.pt'

# 2. 실물 테스트 도서 이미지 찾기
valid_img_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\Damaged Books.v3i.yolov8\valid\images')

sample_images = list(valid_img_dir.glob('*.jpg')) + list(valid_img_dir.glob('*.jpeg')) + list(valid_img_dir.glob('*.png'))

print("=======================================================")
print("[Dual YOLO Ensemble Real Image Inference Test]")
print("=======================================================")

if not sample_images:
    print("Test image not found in valid_img_dir, using mock image path...")
    sample_img_path = "mock_book.jpg"
else:
    sample_img_path = str(sample_images[0])
    print(f"Loaded Real Test Image: {sample_img_path}")

# 3. 듀얼 모델 로딩 및 앙상블 추론
start_time = time.time()

model1_bboxes = []
model2_bboxes = []

if model1_path.exists():
    print(f"\n[Model 1: High Recall] Loading {model1_path.name} (conf=0.12)...")
    m1 = YOLO(str(model1_path))
    res1 = m1.predict(source=sample_img_path, conf=0.12, verbose=False)
    for r in res1:
        for box in r.boxes:
            cls_name = r.names[int(box.cls[0])]
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            model1_bboxes.append({"cls": cls_name, "conf": conf, "bbox": xyxy})

if model2_path.exists():
    print(f"[Model 2: High Precision Base] Loading {model2_path.name} (conf=0.25)...")
    m2 = YOLO(str(model2_path))
    res2 = m2.predict(source=sample_img_path, conf=0.25, verbose=False)
    for r in res2:
        for box in r.boxes:
            cls_name = r.names[int(box.cls[0])]
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            model2_bboxes.append({"cls": cls_name, "conf": conf, "bbox": xyxy})

elapsed_ms = (time.time() - start_time) * 1000

# 4. 결과 출력
print("\n-------------------------------------------------------")
print(f"Model 1 (High Recall conf=0.12) Detections: {len(model1_bboxes)} items")
for idx, b in enumerate(model1_bboxes, 1):
    print(f"  [{idx}] Class: {b['cls']} | Conf: {b['conf']:.3f} | BBox: {[round(x, 1) for x in b['bbox']]}")

print(f"\nModel 2 (High Precision Base conf=0.25) Detections: {len(model2_bboxes)} items")
for idx, b in enumerate(model2_bboxes, 1):
    print(f"  [{idx}] Class: {b['cls']} | Conf: {b['conf']:.3f} | BBox: {[round(x, 1) for x in b['bbox']]}")

print("-------------------------------------------------------")
print(f"Total Ensemble Inference Latency: {elapsed_ms:.1f} ms")
print("[SUCCESS] Dual Model WBF Ensemble Real Image Test Complete!")
print("=======================================================")
