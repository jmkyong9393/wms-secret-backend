"""
====================================================================
[ver 2.6.0.0 마스터 문서 동기화 & 백업 스크립트]
1. PM_정답지_백업 내 4대 마스터 문서 ver 2.6.0.0 업데이트:
   - wms-ai-training/runs/ 경로 일원화 내역 반영
   - stage2_doodle_ocr_dataset (10,000장 손글씨 OCR 데이터셋) 명세 반영
   - yolov8_doodle_ocr.pt 독립 모델 연동 명세 반영
2. archive 폴더에 2026-07-27_..._ver2.5.0.0.md 백업 보존
3. WMS_docs (팀원 배포용) 이원화 자동 복사 동기화
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')
archive_dir = pm_dir / 'archive'
archive_dir.mkdir(exist_ok=True)

# 1. 원본 파일 백업 (ver2.5.0.0)
doc_files = [
    'YOLO_Model_History_Internal.md',
    'YOLOv8_Recall_Optimization_Report.md',
    'YOLOv8_모델_학습결과_분석보고서.md',
    'LangGraph_MultiAgent_Vision_Architecture_Internal.md'
]

for doc_name in doc_files:
    src_p = pm_dir / doc_name
    if src_p.exists():
        stem = src_p.stem.replace('_Internal', '')
        bak_name = f"2026-07-27_{stem}_ver2.5.0.0.md"
        shutil.copy2(src_p, archive_dir / bak_name)
        print(f"[Backup] {doc_name} -> archive/{bak_name}")

# 2. ver 2.6.0.0 갱신 내용 세그먼트
v26_yolo_section = """
---

## 8. [v2.6.0.0] AIHub 손글씨/낙서 OCR 독립 전담 모델 구축 및 워크스페이스 경로 통합 (2026-07-27)

### 8.1 아키텍처 결정 및 경로 일원화
* **배경**: 백엔드 소스코드 디렉터리(`wms-secret-backend`)의 경량화 및 AI 데이터/학습 산출물 관리를 단일화하기 위해 모든 YOLO 학습 이력 및 데이터셋을 `wms-ai-training`으로 통합 조치함.
* **통합 경로 명세**:
  * **전담 AI 데이터셋**: `E:\\취업\\KT AIVLE School\\빅프로젝트\\develop\\solo_develop\\wms-ai-training\\stage2_doodle_ocr_dataset\\` (AIHub 손글씨/낙서 10,000장 정제)
  * **통합 훈련 결과물**: `E:\\취업\\KT AIVLE School\\빅프로젝트\\develop\\solo_develop\\wms-ai-training\\runs\\`
    * `stage1_yolov8m_200e\\`: 1차 도서 외관 물리 결함 이력
    * `doodle_scribble_yolov8m_run1\\`: 신규 손글씨/낙서 전담 모델 훈련 이력

### 8.2 독립 낙서 전담 모델 (`yolov8_doodle_ocr.pt`) 최적화 스펙
* **데이터 구성**: AIHub 중·고등학생 실물 손글씨/수식 10,000장 (Train 7,000 / Valid 2,000 / Test 1,000)
* **하이퍼파라미터 최적화**:
  * `imgsz=640`, `rect=True` (손글씨 글자 종횡비 100% 보존 직사각형 학습)
  * `mosaic=0.0` (문장 싹둑 잘림 100% 방지)
  * `fliplr=0.0` (거울 반전 문자 노이즈 100% 방지)
  * `epochs=200`, `patience=30` (Early Stopping)
"""

v26_langgraph_section = """
---

## 7. [v2.6.0.0] 백엔드 WBF 앙상블 & 독립 낙서 탐지기 Multi-Model 서빙 명세 (2026-07-27)

### 7.1 Multi-Model Specialist Ensemble 서빙 아키텍처
* **Model 1 & 2 (도서 외관 결함 WBF 융합)**: `yolov8_high_recall_best.pt` + `yolov8_high_precision_base.pt` ➔ 픽셀 BBox WBF 가중 평균 융합
* **Model 3 (독립 낙서/필기 전담)**: `yolov8_doodle_ocr.pt` (`wms-ai-training/runs/doodle_scribble_yolov8m_run1/weights/best.pt`) ➔ 손글씨, 연필/볼펜 낙서, 밑줄 100% 전담 캡처
* **백엔드 통합 추론 파이프라인**: `app/ai/wbf_detector.py` ➔ `app/ai/vision_agent.py` ➔ GPT-4o Vision Prompt 주입
"""

# 문서 갱신 함수
def append_or_update(filepath, new_section, version_str="v2.6.0.0"):
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    if version_str not in content:
        content += f"\n{new_section}\n"
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"[Updated] {filepath.name} with {version_str}")
    else:
        print(f"[Already Updated] {filepath.name} has {version_str}")

# PM_정답지_백업 문서 업데이트
append_or_update(pm_dir / 'YOLO_Model_History_Internal.md', v26_yolo_section)
append_or_update(pm_dir / 'YOLOv8_Recall_Optimization_Report.md', v26_yolo_section)
append_or_update(pm_dir / 'YOLOv8_모델_학습결과_분석보고서.md', v26_yolo_section)
append_or_update(pm_dir / 'LangGraph_MultiAgent_Vision_Architecture_Internal.md', v26_langgraph_section)

# WMS_docs 이원화 배포 동기화 (기밀 필터링 및 대외 배포용 복사)
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History_Internal.md')
shutil.copy2(pm_dir / 'YOLO_Model_History_Internal.md', wms_dir / 'YOLO_Model_History.md')
shutil.copy2(pm_dir / 'YOLOv8_Recall_Optimization_Report.md', wms_dir / 'YOLOv8_Recall_Optimization_Report.md')
shutil.copy2(pm_dir / 'YOLOv8_모델_학습결과_분석보고서.md', wms_dir / 'YOLOv8_모델_학습결과_분석보고서.md')
shutil.copy2(pm_dir / 'LangGraph_MultiAgent_Vision_Architecture_Internal.md', wms_dir / 'LangGraph_MultiAgent_Vision_Architecture.md')

print("[SUCCESS] All 4 Master Docs updated to v2.6.0.0 and synced to WMS_docs!")
