# ai_training — 데이터셋 구축 · 라벨링 · 학습 스크립트

서빙에 쓰는 YOLO 3종을 **실제로 만들어낸 도구들**이다. 애플리케이션 런타임 경로가 아니라
모델을 새로 학습하거나 데이터셋을 재구성할 때 쓴다.

## 파이프라인 순서

```
① 수집      crawl_torn_books.py · crawl_wikimedia_unsplash.py
                ↓
② 정제      deduplicate_dataset.py · deduplicate_by_stem.py · check_ds1_ds2_diff.py
                ↓
③ 라벨링    auto_labeling_with_yolo.py (의사 라벨 생성) → run_auto_labeling_gui.py (HITL 정제)
                ↓
④ 합성 보강 build_synthetic_doodle_dataset.py · demonstrate_copypaste_ripped.py
                ↓
⑤ 통합      build_aihub_doodle_dataset.py · merge_aihub_to_unified_dataset.py · build_stage1_dataset.py
                ↓
⑥ 학습      train_yolo_v7_doodle.py · train_doodle_yolo.py · train_stage1_yolo.py
                ↓
⑦ 확인      check_current_val.py · visualize_yolo_bbox.py · visualize_raw3_bbox.py
```

## 알아 둘 것

- **베이스 가중치**(`yolov8m.pt` 등)는 리포 루트에 있으나 **git 추적 대상이 아니다.**
  없으면 Ultralytics가 자동으로 내려받는다. 서빙 이미지에는 들어가지 않는다
  (Dockerfile은 `app/ai/*.pt`만 COPY).
- **학습 산출물의 출처**는 `docs/vision_model_docs/README.md`에 기록돼 있다.
  어떤 런이 어떤 조건으로 무슨 성능을 냈는지 그쪽을 본다.
- `restore_dataset.py` · `copy_crawled_to_desktop.py` · `save_bbox_to_desktop.py`는
  로컬 경로가 하드코딩돼 있을 수 있다. 실행 전에 경로를 확인한다.

## 관련 문서

| 주제 | 문서 |
| --- | --- |
| 학습 이력 · 폐기된 런 | `WMS_docs/20_AI_비전/22_YOLO_모델_학습이력.md` |
| 검증셋 고정 경위 | `WMS_docs/20_AI_비전/23_YOLOv8_학습결과_분석보고서.md` |
| 앙상블 구성 | `WMS_docs/20_AI_비전/25_WBF_3Model_앙상블_아키텍처.md` |
| 합성 데이터 파이프라인 | `WMS_docs/20_AI_비전/28_ISBN_비전_합성데이터셋_파이프라인.md` |
| 오토라벨링 가이드 | `WMS_docs/20_AI_비전/62_labelImg_오토라벨링_가이드.md` |
