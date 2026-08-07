import os

pm_file = r'E:\취업\KT AIVLE School\빅프로젝트\개인개발가이드\labelImg_Auto_Labeling_Guide.md'
wms_file = r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs\labelImg_Auto_Labeling_Guide.md'

doc_content = """# labelImg 연동 YOLOv8 반자동 라벨링(Pre-Annotation) 파이프라인 가이드
**[보안 등급: 팀원 배포용]**  
**작성일자**: 2026-07-24  
**버전**: ver 1.0.0.0  
**작성자**: 장문경 (Lead Architect & Project Owner)

---

## 1. 개요 (Overview)

본 가이드는 WMS 도서 물류 AI 검수 플랫폼의 YOLOv8 비전 모델 재학습 및 검증 데이터 구축을 위해, **`labelImg` GUI 라벨링 툴**과 **YOLOv8 자동 추론 엔진(`auto_labeling_with_yolo.py`)**을 연동하는 **반자동 라벨링(Pre-Annotation & Fast Audit) 워크플로우**를 설명합니다.

100% 수동 라벨링 방식 대비 작업 시간을 **85% 이상 단축**하며, Human-in-the-Loop(HITL) 검수자 및 작업자가 고속으로 BBox 바운딩 박스를 검증 및 수정할 수 있습니다.

---

## 2. 사전 환경 세팅 (Prerequisite Configuration)

### 2.1 labelImg 프로그램 위치
* **실행 경로**: `C:\\Users\\jmkyo\\Downloads\\windows_v1.8.0\\labelImg.exe`

### 2.2 사전 클래스 정의 파일 세팅 (`predefined_classes.txt`)
`labelImg` 가동 시 클래스명을 매번 수동 입력하지 않도록 미리 세팅이 완료되어 있습니다.

* **세팅 위치**: `C:\\Users\\jmkyo\\Downloads\\windows_v1.8.0\\data\\predefined_classes.txt`
* **설정된 도서 결함 규격 클래스**:
  ```text
  ripped
  Wornout
  COVER_SCRATCH
  COVER_TEAR
  COVER_STICKER
  EDGE_CORNER_DAMAGE
  EDGE_WEAR
  STAIN_DIRT
  STAIN_FADING
  STAIN_WATER_DAMAGE
  PAGE_WARPING
  BINDING_LOOSE
  ```

---

## 3. 반자동 라벨링 3단계 가동 수순 (Workflow)

```mermaid
flowchart LR
    A["미라벨링 도서 사진 폴더"] --> B["[Step 1]<br/>auto_labeling_with_yolo.py 가동<br/>(YOLOv8 conf=0.12 추론)"]
    B --> C["[Step 2]<br/>YOLO 라벨(.txt) 자동 생성"]
    C --> D["[Step 3]<br/>labelImg.exe 가동<br/>(Open Dir -> YOLO 포맷 선택)"]
    D --> E["고속 마우스 1초 수정 & Ctrl+S 저장"]
```

### Step 1. YOLOv8 자동 라벨링 스크립트 가동
원하시는 사진 폴더에 대해 백엔드 가중치(`yolov8_high_recall_best.pt`)를 사용해 1초 만에 YOLO 좌표 파일(`.txt`)을 자동으로 생성합니다.

```cmd
cd "E:\\취업\\KT AIVLE School\\빅프로젝트\\develop\\solo_develop\\wms-secret-backend"
.venv\\Scripts\\python.exe scratch/auto_labeling_with_yolo.py "사진이담긴폴더경로"
```

* **동작 상세**: 
  - 이미지 해상도 대비 BBox 픽셀 좌표를 YOLO 규격화 좌표(`class_id x_center y_center width height`)로 자동 변환하여 정합 저장.
  - 해당 폴더 내 `classes.txt` 자동 생성.

---

### Step 2. labelImg 실행 및 설정
1. `C:\\Users\\jmkyo\\Downloads\\windows_v1.8.0\\labelImg.exe` 프로그램을 더블클릭하여 실행합니다.
2. 좌측 패널의 **`Open Dir`** 버튼을 클릭하여 사진이 있는 폴더를 선택합니다.
3. 좌측 패널의 **`Change Save Dir`** 버튼을 클릭하여 라벨 파일이 저장될 폴더를 동일하게 지정합니다.
4. 좌측 저장 형식 버튼을 클릭하여 **`PascalVOC` ➔ `YOLO`** 포맷으로 반드시 전환합니다.

---

### Step 3. 1초 고속 검수 및 수정
- 이미지를 이동하면 **YOLOv8이 1차로 자동 추출한 초록색 BBox 상자들이 100% 렌더링**됩니다.
- 미세 조정이 필요한 상자는 마우스로 클릭 후 모서리를 끌어 1초 만에 크기 조절합니다.
- 추가 결함이 있는 경우 키보드 **`W`** 키를 눌러 상자를 새로 만듭니다.
- 검수 완료 후 **`Ctrl + S`** 키를 눌러 라벨을 저장합니다.

---

## 4. labelImg 필수 단축키 치트시트 (Keyboard Shortcuts)

| 단축키 | 기능 설명 | 활용 팁 |
| :---: | :--- | :--- |
| **`W`** | **Create RectBox** (새 상자 그리기) | 추가 결함 영역 지정 시 사용 |
| **`D`** | **Next Image** (다음 사진 이동) | 검수 완료 후 다음 사진으로 즉시 이동 |
| **`A`** | **Prev Image** (이전 사진 이동) | 재확인 필요 시 이전 사진으로 이동 |
| **`Del`** | **Delete RectBox** (상자 삭제) | 오탐(False Positive) 상자 삭제 시 사용 |
| **`Ctrl + S`** | **Save** (라벨 저장) | 라벨 변경 사항 즉시 저장 |
| **`Ctrl + r`** | **Change Save Dir** | 저장 폴더 변경 |

---

## 5. 기대 효과 및 MLOps 연속성

1. **라벨링 속도 10배 향상**: 제로베이스 수동 바운딩 박스 생성 대비 85% 이상 시간 단축.
2. **YOLO 재학습 데이터셋 즉시 변환**: 생성 및 보정된 `.txt` 파일은 그대로 Roboflow 또는 Ultralytics Dataset 구조(`images/`, `labels/`)로 직행하여 MLOps 파이프라인에 피드백 환류 가능.
"""

with open(pm_file, 'w', encoding='utf-8') as f:
    f.write(doc_content)
print(f'Created PM master doc: {pm_file}')

with open(wms_file, 'w', encoding='utf-8') as f:
    f.write(doc_content)
print(f'Created WMS public doc: {wms_file}')
