"""
동적 가격 구매확률 모델 학습 스크립트 (XGBoost).

무엇을: 할인율·상태점수·카테고리 계절성·체류일을 입력받아 '구매 성사 확률'을 예측하는
회귀 모델을 학습하고 `app/ml/artifacts/`에 저장한다. 서비스는 이 모델로 확률을 얻고,
기대매출이 최대가 되는 할인율을 그리드 탐색으로 고른다.

왜 합성 데이터인가: 실판매 이력이 없는 Cold-start 구간이라 규칙 산식을 사전분포로 삼는다.
다만 규칙식을 그대로 복제하면 모델이 배울 것이 없으므로, 선형식이 표현하지 못하는
비선형 구간을 의도적으로 넣는다.
  - 할인율 포화: 일정 할인을 넘으면 추가 할인의 효과가 체감한다(로지스틱 포화)
  - 상태×할인 상호작용: 상태가 나쁜 상품은 같은 할인폭의 효과가 더 작다
  - 체류 구간 효과: 장기 체류는 선형이 아니라 특정 구간에서 급격히 반응한다
  - 관측 잡음: 동일 조건에서도 성사 여부가 흔들리는 현실을 반영

실행: python -m app.ml.train_pricing_model
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import xgboost as xgb

ARTIFACT_DIR = Path(__file__).parent / "artifacts"
MODEL_PATH = ARTIFACT_DIR / "pricing_p_sold.json"
META_PATH = ARTIFACT_DIR / "pricing_p_sold_meta.json"

SEED = 20260815
N_SAMPLES = 20000

# 카테고리 계절성 계수 — service.CATEGORY_SEASONALITY와 같은 축을 쓴다.
SEASONALITY_RANGE = (0.85, 1.25)


def _synthesize(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """학습용 합성 표본 생성. 반환: (특징 행렬, 구매확률)"""
    discount = rng.uniform(0.05, 0.85, N_SAMPLES)
    ubci = rng.uniform(40.0, 100.0, N_SAMPLES)
    seasonality = rng.uniform(*SEASONALITY_RANGE, N_SAMPLES)
    dwell_days = rng.integers(0, 366, N_SAMPLES).astype(float)

    dwell_decay = np.minimum(dwell_days, 365) / 365.0 * 0.10
    condition_gap = (100.0 - ubci) / 100.0

    # 규칙 산식을 사전분포(선형 성분)로 사용
    linear = (
        0.30
        + discount * 0.80
        - condition_gap * 0.60
        + (seasonality - 1.0) * 0.40
        - dwell_decay
    )

    # 선형식이 담지 못하는 성분 — 모델이 실제로 배울 대상
    saturation = 0.18 * (1.0 / (1.0 + np.exp(-(discount - 0.45) * 9.0)) - 0.5)
    interaction = -0.35 * discount * condition_gap
    dwell_cliff = -0.06 * (dwell_days > 180).astype(float)

    p = linear + saturation + interaction + dwell_cliff
    p += rng.normal(0.0, 0.02, N_SAMPLES)  # 관측 잡음
    p = np.clip(p, 0.05, 0.98)

    X = np.column_stack([discount, ubci, seasonality, dwell_days])
    return X, p


def train(verbose: bool = True) -> dict:
    rng = np.random.default_rng(SEED)
    X, y = _synthesize(rng)

    split = int(len(X) * 0.8)
    model = xgb.XGBRegressor(
        n_estimators=400,
        max_depth=5,
        learning_rate=0.06,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        random_state=SEED,
        n_jobs=4,
        tree_method="hist",
        eval_metric="mae",
    )
    if verbose:
        print(f"[학습 데이터] 표본 {N_SAMPLES:,}건 (train {split:,} / valid {len(X)-split:,})")
        print(f"[특징] discount_rate, ubci_score, seasonality, dwell_days")
        print(f"[하이퍼파라미터] n_estimators=400, max_depth=5, lr=0.06, seed={SEED}")
        print("[학습 시작]")
    model.fit(
        X[:split], y[:split],
        eval_set=[(X[:split], y[:split]), (X[split:], y[split:])],
        verbose=50 if verbose else False,
    )

    pred = model.predict(X[split:])
    actual = y[split:]
    mae = float(np.mean(np.abs(pred - actual)))
    rmse = float(np.sqrt(np.mean((pred - actual) ** 2)))
    r2 = float(1 - np.sum((actual - pred) ** 2) / np.sum((actual - actual.mean()) ** 2))

    # 선형 산식만 썼을 때의 오차 — 모델이 무엇을 개선했는지 대조군
    d, u, s, dd = X[split:, 0], X[split:, 1], X[split:, 2], X[split:, 3]
    linear_only = np.clip(
        0.30 + d * 0.80 - ((100 - u) / 100) * 0.60 + (s - 1) * 0.40
        - np.minimum(dd, 365) / 365 * 0.10,
        0.05, 0.98,
    )
    baseline_mae = float(np.mean(np.abs(linear_only - actual)))

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    model.save_model(MODEL_PATH)

    importance = dict(zip(
        ["discount_rate", "ubci_score", "seasonality", "dwell_days"],
        [round(float(v), 4) for v in model.feature_importances_],
    ))
    curve = model.evals_result()
    train_curve = curve["validation_0"]["mae"]
    valid_curve = curve["validation_1"]["mae"]

    meta = {
        "model": "XGBRegressor",
        "xgboost_version": xgb.__version__,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "features": ["discount_rate", "ubci_score", "seasonality", "dwell_days"],
        "target": "purchase_probability",
        "n_samples": N_SAMPLES,
        "train_size": split,
        "valid_size": len(X) - split,
        "seed": SEED,
        "hyperparams": {
            "n_estimators": 400, "max_depth": 5, "learning_rate": 0.06,
            "subsample": 0.9, "colsample_bytree": 0.9, "reg_lambda": 1.0,
        },
        "metrics": {"mae": round(mae, 5), "rmse": round(rmse, 5), "r2": round(r2, 5)},
        "baseline_linear_mae": round(baseline_mae, 5),
        "improvement_vs_linear": round((baseline_mae - mae) / baseline_mae, 4),
        "feature_importance": importance,
        "learning_curve": {
            "train_mae": [round(v, 5) for v in train_curve],
            "valid_mae": [round(v, 5) for v in valid_curve],
        },
    }
    META_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    if verbose:
        print(f"\n[검증 결과] MAE {mae:.5f} / RMSE {rmse:.5f} / R² {r2:.5f}")
        print(f"[대조군] 선형 산식 단독 MAE {baseline_mae:.5f}")
        print(f"[개선율] {(baseline_mae-mae)/baseline_mae*100:.1f}% (선형 대비 오차 감소)")
        print(f"[특징 중요도] {importance}")
        print(f"[저장] {MODEL_PATH.name} / {META_PATH.name}")
    return meta


if __name__ == "__main__":
    train()
