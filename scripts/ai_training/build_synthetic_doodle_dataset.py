"""
====================================================================
[Synthetic Doodle Dataset Generator for YOLOv8 Fine-Tuning]
- 기존 55장 이상의 도서 이미지 위에 컴퓨터 비전(OpenCV) 기반으로
  볼펜, 연필, 형광펜, 수성펜 등의 현실적인 손글씨 낙서/스크래치를 합성합니다.
- 동시에 바운딩 박스(BBox) 좌표와 YOLO 포맷 (.txt) 라벨 및 data.yaml을
  100% 정밀하게 자동으로 생성합니다.
====================================================================
"""

import os
import random
import glob
from pathlib import Path
import cv2
import numpy as np

# 1. 경로 설정
backend_exp_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend\app\experiment_data')
val_img_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\Damaged Books.v3i.yolov8\valid\images')

output_dataset_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\doodle_augmented_dataset')

train_img_dir = output_dataset_dir / 'images' / 'train'
val_img_dir_out = output_dataset_dir / 'images' / 'val'
train_lbl_dir = output_dataset_dir / 'labels' / 'train'
val_lbl_dir_out = output_dataset_dir / 'labels' / 'val'

for d in [train_img_dir, val_img_dir_out, train_lbl_dir, val_lbl_dir_out]:
    os.makedirs(d, exist_ok=True)

# 2. classes 및 data.yaml 작성
CLASSES = ["ripped", "Wornout", "doodle"]

data_yaml_content = f"""path: {output_dataset_dir}
train: images/train
val: images/val

names:
  0: ripped
  1: Wornout
  2: doodle
"""

with open(output_dataset_dir / "data.yaml", "w", encoding="utf-8") as f:
    f.write(data_yaml_content)

print(f"[Dataset Generator] Created data.yaml at {output_dataset_dir / 'data.yaml'}")

# 3. 이미지 수집
source_images = []
for root, _, files in os.walk(backend_exp_dir):
    for f in files:
        if f.endswith(('.jpg', '.jpeg', '.png')):
            source_images.append(Path(root) / f)

if val_img_dir.exists():
    for f in val_img_dir.glob("*.jpg"):
        source_images.append(f)

print(f"[Dataset Generator] Total source images found: {len(source_images)}")

def draw_random_doodle(img):
    """
    OpenCV 기반 현실적인 낙서/필기 합성 함수
    """
    h_img, w_img = img.shape[:2]
    annotated = img.copy()

    # 낙서 영역 무작위 지정 (가로/세로 15~40% 크기)
    dw = int(w_img * random.uniform(0.15, 0.40))
    dh = int(h_img * random.uniform(0.10, 0.30))
    
    x1 = random.randint(int(w_img * 0.1), max(int(w_img * 0.1) + 1, w_img - dw - int(w_img * 0.1)))
    y1 = random.randint(int(h_img * 0.1), max(int(h_img * 0.1) + 1, h_img - dh - int(h_img * 0.1)))
    x2 = min(x1 + dw, w_img - 1)
    y2 = min(y1 + dh, h_img - 1)

    # 낙서 스타일 (볼펜, 형광펜, 수성펜, 밑줄)
    doodle_style = random.choice(["scribble", "underline", "text_squiggle", "marker"])

    # 색상 선택 (검은 볼펜, 파란 볼펜, 빨간 펜, 형광 노랑)
    colors = [(20, 20, 20), (180, 40, 20), (30, 30, 200), (0, 230, 255)] # BGR
    color = random.choice(colors)
    thickness = random.randint(1, 3) if doodle_style != "marker" else random.randint(5, 12)

    if doodle_style == "scribble":
        # 무작위 곡선 낙서
        pts = []
        num_pts = random.randint(8, 20)
        for _ in range(num_pts):
            px = random.randint(x1, x2)
            py = random.randint(y1, y2)
            pts.append([px, py])
        pts = np.array(pts, np.int32).reshape((-1, 1, 2))
        cv2.polylines(annotated, [pts], isClosed=False, color=color, thickness=thickness)

    elif doodle_style == "underline":
        # 삐뚤빼뚤한 밑줄 2~3줄
        for _ in range(random.randint(2, 4)):
            ly = random.randint(y1, y2)
            cv2.line(annotated, (x1, ly), (x2, ly + random.randint(-5, 5)), color, thickness)

    elif doodle_style == "text_squiggle":
        # 글씨 필기 같은 짧은 꼬불꼬불 선들
        for _ in range(random.randint(3, 6)):
            sy = random.randint(y1, y2)
            for sx in range(x1, x2 - 15, 15):
                cv2.line(annotated, (sx, sy), (sx + 10, sy + random.randint(-8, 8)), color, thickness)

    elif doodle_style == "marker":
        # 형광펜 투명도 하이라이트
        overlay = annotated.copy()
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 255), -1) # 노랑
        cv2.addWeighted(overlay, 0.35, annotated, 0.65, 0, annotated)

    # BBox YOLO 상대 좌표 산출 (Class ID 2 = doodle)
    x_center = ((x1 + x2) / 2.0) / w_img
    y_center = ((y1 + y2) / 2.0) / h_img
    bbox_w = (x2 - x1) / w_img
    bbox_h = (y2 - y1) / h_img

    yolo_label = f"2 {x_center:.6f} {y_center:.6f} {bbox_w:.6f} {bbox_h:.6f}"
    return annotated, yolo_label

# 4. 데이터셋 생성 루프 (총 120장 생성)
count = 0
for idx, img_path in enumerate(source_images):
    # 한글 경로 호환 imdecode
    img_array = np.fromfile(str(img_path), np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is None:
        continue

    # 이미지 당 2~3개 변형본 생성
    for k in range(2):
        count += 1
        annotated_img, label_line = draw_random_doodle(img)
        
        # Train / Val 8:2 분할
        if count % 5 == 0:
            target_i = val_img_dir_out / f"doodle_{count:04d}.jpg"
            target_l = val_lbl_dir_out / f"doodle_{count:04d}.txt"
        else:
            target_i = train_img_dir / f"doodle_{count:04d}.jpg"
            target_l = train_lbl_dir / f"doodle_{count:04d}.txt"

        # 한글 경로 호환 imencode 저장
        _, buf = cv2.imencode(".jpg", annotated_img)
        buf.tofile(str(target_i))

        with open(target_l, "w", encoding="utf-8") as f:
            f.write(label_line + "\n")

print(f"\n=======================================================")
print(f"[SUCCESS] Total {count} Synthetic Doodle Dataset Generated!")
print(f"  - Dataset Path: {output_dataset_dir}")
print(f"  - Train Images: {len(os.listdir(train_img_dir))}")
print(f"  - Val Images: {len(os.listdir(val_img_dir_out))}")
print(f"=======================================================")
