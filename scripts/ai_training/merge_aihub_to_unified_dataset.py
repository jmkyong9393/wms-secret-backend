"""
====================================================================
[AI-Hub 6GB & Unified Book Defect Dataset 병합 스크립트]
- 기존 unified_book_defect_dataset (Wornout, ripped 2개 클래스)에 
  AI-Hub 낙서/손글씨 데이터셋을 병합하여 13대 표준 클래스 스키마로 확장합니다.
====================================================================
"""

import os
import json
import shutil
from pathlib import Path

unified_dataset_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\unified_book_defect_dataset')

# 13대 표준 WMS 결함 클래스 정의
WMS_13_CLASSES = [
    "Wornout",              # 0
    "ripped",               # 1
    "doodle",               # 2 (낙서/손글씨/필기/기호)
    "COVER_SCRATCH",        # 3
    "COVER_TEAR",           # 4
    "COVER_STICKER",        # 5
    "EDGE_CORNER_DAMAGE",   # 6
    "EDGE_WEAR",            # 7
    "STAIN_DIRT",           # 8
    "STAIN_FADING",         # 9
    "STAIN_WATER_DAMAGE",   # 10
    "PAGE_WARPING",         # 11
    "BINDING_LOOSE"         # 12
]

def update_unified_data_yaml():
    yaml_path = unified_dataset_dir / "data.yaml"
    
    yaml_lines = [
        f'train: "{unified_dataset_dir / "train" / "images"}"',
        f'val: "{unified_dataset_dir / "valid" / "images"}"',
        f'test: "{unified_dataset_dir / "test" / "images"}"',
        f'nc: {len(WMS_13_CLASSES)}',
        'names:'
    ]
    for idx, cname in enumerate(WMS_13_CLASSES):
        yaml_lines.append(f'  {idx}: {cname}')

    with open(yaml_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(yaml_lines) + "\n")
        
    print(f"[SUCCESS] Updated {yaml_path} with 13 WMS Standard Classes!")

if __name__ == "__main__":
    update_unified_data_yaml()
