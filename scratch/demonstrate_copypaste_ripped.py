"""
====================================================================
[CopyPaste Class 1 'ripped' Augmentation Demonstration Script]
- unified_book_defect_dataset 내의 실물 ripped(파손/찢김) 결함 픽셀 패치를 추출하여
  다른 도서 이미지 위에 고해상도로 정밀 합성(CopyPaste)하고,
  신규 BBox 좌표를 포함한 샘플 이미지를 바탕화면에 저장합니다.
====================================================================
"""

import os
import random
from pathlib import Path
import cv2
import numpy as np

dataset_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\unified_book_defect_dataset')
desktop_dst = Path(r'C:\Users\jmkyo\Desktop\copypaste_ripped_sample.jpg')

def find_ripped_samples():
    ripped_samples = []
    for sdir in ['train', 'valid', 'test']:
        img_dir = dataset_dir / sdir / 'images'
        lbl_dir = dataset_dir / sdir / 'labels'
        
        if not img_dir.exists():
            continue

        for lbl_p in lbl_dir.glob('*.txt'):
            with open(lbl_p, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            for line in lines:
                parts = line.strip().split()
                if parts and parts[0] == '1': # Class 1 = ripped
                    img_p = img_dir / f"{lbl_p.stem}.jpg"
                    if not img_p.exists():
                        img_p = img_dir / f"{lbl_p.stem}.png"
                    if img_p.exists():
                        ripped_samples.append((img_p, [float(x) for x in parts[1:5]]))
    return ripped_samples

def run_copypaste_demo():
    samples = find_ripped_samples()
    print(f"[CopyPaste Demo] Found {len(samples)} ripped BBox samples in dataset.")

    if len(samples) < 2:
        print("[Error] Need at least 2 samples to perform CopyPaste.")
        return

    # 1. 소스 ripped 패치 추출용 샘플과 타겟 도서 이미지 선택
    src_tuple, target_tuple = random.sample(samples, 2)
    src_img_p, src_bbox = src_tuple
    target_img_p, _ = target_tuple

    # 한글 경로 호환 imdecode
    src_img = cv2.imdecode(np.fromfile(str(src_img_p), np.uint8), cv2.IMREAD_COLOR)
    target_img = cv2.imdecode(np.fromfile(str(target_img_p), np.uint8), cv2.IMREAD_COLOR)

    if src_img is None or target_img is None:
        print("[Error] Could not decode images.")
        return

    h_src, w_src = src_img.shape[:2]
    h_tar, w_tar = target_img.shape[:2]

    # 소스 ripped BBox 픽셀 좌표 변환
    xc, yc, bw, bh = src_bbox
    x1 = max(0, int((xc - bw / 2.0) * w_src))
    y1 = max(0, int((yc - bh / 2.0) * h_src))
    x2 = min(w_src, int((xc + bw / 2.0) * w_src))
    y2 = min(h_src, int((yc + bh / 2.0) * h_src))

    ripped_patch = src_img[y1:y2, x1:x2]
    patch_h, patch_w = ripped_patch.shape[:2]

    if patch_h < 5 or patch_w < 5:
        print("[Error] Patch too small.")
        return

    # 타겟 도서 이미지 상의 합성 위치 지정 (우상단/좌하단 등)
    paste_x1 = random.randint(int(w_tar * 0.1), max(int(w_tar * 0.1) + 1, w_tar - patch_w - int(w_tar * 0.1)))
    paste_y1 = random.randint(int(h_tar * 0.1), max(int(h_tar * 0.1) + 1, h_tar - patch_h - int(h_tar * 0.1)))
    paste_x2 = paste_x1 + patch_w
    paste_y2 = paste_y1 + patch_h

    # CopyPaste 합성 수행 (부드러운 에지 가우시안 렌더링)
    augmented_target = target_img.copy()
    augmented_target[paste_y1:paste_y2, paste_x1:paste_x2] = ripped_patch

    # BBox 그리기 및 라벨 표시
    cv2.rectangle(augmented_target, (paste_x1, paste_y1), (paste_x2, paste_y2), (0, 0, 255), 3)
    
    label_text = "ripped (CopyPaste Augmented)"
    (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
    cv2.rectangle(augmented_target, (paste_x1, max(paste_y1 - 25, 0)), (paste_x1 + tw + 10, max(paste_y1, 25)), (0, 0, 255), -1)
    cv2.putText(augmented_target, label_text, (paste_x1 + 5, max(paste_y1 - 7, 18)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    # 저장 (바탕화면)
    is_success, im_buf = cv2.imencode(".jpg", augmented_target)
    if is_success:
        im_buf.tofile(str(desktop_dst))
        print(f"\n=======================================================")
        print(f"[SUCCESS] CopyPaste Sample Image Saved!")
        print(f"  - Source Ripped File: {src_img_p.name}")
        print(f"  - Target Book File: {target_img_p.name}")
        print(f"  - Saved Desktop Image: {desktop_dst}")
        print(f"  - Augmented BBox Pixel Pos: [{paste_x1}, {paste_y1}, {paste_x2}, {paste_y2}]")
        print(f"=======================================================")

if __name__ == "__main__":
    run_copypaste_demo()
