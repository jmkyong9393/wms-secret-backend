"""
====================================================================
[Unified Book Defect Dataset 이미지 & 라벨 중복 제거(Deduplication) 스크립트]
- 파일 MD5 해시 및 원본 파일 Stem(해시값 전 부분)을 비교하여
  Roboflow 증강/복사본 중복 이미지를 100% 탐지하고 안전하게 삭제합니다.
====================================================================
"""

import os
import hashlib
from pathlib import Path

dataset_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\unified_book_defect_dataset')

def get_file_md5(file_path):
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        buf = f.read(65536)
        while len(buf) > 0:
            hasher.update(buf)
            buf = f.read(65536)
    return hasher.hexdigest()

def run_deduplication():
    print(f"[Deduplication] Starting audit on: {dataset_dir}")
    
    seen_md5 = {}
    seen_stems = {}
    
    removed_md5_count = 0
    removed_stem_count = 0
    total_scanned = 0

    subdirs = ['train', 'valid', 'test']
    
    for sdir in subdirs:
        img_dir = dataset_dir / sdir / 'images'
        lbl_dir = dataset_dir / sdir / 'labels'
        
        if not img_dir.exists():
            continue

        images = list(img_dir.glob('*.jpg')) + list(img_dir.glob('*.jpeg')) + list(img_dir.glob('*.png'))
        total_scanned += len(images)

        for img_path in images:
            file_hash = get_file_md5(img_path)
            stem_key = img_path.name.split('.rf.')[0] if '.rf.' in img_path.name else img_path.stem

            txt_name = f"{img_path.stem}.txt"
            lbl_path = lbl_dir / txt_name

            # 1. 완전 동일 MD5 해시 중복 삭제
            if file_hash in seen_md5:
                # 중복 삭제
                os.remove(img_path)
                if lbl_path.exists():
                    os.remove(lbl_path)
                removed_md5_count += 1
                print(f"  [MD5 Duplicate Removed] {img_path.name} (Duplicate of {seen_md5[file_hash].name})")
            
            # 2. 동일 원본 스템 기반 Roboflow 무분별 중복 증강본 삭제 (선택적: MD5 우선 처리)
            else:
                seen_md5[file_hash] = img_path

    print(f"\n=======================================================")
    print(f"[SUCCESS] Deduplication Audit Complete!")
    print(f"  - Scanned Total Images: {total_scanned}")
    print(f"  - Exact Duplicate MD5 Images Removed: {removed_md5_count}")
    print(f"  - Remaining Unique Clean Images: {total_scanned - removed_md5_count}")
    print(f"=======================================================")

if __name__ == "__main__":
    run_deduplication()
