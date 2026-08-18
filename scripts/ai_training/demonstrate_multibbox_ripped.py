"""
====================================================================
[Multi-BBox CopyPaste Class 1 'ripped' Demonstration Script]
- 도서 1장에 존재하는 모든 모서리/에지 결함 BBox들을 100% 포착하고,
  추가 CopyPaste 패치까지 다중 BBox(Multi-BBox)로 완벽 렌더링합니다.
====================================================================
"""

import os
from pathlib import Path
import cv2
import numpy as np

dataset_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\unified_book_defect_dataset')
desktop_dst = Path(r'C:\Users\jmkyo\Desktop\copypaste_ripped_multibbox_sample.jpg')

def run_multibbox_demo():
    # 스크린샷의 '베르나르 베르베르 뇌' 도서 이미지 찾기
    target_stem = "ds1_IMG_1738_jpg.rf.c146f4462e61bae99ce6d41f424cce44"
    
    target_img_p = dataset_dir / "train" / "images" / f"{target_stem}.jpg"
    target_lbl_p = dataset_dir / "train" / "labels" / f"{target_stem}.txt"

    if not target_img_p.exists():
        print(f"[Error] Target image not found: {target_img_p}")
        return

    # 이미지 로딩
    img = cv2.imdecode(np.fromfile(str(target_img_p), np.uint8), cv2.IMREAD_COLOR)
    h_img, w_img = img.shape[:2]

    # 원본 BBox 목록 읽기
    bboxes = []
    if target_lbl_p.exists():
        with open(target_lbl_p, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 5:
                    cls_id = int(parts[0])
                    xc, yc, bw, bh = [float(x) for x in parts[1:5]]
                    bboxes.append((cls_id, xc, yc, bw, bh, "Original GT"))

    # 추가 CopyPaste 패치 (상단 모서리 찢김 부근)
    # 소스 ripped 패치 가져오기 (IMG_1775)
    src_stem = "ds1_IMG_1775_jpg.rf.2ee25e0e5195740e113830ec9bcc7bc3"
    src_img_p = dataset_dir / "train" / "images" / f"{src_stem}.jpg"
    src_lbl_p = dataset_dir / "train" / "labels" / f"{src_stem}.txt"

    if src_img_p.exists() and src_lbl_p.exists():
        src_img = cv2.imdecode(np.fromfile(str(src_img_p), np.uint8), cv2.IMREAD_COLOR)
        h_src, w_src = src_img.shape[:2]
        
        with open(src_lbl_p, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split()
                if parts and parts[0] == '1': # ripped
                    xc, yc, bw, bh = [float(x) for x in parts[1:5]]
                    px1 = max(0, int((xc - bw / 2.0) * w_src))
                    py1 = max(0, int((yc - bh / 2.0) * h_src))
                    px2 = min(w_src, int((xc + bw / 2.0) * w_src))
                    py2 = min(h_src, int((yc + bh / 2.0) * h_src))
                    
                    patch = src_img[py1:py2, px1:px2]
                    pw, ph = patch.shape[1], patch.shape[0]

                    # 우상단 모서리 근처 합성
                    tar_x1, tar_y1 = int(w_img * 0.70), int(h_img * 0.05)
                    tar_x2, tar_y2 = tar_x1 + pw, tar_y1 + ph
                    
                    img[tar_y1:tar_y2, tar_x1:tar_x2] = patch

                    # BBox 등록
                    n_xc = ((tar_x1 + tar_x2) / 2.0) / w_img
                    n_yc = ((tar_y1 + tar_y2) / 2.0) / h_img
                    n_bw = pw / w_img
                    n_bh = ph / h_img
                    bboxes.append((1, n_xc, n_yc, n_bw, n_bh, "CopyPaste Augmented"))
                    break

    # 이미지에 모든 다중 BBox 렌더링
    CLASSES_MAP = {0: "Wornout", 1: "ripped"}
    annotated = img.copy()

    for cls_id, xc, yc, bw, bh, tag in bboxes:
        x1 = int((xc - bw / 2.0) * w_img)
        y1 = int((yc - bh / 2.0) * h_img)
        x2 = int((xc + bw / 2.0) * w_img)
        y2 = int((yc + bh / 2.0) * h_img)

        cls_name = CLASSES_MAP.get(cls_id, f"Class {cls_id}")
        color = (0, 0, 255) if cls_id == 1 else (0, 255, 0) # Red for ripped, Green for Wornout

        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 3)
        label_str = f"{cls_name} ({tag})"
        (tw, th), _ = cv2.getTextSize(label_str, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.rectangle(annotated, (x1, max(y1 - 22, 0)), (x1 + tw + 10, max(y1, 22)), color, -1)
        cv2.putText(annotated, label_str, (x1 + 5, max(y1 - 6, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # 바탕화면 저장
    is_success, im_buf = cv2.imencode(".jpg", annotated)
    if is_success:
        im_buf.tofile(str(desktop_dst))
        print(f"\n=======================================================")
        print(f"[SUCCESS] Multi-BBox CopyPaste Sample Image Saved!")
        print(f"  - Target Book File: {target_img_p.name}")
        print(f"  - Total Multi-BBoxes Rendered: {len(bboxes)}")
        print(f"  - Saved Desktop Image: {desktop_dst}")
        print(f"=======================================================")

if __name__ == "__main__":
    run_multibbox_demo()
