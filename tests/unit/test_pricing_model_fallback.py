# 동적 가격 모델의 폴백 검증 — 모델이 없거나 죽어도 가격 산정은 멈추지 않아야 한다.
# 가격이 안 나오면 출고가 막히므로, 정확도를 잃더라도 산출은 계속되는 것이 설계 의도다.
import importlib

import pytest

from app.domains.orders.service import calculate_price_elasticity_revenue_optimization as calc


def _reload_predictor():
    import app.ml.pricing_predictor as pp
    return importlib.reload(pp)


def test_model_is_active_by_default():
    """학습 산출물이 레포에 있으므로 기본 경로는 모델 추론이어야 한다."""
    pp = _reload_predictor()
    assert pp.is_model_active() is True
    assert "XGBoost" in pp.model_label()


def test_falls_back_when_model_file_missing(monkeypatch):
    """모델 파일이 없으면 규칙 산식으로 자동 전환된다."""
    pp = _reload_predictor()
    monkeypatch.setattr(pp, "MODEL_PATH", pp.MODEL_PATH.parent / "does_not_exist.json")
    monkeypatch.setattr(pp, "_model", None)
    monkeypatch.setattr(pp, "_load_failed", False)

    probs = pp.predict_p_sold_batch([0.1, 0.3, 0.5], ubci_score=85, seasonality=1.0, dwell_days=30)
    assert len(probs) == 3
    assert all(0.05 <= p <= 0.98 for p in probs)
    assert pp.is_model_active() is False
    assert "폴백" in pp.model_label()


def test_falls_back_when_inference_raises(monkeypatch):
    """추론 중 예외가 나도 결과를 돌려준다."""
    pp = _reload_predictor()

    class Boom:
        def predict(self, X):
            raise RuntimeError("inference failure")

    monkeypatch.setattr(pp, "_model", Boom())
    probs = pp.predict_p_sold_batch([0.2, 0.4], ubci_score=70, seasonality=1.1, dwell_days=10)
    assert len(probs) == 2
    assert all(0.05 <= p <= 0.98 for p in probs)


def test_falls_back_when_prediction_has_nan(monkeypatch):
    """NaN/Inf가 섞여 나오면 신뢰할 수 없으므로 규칙 산식으로 대체한다."""
    import numpy as np
    pp = _reload_predictor()

    class Nan:
        def predict(self, X):
            return np.array([float("nan")] * len(X))

    monkeypatch.setattr(pp, "_model", Nan())
    probs = pp.predict_p_sold_batch([0.2, 0.4], ubci_score=70, seasonality=1.1, dwell_days=10)
    assert all(p == p for p in probs)  # NaN이 아님


def test_price_is_deterministic_across_repeats():
    """같은 입력이면 항상 같은 가격 — 모델 추론이 결정론적임을 보장한다."""
    results = {
        (calc(20000, 88, 30, "IT")["final_price"], calc(20000, 88, 30, "IT")["optimal_discount_rate"])
        for _ in range(30)
    }
    assert len(results) == 1


@pytest.mark.parametrize("ubci_low,ubci_high", [(60, 90), (70, 100)])
def test_better_condition_gets_smaller_discount(ubci_low, ubci_high):
    """상태가 좋을수록 할인이 작아야 한다(가격이 높아야 한다)."""
    low = calc(20000, ubci_low, 30, "Novel")
    high = calc(20000, ubci_high, 30, "Novel")
    assert high["optimal_discount_rate"] <= low["optimal_discount_rate"]
    assert high["final_price"] >= low["final_price"]
