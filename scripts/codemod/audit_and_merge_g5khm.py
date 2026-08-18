"""
====================================================================
[Damaged Books_g5khm 정제, 검증 및 unified_book_defect_dataset 병합 스크립트]
1. Damaged Books_g5khm 의 data.yaml 및 라벨 클래스 분포 전수 검증
2. MD5 바이너리 해시 검사로 기존 unified_book_defect_dataset 과의 중복 100% 제거
3. 4대 슈퍼클래스로 라벨 ID 리매핑(Remapping):
   - Wornout (0): wear, folded, corner
   - ripped (1) : ripped, tear
   - doodle (2) : doodle, scribble
   - stain (3)  : stain, wet, water
4. unified_book_defect_dataset 에 안전하게 병합 복사!
====================================================================
"""

import os
import shutil
import hashlib
from pathlib import Path
from collections import Counter

g5khm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\Damaged Books_g5khm')
unified_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\unified_book_defect_dataset')

def get_file_md5(file_path):
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        buf = f.read(65536)
        while len(buf) > 0:
            hasher.update(buf)
            buf = f.read(65536)
    return hasher.hexdigest()

def audit_and_merge():
    print(f"[g5khm Audit] Checking dataset structure in {g5khm_dir}...")
    
    if not g5khm_dir.exists():
        print(f"[Error] Directory not found: {g5khm_dir}")
        return

    # 1. 기존 unified_dataset 의 MD5 해시 사전 구축
    existing_md5s = set()
    for root, _, files in os.walk(unified_dir):
        for f in files:
            if f.endswith(('.jpg', '.jpeg', '.png')):
                existing_md5s.add(get_file_md5(Path(root) / f))
    
    print(f"[g5khm Audit] Existing unified dataset MD5 hashes indexed: {len(existing_md5s)}")

    # 2. g5khm data.yaml 검사
    yaml_path = g5khm_dir / "data.yaml"
    g5khm_classes = {}
    if yaml_path.exists():
        with open(yaml_path, 'r', encoding='utf-8') as f:
            for line in f:
                if ':' in line and not line.startswith('path') and not line.startswith('train') and not line.startswith('val') and not line.startswith('test') and not line.startswith('nc') and not line.startswith('names'):
                    parts = line.strip().split(':')
                    if len(parts) == 2 and parts[0].strip().isdigit():
                        cls_idx = int(parts[0].strip())
                        cls_name = parts[1].strip().strip("'\"")
                        g5khm_classes[cls_idx] = cls_name
    
    print(f"[g5khm Audit] g5khm Classes found in data.yaml: {g5khm_classes}")

    # 4대 슈퍼클래스 매핑 사전 정의
    # 0: Wornout, 1: ripped, 2: doodle, 3: stain
    REMAP = {}
    for c_idx, c_name in g5khm_classes.items():
        name_lower = c_name.lower()
        if 'wear' in name_lower or 'fold' in name_lower or 'corner' in name_lower:
            REMAP[c_idx] = 0 # Wornout
        elif 'rip' in name_lower or 'tear' in name_lower:
            REMAP[c_idx] = 1 # ripped
        elif 'doodle' in name_lower or 'scribble' in name_lower:
            REMAP[c_idx] = 2 # doodle
        elif 'stain' in name_lower or 'wet' in name_lower or 'water' in name_lower:
            REMAP[c_idx] = 3 # stain
        else:
            REMAP[c_idx] = 0 # default Wornout

    print(f"[g5khm Audit] Class Remapping Table -> 4-Super-Categories: {REMAP}")

    # 3. 이미지 정제 및 병합
    merged_img_count = 0
    duplicate_filtered = 0
    bbox_counts = Counter()

    for sdir in ['train', 'valid', 'test']:
        img_dir = g5khm_dir / sdir / 'images'
        lbl_dir = g5khm_dir / sdir / 'labels'
        
        if not img_dir.exists():
            continue

        target_sub = 'train' if sdir == 'train' else 'valid'
        t_img_dir = unified_dir / target_sub / 'images'
        t_lbl_dir = unified_dir / target_sub / 'labels'
        
        os.makedirs(t_img_dir, exist_ok=True)
        os.makedirs(t_lbl_dir, exist_ok=True)

        for img_p in img_dir.glob('*.*'):
            if img_p.suffix.lower() not in ['.jpg', '.jpeg', '.png']:
                continue

            md5_val = get_file_md5(img_p)
            if md5_val in existing_md5s:
                duplicate_filtered += 1
                continue

            existing_md5s.add(md5_val)

            # 복사 및 라벨 리매핑
            dest_stem = f"g5khm_{img_p.stem}"
            dest_img_p = t_img_dir / f"{dest_stem}{img_p.suffix}"
            dest_lbl_p = t_lbl_dir / f"{dest_stem}.txt"

            shutil.copy2(img_p, dest_img_p)
            
            src_lbl_p = lbl_dir / f"{img_p.stem}.txt"
            remapped_lines = []
            if src_lbl_p.exists():
                with open(src_lbl_p, 'r', encoding='utf-8') as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 5:
                            old_cls = int(parts[0])
                            new_cls = REMAP.get(old_cls, 0)
                            bbox_counts[new_cls] += 1
                            remapped_lines.append(f"{new_cls} {' '.join(parts[1:])}")

            with open(dest_lbl_p, 'w', encoding='utf-8') as f:
                f.write("\n".join(remapped_lines) + "\n")

            merged_img_count += 1

    print(f"\n=======================================================")
    print(f"[SUCCESS] Damaged Books_g5khm Audit & Merge Complete!")
    print(f"  - Purged MD5 Duplicate Images: {duplicate_filtered}")
    print(f"  - New Clean Unique Images Merged: {merged_img_count}")
    print(f"  - Merged BBox Counts (4 Super-Categories): {dict(bbox_counts)}")
    print(f"=======================================================")

if __name__ == "__main__":
    audit_and_merge()
