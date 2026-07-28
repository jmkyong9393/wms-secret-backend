import os
from pathlib import Path
import cv2
import numpy as np

v9i_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\second-hand-book-defect.v9i.yolov8')

p1 = None
p2 = None

for root, _, files in os.walk(v9i_dir):
    for f in files:
        if f == 'IMG_3_jpg.rf.20e8ac774ca51054c512b2eb28fd3f90.jpg':
            p1 = Path(root) / f
        elif f == 'IMG_3_jpg.rf.c685c0514d22f4b0f2ee980cf43fbd5b.jpg':
            p2 = Path(root) / f

print(f"Path 1: {p1}")
print(f"Path 2: {p2}")

if p1 and p2:
    i1 = cv2.imdecode(np.fromfile(str(p1), np.uint8), cv2.IMREAD_COLOR)
    i2 = cv2.imdecode(np.fromfile(str(p2), np.uint8), cv2.IMREAD_COLOR)
    
    if i1 is not None and i2 is not None:
        diff = cv2.absdiff(i1, i2)
        diff_score = np.sum(diff)
        print(f"\n=> Pixel Absolute Difference Sum between the 2 IMG_3 files: {diff_score}")
        if diff_score == 0:
            print("=> RESULT: 100% EXACT SAME BINARY IMAGE (Duplicate export!)")
        else:
            print(f"=> RESULT: DIFFERENT MULTI-VIEW OR AUGMENTED IMAGE! (Pixel Diff = {diff_score})")
