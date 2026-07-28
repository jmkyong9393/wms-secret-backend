import os
import shutil
from pathlib import Path
import cv2
from ultralytics import YOLO

# 1. 원본 이미지 경로 및 가중치 경로
img_path = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-ai-training\Damaged Books.v3i.yolov8\valid\images\IMG_1003_jpg.rf.a25dd3cfc3fe8321f597737f1299ade8.jpg')
model1_path = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend\app\ai\yolov8_high_recall_best.pt')

# 2. 아티팩트 저장소 경로
artifact_dir = Path(r'C:\Users\jmkyo\.gemini\antigravity\brain\e3581def-d658-43e3-94b1-c67850b88493')
os.makedirs(artifact_dir, exist_ok=True)

out_orig_path = artifact_dir / 'test_sample_img1003_orig.jpg'
out_annotated_path = artifact_dir / 'test_sample_img1003_annotated.jpg'

if img_path.exists():
    # 원본 이미지 복사
    shutil.copy2(img_path, out_orig_path)
    print(f'Original image copied to: {out_orig_path}')

    # OpenCV 바운딩 박스 그리기
    img = cv2.imread(str(img_path))
    
    if model1_path.exists():
        model = YOLO(str(model1_path))
        results = model.predict(source=str(img_path), conf=0.12, verbose=False)
        
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                conf = float(box.conf[0])
                cls_name = r.names[int(box.cls[0])]
                
                # 라벨 및 BBox 그리기 (초록색 상자, 빨간색 텍스트)
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                label = f"{cls_name} ({conf:.2f})"
                cv2.putText(img, label, (x1, max(y1 - 10, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        
        cv2.imwrite(str(out_annotated_path), img)
        print(f'Annotated image saved to: {out_annotated_path}')

print('Visualization script completed!')
