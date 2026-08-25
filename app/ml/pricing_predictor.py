"""
동적 가격 구매확률 모델 추론 래퍼.

학습된 XGBoost 모델을 프로세스당 1회 로드해 재사용한다. 모델 파일이 없거나 로드에
실패하면 규칙 산식으로 자동 폴백한다 — 가격 산정은 매출에 직결되므로 모델 부재가
서비스 중단으로 이어지면 안 된다.

추론은 결정론적이다: 학습이 끝난 트리 앙상블은 같은 입력에 항상 같은 값을 낸다.
따라서 '동일 입력 반복 시 동일 가격' 성질이 유지된다.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import numpy as np

MODEL_PATH = Path(__file__).parent / "artifacts" / "pricing_p_sold.json"

_model = None
_load_failed = False
_lock = threading.Lock()


def _rule_based_p_sold(
    discount: float, ubci_score: float, seasonality: float, dwell_decay: float
) -> float:
    """모델 부재 시 폴백. 기존 선형 산식과 동일하다."""
    p = (
        0.30
        + discount * 0.80
        - ((100.0 - ubci_score) / 100.0) * 0.60
        + (seasonality - 1.0) * 0.40
        - dwell_decay
    )
    return max(0.05, min(0.98, p))


def _get_model():
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    with _lock:
        if _model is not None or _load_failed:
            return _model
        try:
            import xgboost as xgb

            if not MODEL_PATH.exists():
                _load_failed = True
                return None
            m = xgb.XGBRegressor()
            m.load_model(MODEL_PATH)
            _model = m
        except Exception:
            _load_failed = True
    return _model


def is_model_active() -> bool:
    """모델이 실제로 로드되어 추론에 쓰이는지 여부."""
    return _get_model() is not None


def predict_p_sold_batch(
    discounts: list[float], ubci_score: float, seasonality: float, dwell_days: int
) -> list[float]:
    """
    할인율 후보들에 대한 구매 성사 확률을 한 번에 예측한다.
    후보를 배치로 넘겨 그리드 탐색 1회당 추론도 1회만 일어나게 한다.
    """
    dwell_decay = min(dwell_days, 365) / 365.0 * 0.10

    def _fallback() -> list[float]:
        return [
            _rule_based_p_sold(d, ubci_score, seasonality, dwell_decay)
            for d in discounts
        ]

    model = _get_model()
    if model is None:
        return _fallback()

    # 추론 단계 예외(입력 형상 불일치·메모리 부족 등)도 폴백으로 흡수한다.
    # 가격 산정이 멈추면 출고 자체가 막히므로, 정확도를 조금 잃더라도 산출은 계속한다.
    try:
        X = np.array(
            [
                [d, ubci_score, seasonality, float(min(dwell_days, 365))]
                for d in discounts
            ],
            dtype=np.float32,
        )
        preds = model.predict(X)
        if len(preds) != len(discounts) or not np.all(np.isfinite(preds)):
            return _fallback()
        return [float(max(0.05, min(0.98, p))) for p in preds]
    except Exception:
        return _fallback()


def model_label() -> str:
    """응답에 실을 산정 방식 표기. 실제 동작과 어긋나지 않게 상태에 따라 바꾼다."""
    if is_model_active():
        return "XGBoost Purchase-Probability + Expected Revenue Maximization"
    return (
        "Rule-based Price Elasticity + Expected Revenue Maximization (모델 미로드 폴백)"
    )
