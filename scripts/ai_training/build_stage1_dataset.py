"""
====================================================================
[Stage 1 실물 도서 결함 무결점 데이터셋 구축 스크립트]
- 정제된 1,037장 실물 도서 마스터 데이터셋을 바탕으로
  Train Set에만 Multi-BBox CopyPaste를 적용하여 Class 1(ripped)을 1,700개로 밸런싱.
- Validation / Test Set은 100% 순수 원본 실물 이미지로 성역화(보존).
- stage1_book_defect_dataset 구축 및 data.yaml 생성.
====================================================================
"""

import os
import shutil
import random
from pathlib import Path
from collections import Counter
import cv2
import numpy as np

src_dataset_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\unified_book_defect_dataset')
dst_dataset_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\stage1_book_defect_dataset')

def extract_ripped_patches(train_img_dir, train_lbl_dir):
    patches = []
    for lbl_p in train_lbl_dir.glob('*.txt'):
        with open(lbl_p, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        ripped_bboxes = [line.strip().split() for line in lines if line.strip() and line.strip().split()[0] == '1']
        if not ripped_bboxes:
            continue

        img_p = train_img_dir / f"{lbl_p.stem}.jpg"
        if not img_p.exists():
            img_p = train_img_dir / f"{lbl_p.stem}.png"
        if not img_p.exists():
            continue

        img = cv2.imdecode(np.fromfile(str(img_p), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue

        h_img, w_img = img.shape[:2]
        for parts in ripped_bboxes:
            xc, yc, bw, bh = [float(x) for x in parts[1:5]]
            px1 = max(0, int((xc - bw / 2.0) * w_img))
            py1 = max(0, int((yc - bh / 2.0) * h_img))
            px2 = min(w_img, int((xc + bw / 2.0) * w_img))
            py2 = min(h_img, int((yc + bh / 2.0) * h_img))
            
            patch = img[py1:py2, px1:px2]
            if patch.shape[0] > 8 and patch.shape[1] > 8:
                patches.append(patch)
    return patches

def build_stage1_dataset():
    print(f"[Stage 1 Builder] Resetting {dst_dataset_dir}...")
    if dst_dataset_dir.exists():
        shutil.rmtree(dst_dataset_dir)

    for sdir in ['train', 'valid', 'test']:
        os.makedirs(dst_dataset_dir / sdir / 'images', exist_ok=True)
        os.makedirs(dst_dataset_dir / sdir / 'labels', exist_ok=True)

    # 1. 원본 복사
    for sdir in ['train', 'valid', 'test']:
        src_img_dir = src_dataset_dir / sdir / 'images'
        src_lbl_dir = src_dataset_dir / sdir / 'labels'
        
        dst_img_dir = dst_dataset_dir / sdir / 'images'
        dst_lbl_dir = dst_dataset_dir / sdir / 'labels'

        if not src_img_dir.exists():
            continue

        for img_p in src_img_dir.glob('*.*'):
            if img_p.suffix.lower() in ['.jpg', '.jpeg', '.png']:
                shutil.copy2(img_p, dst_img_dir / img_p.name)
                lbl_p = src_lbl_dir / f"{img_p.stem}.txt"
                if lbl_p.exists():
                    shutil.copy2(lbl_p, dst_lbl_dir / f"{img_p.stem}.txt")

    # 2. Train Set에만 Multi-BBox CopyPaste 적용 (ripped 보강)
    t_img_dir = dst_dataset_dir / 'train' / 'images'
    t_lbl_dir = dst_dataset_dir / 'train' / 'labels'
    
    ripped_patches = extract_ripped_patches(t_img_dir, t_lbl_dir)
    print(f"[Stage 1 Builder] Extracted {len(ripped_patches)} pure real 'ripped' patches.")

    if ripped_patches:
        train_images = list(t_img_dir.glob('*.*'))
        added_copypaste = 0

        # target ~1,200 additional ripped BBoxes
        for i in range(1200):
            target_img_p = random.choice(train_images)
            target_lbl_p = t_lbl_dir / f"{target_img_p.stem}.txt"

            img = cv2.imdecode(np.fromfile(str(target_img_p), np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue

            h_tar, w_tar = img.shape[:2]
            patch = random.choice(ripped_patches)
            ph, pw = patch.shape[:2]

            if ph >= h_tar or pw >= w_tar:
                continue

            px1 = random.randint(int(w_tar * 0.05), max(int(w_tar * 0.05) + 1, w_tar - pw - int(w_tar * 0.05)))
            py1 = random.randint(int(h_tar * 0.05), max(int(h_tar * 0.05) + 1, h_tar - ph - int(h_tar * 0.05)))
            px2, py2 = px1 + pw, py1 + ph

            img[py1:py2, px1:px2] = patch

            # 이미지 Overwrite 저장
            cv2.imencode(target_img_p.suffix, img)[1].tofile(str(target_img_p))

            # BBox 라벨 추가
            n_xc = ((px1 + px2) / 2.0) / w_tar
            n_yc = ((py1 + py2) / 2.0) / h_tar
            n_bw = pw / w_tar
            n_bh = ph / h_tar

            with open(target_lbl_p, 'a', encoding='utf-8') as f:
                f.write(f"\n1 {n_xc:.6f} {n_yc:.6f} {n_bw:.6f} {n_bh:.6f}")

            added_copypaste += 1

        print(f"[Stage 1 Builder] Multi-BBox CopyPaste added {added_copypaste} ripped instances.")

    # 3. 최종 수량 실측 검증
    final_counts = Counter()
    total_imgs = 0

    for root, _, files in os.walk(dst_dataset_dir):
        for f in files:
            if f.endswith(('.jpg', '.jpeg', '.png')):
                total_imgs += 1
            elif f.endswith('.txt') and f != 'classes.txt':
                with open(Path(root) / f, 'r', encoding='utf-8', errors='ignore') as tf:
                    for line in tf:
                        parts = line.strip().split()
                        if parts and parts[0].isdigit():
                            final_counts[int(parts[0])] += 1

    # 4. data.yaml 생성 (Windows 역슬래시 escape error 방지)
    yaml_p = dst_dataset_dir / "data.yaml"
    yaml_content = f"""path: {dst_dataset_dir.as_posix()}
train: train/images
val: valid/images
test: test/images
nc: 2
names:
  0: Wornout
  1: ripped
"""
    with open(yaml_p, 'w', encoding='utf-8') as f:
        f.write(yaml_content)

    print(f"\n=======================================================")
    print(f"[SUCCESS] Stage 1 Dataset Built Successfully!")
    print(f"  - Dataset Path: {dst_dataset_dir}")
    print(f"  - Total Clean Images: {total_imgs}")
    print(f"  - Final BBox Counts: {dict(final_counts)}")
    print(f"  - Class 0 (Wornout): {final_counts[0]}")
    print(f"  - Class 1 (ripped): {final_counts[1]}")
    print(f"=======================================================")

if __name__ == "__main__":
    build_stage1_dataset()
