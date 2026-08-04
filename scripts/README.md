# scripts/ — 개발·운영 보조 스크립트

레포 루트에 흩어져 있던 일회성/보조 스크립트를 용도별로 정리한 폴더입니다.
(2026-08-05 루트 정리 작업으로 이동됨. `scratch_*.py` 계열은 `../scratch/`로 이동)

## 폴더 구조

| 폴더 | 용도 |
|---|---|
| `seed/` | 데모·테스트 데이터 시딩 (도서 50권, HITL 항목, 재고, 리시드) |
| `debug/` | DB/데이터 상태 점검, 추적, 비밀번호 유틸 (`check_*`, `trace_lpn`, `test_query` 등) |
| `oneoff/` | 과거 일회성 마이그레이션·패치 스크립트 (`fix_*`, `patch_*`, `migrate.py` 등) — **재실행 금지**, 이력 보존용 |
| (루트) | 현행 유틸: S3 백필, 합성 데이터셋 생성, 시딩, 오토라벨링 실행 배치 |

## 실행 방법 (중요)

대부분의 스크립트는 `from app...` 임포트를 사용하므로 **반드시 백엔드 레포 루트에서** 실행합니다:

```
# 레포 루트(wms-secret-backend/)에서
python scripts/seed/seed_hitl_item.py        # X — 임포트 깨짐
python -m scripts.seed.seed_hitl_item        # O — 모듈로 실행
```

또는 `PYTHONPATH=.`를 지정해 직접 실행해도 됩니다.

`run_auto_labeling.bat` / `run_auto_labeling_gui.bat`는 내부에서 레포 루트로 자동 이동(`cd /d "%~dp0.."`)하므로 위치와 무관하게 더블클릭/드래그앤드롭으로 사용 가능합니다.

## 주의

- `oneoff/`의 fix/patch 스크립트는 특정 시점의 DB·코드 상태를 전제로 작성된 것이라 현재 스키마에 다시 실행하면 데이터가 손상될 수 있습니다. 참고용으로만 보존합니다.
- 루트의 `yolo*.pt`는 학습용 베이스 가중치로, `scratch/`의 학습 스크립트가 참조하므로 이동하지 않았습니다. (서빙용 모델은 `app/ai/` 하위의 별도 `.pt`를 사용)
