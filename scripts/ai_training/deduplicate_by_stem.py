"""
====================================================================
[Unified Dataset Stem-Based Base Photo Deduplication Script]
- 동일 원본 촬영 사진(예: IMG_3_jpg)에서 발생한 Roboflow 중복 변형본들까지
  1개만 남기고 모두 정제하여 100% 순수 유일 실물 도서 사진 데이터셋으로 통제합니다.
====================================================================
"""

import os
import re
from pathlib import Path

dataset_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\unified_book_defect_dataset')

def extract_base_photo_id(filename):
    # ds1_IMG_3_jpg.rf.xxxx -> IMG_3_jpg
    # ds2_IMG_1142_jpg.rf.xxxx -> IMG_1142_jpg
    name = filename.replace('ds1_', '').replace('ds2_', '')
    if '.rf.' in name:
        base_id = name.split('.rf.')[0]
    else:
        base_id = Path(name).stem
    return base_id

def run_stem_deduplication():
    print(f"[Stem Deduplication] Auditing base photo duplicates in {dataset_dir}...")
    
    seen_base_photos = {}
    removed_stem_count = 0
    scanned_count = 0

    subdirs = ['train', 'valid', 'test']
    
    for sdir in subdirs:
        img_dir = dataset_dir / sdir / 'images'
        lbl_dir = dataset_dir / sdir / 'labels'
        
        if not img_dir.exists():
            continue

        images = list(img_dir.glob('*.jpg')) + list(img_dir.glob('*.jpeg')) + list(img_dir.glob('*.png'))
        scanned_count += len(images)

        for img_path in images:
            base_photo_id = extract_base_photo_id(img_path.name)
            lbl_path = lbl_dir / f"{img_path.stem}.txt"

            if base_photo_id in seen_base_photos:
                # 중복 원본 사진 변형본 삭제
                if img_path.exists():
                    os.remove(img_path)
                if lbl_path.exists():
                    os.remove(lbl_path)
                removed_stem_count += 1
                print(f"  [Base Photo Duplicate Removed] {img_path.name} (Base: {base_photo_id}, kept {seen_base_photos[base_photo_id].name})")
            else:
                seen_base_photos[base_photo_id] = img_path

    print(f"\n=======================================================")
    print(f"[SUCCESS] Base Photo Stem Deduplication Complete!")
    print(f"  - Scanned Images: {scanned_count}")
    print(f"  - Base Photo Duplicates Purged: {removed_stem_count}")
    print(f"  - Final 100% Pure Unique Base Photos Remaining: {len(seen_base_photos)}")
    print(f"=======================================================")

if __name__ == "__main__":
    run_stem_deduplication()
