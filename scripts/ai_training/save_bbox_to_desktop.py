import shutil
from pathlib import Path

source_img = Path(r'C:\Users\jmkyo\.gemini\antigravity\brain\e3581def-d658-43e3-94b1-c67850b88493\raw3_annotated.jpg')

# 1. 바탕화면 저장 경로
desktop_dst = Path(r'C:\Users\jmkyo\Desktop\raw3_bbox_result.jpg')

# 2. 백엔드 실험 데이터 폴더 저장 경로
job_dst = Path(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend\app\experiment_data\job-f309b042\raw_3_bbox_result.jpg')

if source_img.exists():
    shutil.copy2(source_img, desktop_dst)
    print(f"Copied BBox image to Desktop: {desktop_dst}")

    shutil.copy2(source_img, job_dst)
    print(f"Copied BBox image to Job Folder: {job_dst}")
else:
    print(f"Source image not found: {source_img}")
