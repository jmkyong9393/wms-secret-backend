# vision_model_docs — 산출 모델과 실험 조건

이 폴더의 그래프·행렬·배치 이미지는 **`train_unified_v6_recall` 학습 런**의 Ultralytics 출력물입니다.
어느 모델의 결과인지 파일만 봐서는 알 수 없어 여기에 조건을 남깁니다.

## 학습 조건 (`args.yaml` 원문 기준)

| 항목 | 값 |
| --- | --- |
| 베이스 가중치 | `yolov8m.pt` |
| 런 이름 | `train_unified_v6_recall` |
| 데이터셋 | `wms-ai-training/unified_book_defect_dataset/data.yaml` |
| 에포크 | 300 (patience 50) |
| 이미지 크기 | 640 · 배치 8 |
| 옵티마이저 | AdamW · cos_lr 미사용 · close_mosaic 10 |
| 재현성 | seed 0 · deterministic true |
| 장치 | GPU 0 · AMP 사용 |

## 최종 성능 (`yolo_classification_report.csv`)

| 클래스 | 이미지 | 인스턴스 | Precision | Recall | F1 | mAP50 | mAP50-95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Wornout (마모/훼손) | 175 | 505 | 0.814 | 0.455 | 0.584 | 0.490 | 0.339 |
| ripped (파손/찢김) | 78 | 122 | 0.955 | 0.664 | 0.783 | 0.709 | 0.556 |
| **ALL** | **216** | **627** | **0.885** | **0.560** | **0.686** | **0.599** | **0.448** |

`results.csv` 마지막 에포크(300)의 검증값과 일치합니다 — Precision 0.870 · Recall 0.555 · mAP50 0.587.
위 표는 별도 리포트 산출 시점의 값이라 소수점 이하가 미세하게 다릅니다.

## 이 런의 위치

Recall 특화 모델의 학습 런입니다. 서비스에 투입되는 것은 이 단일 모델이 아니라
**Recall 특화 · Precision 특화 · 낙서(doodle) 전담 3종을 WBF로 융합한 앙상블**입니다.
따라서 위 단일 모델 수치를 서비스 성능으로 인용하면 안 됩니다.

- 전체 학습 이력과 폐기된 런: `WMS_docs/20_AI_비전/22_YOLO_모델_학습이력.md`
- 앙상블 구성: `WMS_docs/20_AI_비전/25_WBF_3Model_앙상블_아키텍처.md`
- 검증셋 고정 경위: `WMS_docs/20_AI_비전/23_YOLOv8_학습결과_분석보고서.md`

## 파일 안내

| 파일 | 내용 |
| --- | --- |
| `results.csv` · `results.png` | 에포크별 손실·지표 추이 |
| `BoxP_curve` · `BoxR_curve` · `BoxF1_curve` · `BoxPR_curve` | 신뢰도 임계값별 Precision/Recall/F1/PR 곡선 |
| `confusion_matrix(_normalized)` | 클래스 혼동 행렬 |
| `labels.jpg` | 라벨 분포 (클래스 불균형 확인용) |
| `train_batch*.jpg` | 학습 배치 샘플 (증강 적용 상태) |
| `val_batch*_labels/pred.jpg` | 검증 배치의 정답 대 예측 대조 |
| `yolo_training_history_analysis.png` | 학습 이력 종합 분석 |
