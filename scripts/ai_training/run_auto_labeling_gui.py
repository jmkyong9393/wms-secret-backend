"""
====================================================================
[WMS YOLOv8 Auto-Labeling GUI Tool]
- 윈도우 폴더 선택창(GUI)을 통해 사진 폴더를 클릭 한 번으로 선택하여 
  자동 라벨링(.txt)을 가동하는 마우스 클릭형 도구입니다.
====================================================================
"""

import sys
import os
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from ultralytics import YOLO

CLASSES = ["ripped", "Wornout"]

def run_gui():
    root = tk.Tk()
    root.withdraw() # 메인 창 숨기기

    # 폴더 선택 대화상자 띄우기
    target_dir = filedialog.askdirectory(title="자동 라벨링을 진행할 도서 사진 폴더를 선택하세요")
    
    if not target_dir:
        print("[Notice] 폴더 선택이 취소되었습니다.")
        return

    model_path = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend\app\ai\yolov8_high_recall_best.pt')
    if not model_path.exists():
        messagebox.showerror("오류", f"YOLOv8 모델 가중치를 찾을 수 없습니다:\n{model_path}")
        return

    # classes.txt 생성
    classes_file = Path(target_dir) / "classes.txt"
    with open(classes_file, "w", encoding="utf-8") as f:
        f.write("\n".join(CLASSES) + "\n")

    model = YOLO(str(model_path))
    image_files = list(Path(target_dir).glob("*.jpg")) + list(Path(target_dir).glob("*.jpeg")) + list(Path(target_dir).glob("*.png"))

    if not image_files:
        messagebox.showwarning("경고", "선택한 폴더에 이미지 파일(.jpg, .jpeg, .png)이 없습니다.")
        return

    count = 0
    for img_p in image_files:
        results = model.predict(source=str(img_p), conf=0.12, verbose=False)
        txt_p = Path(target_dir) / f"{img_p.stem}.txt"
        
        lines = []
        for r in results:
            w_img, h_img = r.orig_shape[1], r.orig_shape[0]
            for box in r.boxes:
                cls_id = int(box.cls[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                x_center = ((x1 + x2) / 2.0) / w_img
                y_center = ((y1 + y2) / 2.0) / h_img
                width = (x2 - x1) / w_img
                height = (y2 - y1) / h_img
                lines.append(f"{cls_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}")
        
        with open(txt_p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        count += 1

    messagebox.showinfo("성공", f"총 {count}개 이미지에 대해 자동 라벨링이 완료되었습니다!\n\nlabelImg에서 '{target_dir}' 폴더를 열어 확인하세요.")

if __name__ == "__main__":
    run_gui()
