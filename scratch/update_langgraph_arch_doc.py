"""
====================================================================
[LangGraph_MultiAgent_Vision_Architecture_Internal.md 누적 최신화 및 동기화 스크립트]
1. archive 백업 (2026-07-27 타임스탬프)
2. ver 2.4.0.0 2-Stage 비전 아키텍처 및 결정론적 YOLOv8 수순 누적 반영
3. Mermaid 아키텍처 다이어그램 동기화
4. PM_정답지_백업 -> WMS_docs 이중 배포 동기화
====================================================================
"""

import os
import shutil
from pathlib import Path

pm_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업')
wms_dir = Path(r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs')
arc_dir = pm_dir / 'archive'

doc_filename = 'LangGraph_MultiAgent_Vision_Architecture_Internal.md'
src_p = pm_dir / doc_filename
arc_p = arc_dir / f"2026-07-27_{doc_filename.replace('.md', '')}_ver2.3.0.0.md"

def update_langgraph_doc():
    print(f"[LangGraph Arch Update] Starting update for {doc_filename}...")

    # 1. Archive 백업
    if src_p.exists():
        shutil.copy2(src_p, arc_p)
        print(f"  [Archive] Backed up -> {arc_p.name}")

    with open(src_p, 'r', encoding='utf-8', errors='ignore') as f:
        orig_text = f.read()

    # ver 2.4.0.0 누적 섹션 작성
    v24_section = """

---

## 8. v2.4.0.0 2-Stage 비전 아키텍처 및 결정론적 YOLOv8 훈련 파이프라인 (2026-07-27 누적)

### A. 결정론적(Deterministic) 비전 아키텍처 원칙 확정
- **VLM 과도 의존 탈피**: 결함 분류를 VLM의 주관적 자연어 해석에만 위임하는 아키텍처는 VLM 환각(Hallucination) 및 프롬프트 온도에 따른 판단 불일치 위험이 상존함.
- **YOLOv8 픽셀 직접 추론**: YOLOv8m 모델이 `Wornout`(마모)과 `ripped`(파손) 2대 물리 결함을 픽셀 텐서 수준에서 **직접 분류 및 BBox 추론**하게 하여 100% 수치적 결정론(Determinism)과 `6.5ms` 초고속 추론을 보장.

### B. 2-Stage 비전 및 멀티에이전트 이중 검증 파이프라인 Mermaid 다이어그램

```mermaid
flowchart TD
    A["입고 도서 이미지 (800x800)"] --> B["Stage 1: YOLOv8m 직접 클래스 추론 Engine"]
    
    B -->|Class 0: 2,414 BBoxes| C1["Wornout (모서리 마모/구겨짐)"]
    B -->|Class 1: 1,908 BBoxes| C2["ripped (표지/내지 파손 및 찢김)"]
    
    C1 & C2 --> D["BBox 픽셀 좌표 & 면적 비율 수치 계산기"]
    
    A --> E["Stage 2: Model 3 GPT-4o Vision (VLM)"]
    E -->|"보조 시각 검증 & 특이사항 요약"| F["special_notes (AI 한줄 요약 생성)"]
    
    D & F --> G["LangGraph Vision Agent Node"]
    G --> H["Policy Agent: ChromaDB RAG 규정 조회 ➔ UBCI 100점 점수 계산"]
    
    H --> I{"최종 상태 판정"}
    I -->|UBCI 90~100점| J1["MINT (최상급)"]
    I -->|UBCI 80~89점| J2["GOOD (상급)"]
    I -->|UBCI 60~79점| J3["NORMAL (중급)"]
    I -->|UBCI 60점 미만 / 중대 결함| J4["REJECT (반품/입고 거부)"]
```

### C. 데이터셋 무결성 및 하드웨어 최적화 세팅
* **`stage1_book_defect_dataset`**: 총 1,037장 (Train 706 / Valid 229 / Test 102)
* **BBox 밸런스**: `Wornout` 2,414개 : `ripped` 1,908개 (Train Set 전용 Multi-BBox CopyPaste 훈련 적용, Valid Set 성역화)
* **RTX 2070 Super (8GB VRAM) 최적화**: `imgsz=800`, `batch=8`, `workers=2`, `epochs=300`
"""

    full_updated_text = orig_text.replace("ver 2.3.0.0 (Milestone Upgrade)", "ver 2.4.0.0 (Milestone Upgrade)") + v24_section

    with open(src_p, 'w', encoding='utf-8') as f:
        f.write(full_updated_text)

    # 4. WMS_docs 배포용 폴더로 자동 동기화 (Copy)
    dst_wms_p = wms_dir / 'LangGraph_MultiAgent_Vision_Architecture.md'
    shutil.copy2(src_p, dst_wms_p)
    print(f"  [WMS_docs Sync] Copied {doc_filename} -> {dst_wms_p.name}")

    print(f"\n=======================================================")
    print(f"[SUCCESS] LangGraph Architecture Document Updated & Synced!")
    print(f"=======================================================")

if __name__ == "__main__":
    update_langgraph_doc()
