"""
====================================================================
[Unified Book Defect Dataset 안전 완전 복원 & MD5 순수 중복 제거 스크립트]
- 원본 소스 데이터셋(v3i, v9i)에서 모든 이미지 및 라벨을 100% 원형 복원합니다.
- 다각도 촬영(Multi-view) 사진 및 Roboflow 각도 변형본(Pixel Diff > 0)은 온전히 보존하고,
  오직 100% 픽셀이 일치하는 '순수 MD5 바이너리 동일 파일'만 안전 제거합니다.
====================================================================
"""

import os
import shutil
import hashlib
from pathlib import Path

v3i_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\Damaged Books.v3i.yolov8')
v9i_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\second-hand-book-defect.v9i.yolov8')

target_dataset_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\unified_book_defect_dataset')

def get_file_md5(file_path):
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        buf = f.read(65536)
        while len(buf) > 0:
            hasher.update(buf)
            buf = f.read(65536)
    return hasher.hexdigest()

def restore_and_clean_dataset():
    print(f"[Restore Engine] Resetting and rebuilding {target_dataset_dir} from v3i and v9i...")
    
    # target_dataset_dir 완전 초기화
    if target_dataset_dir.exists():
        shutil.rmtree(target_dataset_dir)
        
    for sdir in ['train', 'valid', 'test']:
        os.makedirs(target_dataset_dir / sdir / 'images', exist_ok=True)
        os.makedirs(target_dataset_dir / sdir / 'labels', exist_ok=True)

    seen_md5_hashes = {}
    copied_count = 0
    md5_duplicates_filtered = 0

    sources = [('ds1_', v3i_dir), ('ds2_', v9i_dir)]

    for prefix, src_dir in sources:
        for sdir in ['train', 'valid', 'test']:
            src_img_dir = src_dir / sdir / 'images'
            src_lbl_dir = src_dir / sdir / 'labels'
            
            if not src_img_dir.exists():
                continue

            for img_p in src_img_dir.glob('*.*'):
                if img_p.suffix.lower() not in ['.jpg', '.jpeg', '.png']:
                    continue
                
                md5_val = get_file_md5(img_p)
                
                # 100% 완전 동일한 바이너리 파일만 걸러내기 (다각도 사진은 보존)
                if md5_val in seen_md5_hashes:
                    md5_duplicates_filtered += 1
                    continue
                
                seen_md5_hashes[md5_val] = img_p

                # 복사 대상 경로 지정
                dest_img_name = f"{prefix}{img_p.name}"
                dest_lbl_name = f"{prefix}{img_p.stem}.txt"
                
                dest_img_p = target_dataset_dir / sdir / 'images' / dest_img_name
                dest_lbl_p = target_dataset_dir / sdir / 'labels' / dest_lbl_name
                
                shutil.copy2(img_p, dest_img_p)
                
                src_lbl_p = src_lbl_dir / f"{img_p.stem}.txt"
                if src_lbl_p.exists():
                    shutil.copy2(src_lbl_p, dest_lbl_p)

                copied_count += 1

    # data.yaml 생성
    update_data_yaml()

    print(f"\n=======================================================")
    print(f"[SUCCESS] Dataset 100% Restored & Safe MD5 Cleaned!")
    print(f"  - Total Safe Restored Images: {copied_count}")
    print(f"  - Purged Pure Binary MD5 Duplicates: {md5_duplicates_filtered}")
    print(f"  - Multi-view & Angle Photos Preserved: 100%")
    print(f"=======================================================")

def update_data_yaml():
    yaml_path = target_dataset_dir / "data.yaml"
    WMS_13_CLASSES = [
        "Wornout", "ripped", "doodle", "COVER_SCRATCH", "COVER_TEAR",
        "COVER_STICKER", "EDGE_CORNER_DAMAGE", "EDGE_WEAR", "STAIN_DIRT",
        "STAIN_FADING", "STAIN_WATER_DAMAGE", "PAGE_WARPING", "BINDING_LOOSE"
    ]
    lines = [
        f'train: "{target_dataset_dir / "train" / "images"}"',
        f'val: "{target_dataset_dir / "valid" / "images"}"',
        f'test: "{target_dataset_dir / "test" / "images"}"',
        f'nc: {len(WMS_13_CLASSES)}',
        'names:'
    ]
    for idx, cname in enumerate(WMS_13_CLASSES):
        lines.append(f'  {idx}: {cname}')
    with open(yaml_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines) + "\n")

if __name__ == "__main__":
    restore_and_clean_dataset()
