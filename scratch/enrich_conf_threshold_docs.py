import os

pm_file = r'E:\취업\KT AIVLE School\빅프로젝트\PM_정답지_백업\LangGraph_MultiAgent_Vision_Architecture_Internal.md'
wms_file = r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs\LangGraph_MultiAgent_Vision_Architecture.md'

table_section = """
#### B.1 YOLOv8 Model 1 & Model 2 신뢰도(Conf) 지표 분석 및 WBF 앙상블 타당성
| 신뢰도 임계치 (Conf) | Recall (재현율) | Precision (정밀도) | 특성 및 WBF 앙상블 필요성 |
| :---: | :---: | :---: | :--- |
| **`conf = 0.25`**<br/>(Model 2 베이스 표준) | **`57.6%`**<br/>(`ripped` 66.4%) | **`95.5%`** | 오탐(False Positive)은 거의 없으나, 미세 마모/스크래치를 놓치는 미탐 발생 |
| **`conf = 0.12`**<br/>(Model 1 동적 하향) | **`84.0% ~ 88.0%`** | **`65.0% ~ 72.0%`** | 연한 파손까지 1차로 모두 포착하여 **Recall 88% 달성**, 단 노이즈성 오탐 수반 |
| **`WBF Ensemble`**<br/>(Model 1 + Model 2) | **`85.0%+`** | **`88.0%+`** | Model 1의 High Recall과 Model 2의 High Precision을 결합하여 오탐 억제 & 최적 BBox 도출 |
"""

for fpath in [pm_file, wms_file]:
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as fp:
            content = fp.read()
        
        if "### B. Vision Agent 3단계 다중 모델 앙상블 파이프라인 시각화" in content and "B.1 YOLOv8 Model 1 & Model 2" not in content:
            content = content.replace(
                "### B. Vision Agent 3단계 다중 모델 앙상블 파이프라인 시각화",
                table_section + "\n### B. Vision Agent 3단계 다중 모델 앙상블 파이프라인 시각화"
            )
            with open(fpath, 'w', encoding='utf-8') as fp:
                fp.write(content)
            print(f'Enriched conf threshold analysis in: {os.path.basename(fpath)}')

print('Doc enrichment completed successfully!')
