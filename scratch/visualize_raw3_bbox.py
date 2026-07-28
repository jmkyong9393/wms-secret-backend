import os
import shutil
from pathlib import Path
import cv2

# 1. 파일 경로
img_path = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend\app\experiment_data\job-f309b042\raw_3.jpg')
txt_path = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend\app\experiment_data\job-f309b042\raw_3.txt')

# 2. 아티팩트 저장소
artifact_dir = Path(r'C:\Users\jmkyo\.gemini\antigravity\brain\e3581def-d658-43e3-94b1-c67850b88493')
os.makedirs(artifact_dir, exist_ok=True)

out_orig = artifact_dir / 'raw3_orig.jpg'
out_annotated = artifact_dir / 'raw3_annotated.jpg'

CLASSES_MAP = {0: "ripped", 1: "Wornout"}

import numpy as np

if img_path.exists():
    shutil.copy2(img_path, out_orig)
    # 한글 윈도우 경로 호환 imdecode
    img_array = np.fromfile(str(img_path), np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    h_img, w_img = img.shape[0], img.shape[1]


    if txt_path.exists():
        with open(txt_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 5:
                cls_id = int(parts[0])
                x_center = float(parts[1]) * w_img
                y_center = float(parts[2]) * h_img
                w = float(parts[3]) * w_img
                h = float(parts[4]) * h_img

                x1 = int(x_center - w / 2.0)
                y1 = int(y_center - h / 2.0)
                x2 = int(x_center + w / 2.0)
                y2 = int(y_center + h / 2.0)

                cls_name = CLASSES_MAP.get(cls_id, f"Class {cls_id}")

                # BBox 두꺼운 두께로 그리기 (초록색 상자, 빨간색 배경 텍스트)
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 3)
                
                label = f"{cls_name} (Class {cls_id})"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                cv2.rectangle(img, (x1, max(y1 - 25, 0)), (x1 + tw + 10, max(y1, 25)), (0, 0, 255), -1)
                cv2.putText(img, label, (x1 + 5, max(y1 - 7, 18)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                
                print(f"Rendered BBox: {cls_name} -> Pixel [{x1}, {y1}, {x2}, {y2}], Rel %: x={x1/w_img*100:.2f}%, y={y1/h_img*100:.2f}%, w={w/w_img*100:.2f}%, h={h/h_img*100:.2f}%")

    # 한글 윈도우 경로 호환 imencode 저장
    is_success, im_buf = cv2.imencode(".jpg", img)
    if is_success:
        im_buf.tofile(str(out_annotated))
    print(f"Saved annotated image to: {out_annotated}")

