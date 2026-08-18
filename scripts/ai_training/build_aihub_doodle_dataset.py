"""
====================================================================
[AIHub 손글씨/낙서 OCR 데이터셋 1만장(10,000 Samples) 대용량 추출 스크립트]
- F:\수식, 도형, 낙서기호 OCR 데이터셋_aihub
  * TS_손중1.zip ~ TS_손중3.zip (중학생 5,000장)
  * TS_손고1.zip ~ TS_손고3.zip (고등학생 5,000장)
- 총 10,000장의 초고품질 손글씨/낙서/수식 실물 데이터 추출
- YOLOv8 포맷 (class=0: doodle_scribble) 변환 및 train/val/test (7,000 : 2,000 : 1,000) 분할
====================================================================
"""

import os
import json
import zipfile
import random
import cv2
import numpy as np
from pathlib import Path

# AIHub 데이터셋 압축파일 경로
aihub_base = Path(r'F:\수식, 도형, 낙서기호 OCR 데이터셋_aihub\038.수식, 도형, 낙서기호 OCR 데이터\01.데이터\1.Training')
src_dir = aihub_base / '원천데이터'
lbl_dir = aihub_base / '라벨링데이터'

# 출력 데이터셋 경로
dst_dataset_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\stage2_doodle_ocr_dataset')

def process_aihub_zip_list(pairs, target_count=5000):
    extracted_items = []
    per_pair_target = int(np.ceil(target_count / len(pairs)))
    
    for ts_path, tl_path in pairs:
        if len(extracted_items) >= target_count:
            break
        if not (ts_path.exists() and tl_path.exists()):
            continue
            
        print(f"[AIHub Extracting] {ts_path.name} & {tl_path.name}...")
        
        with zipfile.ZipFile(tl_path, 'r') as z_tl:
            tl_files = [f for f in z_tl.namelist() if f.endswith('.json')]
            sample_candidates = random.sample(tl_files, min(per_pair_target * 2, len(tl_files)))
            
            with zipfile.ZipFile(ts_path, 'r') as z_ts:
                ts_file_set = set(z_ts.namelist())
                
                count_for_this_pair = 0
                for jf in sample_candidates:
                    if len(extracted_items) >= target_count or count_for_this_pair >= per_pair_target:
                        break
                    try:
                        json_bytes = z_tl.read(jf)
                        label_data = json.loads(json_bytes.decode('utf-8', errors='ignore'))
                        
                        segments = label_data.get('segments', [])
                        if not segments:
                            continue
                        
                        base_name = Path(jf).stem
                        parent_path = Path(jf).parent.as_posix()
                        
                        possible_img_names = [
                            f"{parent_path}/{base_name}.jpg",
                            f"{parent_path}/{base_name}.png",
                            f"{base_name}.jpg",
                            f"{base_name}.png"
                        ]
                        
                        matched_img = None
                        for img_cand in possible_img_names:
                            if img_cand in ts_file_set:
                                matched_img = img_cand
                                break
                        
                        if not matched_img:
                            for ts_f in ts_file_set:
                                if base_name in ts_f and (ts_f.endswith('.jpg') or ts_f.endswith('.png')):
                                    matched_img = ts_f
                                    break

                        if matched_img:
                            img_bytes = z_ts.read(matched_img)
                            nparr = np.frombuffer(img_bytes, np.uint8)
                            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                            if img is None:
                                continue
                                
                            h, w, _ = img.shape
                            
                            yolo_bboxes = []
                            for seg in segments:
                                pts = seg.get('box', [])
                                if len(pts) < 3:
                                    continue
                                
                                xs = [pt[0] for pt in pts]
                                ys = [pt[1] for pt in pts]
                                
                                xmin, xmax = max(0, min(xs)), min(w, max(xs))
                                ymin, ymax = max(0, min(ys)), min(h, max(ys))
                                
                                bw = xmax - xmin
                                bh = ymax - ymin
                                
                                if bw < 5 or bh < 5:
                                    continue
                                    
                                x_center = (xmin + xmax) / (2.0 * w)
                                y_center = (ymin + ymax) / (2.0 * h)
                                norm_w = bw / float(w)
                                norm_h = bh / float(h)
                                
                                # Class ID 0: doodle_scribble
                                yolo_bboxes.append(f"0 {x_center:.6f} {y_center:.6f} {norm_w:.6f} {norm_h:.6f}")

                            if yolo_bboxes:
                                extracted_items.append({
                                    'image_name': f"{base_name}.jpg",
                                    'image_data': img,
                                    'bboxes': yolo_bboxes
                                })
                                count_for_this_pair += 1

                    except Exception as e:
                        continue

        print(f"  - Pair Extracted: {count_for_this_pair} samples (Total so far: {len(extracted_items)})")

    return extracted_items

def main():
    random.seed(42)
    print(f"=======================================================")
    print(f"[Build 10,000 Dataset] Target Directory: {dst_dataset_dir}")
    print(f"=======================================================")
    
    # 기존 폴더 완전 초기화 (구 버전 잔재 파일 삭제)
    import shutil
    if dst_dataset_dir.exists():
        shutil.rmtree(dst_dataset_dir)
        
    for split in ['train', 'valid', 'test']:
        (dst_dataset_dir / split / 'images').mkdir(parents=True, exist_ok=True)
        (dst_dataset_dir / split / 'labels').mkdir(parents=True, exist_ok=True)

    # 1. 중학생 데이터 5,000장 대상
    jung_pairs = [
        (src_dir / f'TS_손중{i}.zip', lbl_dir / f'TL_손중{i}.zip') for i in [1, 2, 3]
    ]
    items_jung = process_aihub_zip_list(jung_pairs, target_count=5000)

    # 2. 고등학생 데이터 5,000장 대상
    go_pairs = [
        (src_dir / f'TS_손고{i}.zip', lbl_dir / f'TL_손고{i}.zip') for i in [1, 2, 3]
    ]
    items_go = process_aihub_zip_list(go_pairs, target_count=5000)
    
    all_items = items_jung + items_go
    random.shuffle(all_items)
    total_count = len(all_items)
    
    print(f"\n[Split Dataset] Total extracted samples: {total_count}")
    
    n_train = int(total_count * 0.7)
    n_val = int(total_count * 0.2)
    
    train_items = all_items[:n_train]
    val_items = all_items[n_train:n_train + n_val]
    test_items = all_items[n_train + n_val:]
    
    splits = [('train', train_items), ('valid', val_items), ('test', test_items)]
    
    print(f"[Saving Images & Labels] Writing files to disk...")
    for split_name, items in splits:
        img_dir = dst_dataset_dir / split_name / 'images'
        lbl_dir_target = dst_dataset_dir / split_name / 'labels'
        
        for idx, item in enumerate(items):
            fn = f"aihub_doodle_{split_name}_{idx:05d}.jpg"
            lbl_fn = f"aihub_doodle_{split_name}_{idx:05d}.txt"
            
            img_target_p = str(img_dir / fn)
            is_success, buffer = cv2.imencode('.jpg', item['image_data'])
            if is_success:
                buffer.tofile(img_target_p)
                
            with open(lbl_dir_target / lbl_fn, 'w', encoding='utf-8') as f:
                f.write("\n".join(item['bboxes']))

    # 3. data.yaml 생성
    yaml_p = dst_dataset_dir / "data.yaml"
    yaml_content = f"""path: {dst_dataset_dir.as_posix()}
train: train/images
val: valid/images
test: test/images
nc: 1
names:
  0: doodle_scribble
"""
    with open(yaml_p, 'w', encoding='utf-8') as f:
        f.write(yaml_content)

    print(f"\n=======================================================")
    print(f"[SUCCESS] AIHub 10,000 Doodle Dataset Built Successfully!")
    print(f" - Train: {len(train_items)} images")
    print(f" - Valid: {len(val_items)} images")
    print(f" - Test: {len(test_items)} images")
    print(f" - Config: {yaml_p}")
    print(f"=======================================================")

if __name__ == "__main__":
    main()
