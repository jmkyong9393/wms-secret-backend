"""노드 계측 — 구간 지연과 LLM 토큰을 노드 단위로 수집한다.

**노드 구현을 수정하지 않는다.** 그래프 등록 시점에 노드 함수를 감싸기만 하므로
프리즈 구역(파이프라인 구조·모델 배정)에 영향이 없다. 래퍼를 벗기면 원래 함수다.

수집 값은 state의 두 리듀서 필드에 누적된다.
  node_timings  [{node, ms, at}]              재검수 루프가 돌면 같은 노드가 여러 번 쌓인다
  node_tokens   [{node, prompt, completion, total, cost_usd, calls}]

계측 실패가 검수를 막아서는 안 되므로 모든 예외를 삼킨다(fail-open).
"""

import contextvars
import functools
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

# 현재 실행 중인 노드 이름과 그 노드의 토큰 수집함.
# langchain_community(get_openai_callback)가 설치돼 있지 않아 직접 만든다.
# 콜백은 전역 인스턴스라 "어느 노드에서 부른 호출인가"를 알 수 없으므로 contextvar로 잇는다.
_current_node: contextvars.ContextVar = contextvars.ContextVar(
    "wms_current_node", default=None
)
_token_sink: contextvars.ContextVar = contextvars.ContextVar(
    "wms_token_sink", default=None
)

# gpt-4o / gpt-4o-mini 1K 토큰당 단가(USD). 요금 개정 시 이 표만 고친다.
_PRICE = {
    "gpt-4o": {"in": 0.0025, "out": 0.010},
    "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
}


def _cost(model: str, prompt: int, completion: int) -> float:
    # 긴 키부터 대조한다. "gpt-4o"는 "gpt-4o-mini"의 접두사라, 짧은 키를 먼저 보면
    # mini 호출이 전부 4o 단가로 계산된다(실측: policy_agent 5,406토큰이 $0.000905 대신
    # $0.015083으로 기록됐다).
    for key in sorted(_PRICE, key=len, reverse=True):
        if key in (model or ""):
            p = _PRICE[key]
            return prompt / 1000 * p["in"] + completion / 1000 * p["out"]
    return 0.0


from langchain_core.callbacks import BaseCallbackHandler


class TokenCollector(BaseCallbackHandler):
    """LangChain 콜백. 노드별 토큰을 contextvar 수집함에 적재한다.

    **BaseCallbackHandler를 반드시 상속한다.** ChatOpenAI의 `callbacks` 필드는
    타입 검증을 하므로, 상속하지 않은 객체를 넘기면 생성자가 예외를 던지고
    llm 인스턴스가 통째로 None이 된다(실측으로 확인).
    """

    def on_llm_end(self, response, **kwargs):
        try:
            out = getattr(response, "llm_output", None) or {}
            usage = out.get("token_usage") or {}
            model = out.get("model_name") or ""
            if not usage:
                # 일부 경로는 generations[].message.usage_metadata에만 담긴다
                for gen in getattr(response, "generations", None) or []:
                    for g in gen:
                        um = getattr(
                            getattr(g, "message", None), "usage_metadata", None
                        )
                        if um:
                            usage = {
                                "prompt_tokens": um.get("input_tokens", 0),
                                "completion_tokens": um.get("output_tokens", 0),
                                "total_tokens": um.get("total_tokens", 0),
                            }
                            break
            if not usage:
                return
            sink: List[dict] = _token_sink.get()
            if sink is None:
                return
            p = int(usage.get("prompt_tokens") or 0)
            c = int(usage.get("completion_tokens") or 0)
            sink.append(
                {
                    "model": model,
                    "prompt": p,
                    "completion": c,
                    "total": int(usage.get("total_tokens") or (p + c)),
                    "cost_usd": round(_cost(model, p, c), 6),
                }
            )
        except Exception as e:
            logger.debug(f"[계측] 토큰 수집 실패({type(e).__name__}) - 무시한다")


token_collector = TokenCollector()

# 컨테이너 TZ가 UTC라 datetime.now()는 UTC를 준다. 기록 시각은 KST로 남긴다.
_KST = timezone(timedelta(hours=9))


def _now_kst_iso() -> str:
    return datetime.now(_KST).replace(tzinfo=None).isoformat(timespec="seconds")


def instrument(name: str, fn: Callable) -> Callable:
    """노드 함수를 감싸 구간 지연과 토큰 사용량을 state에 덧붙인다."""

    @functools.wraps(fn)
    def wrapped(state: Dict[str, Any], *args, **kwargs):
        t0 = time.perf_counter()
        # 이 노드에서 일어난 LLM 호출만 담길 수집함. 콜백이 contextvar로 찾아온다.
        sink: List[dict] = []
        tok_a = _token_sink.set(sink)
        node_a = _current_node.set(name)
        try:
            out = fn(state, *args, **kwargs)
        finally:
            _token_sink.reset(tok_a)
            _current_node.reset(node_a)

        ms = int((time.perf_counter() - t0) * 1000)
        if not isinstance(out, dict):
            # 노드가 dict를 돌려주지 않으면 계측을 붙일 자리가 없다. 원본을 그대로 돌려준다.
            return out

        try:
            out.setdefault("node_timings", [])
            out["node_timings"] = list(out["node_timings"]) + [
                {"node": name, "ms": ms, "at": _now_kst_iso()}
            ]
            # 토큰이 0인 노드(결정론적 노드)는 기록하지 않는다 — 목록이 의미 없이 길어진다.
            if sink:
                out.setdefault("node_tokens", [])
                out["node_tokens"] = list(out["node_tokens"]) + [
                    {
                        "node": name,
                        "prompt": sum(x["prompt"] for x in sink),
                        "completion": sum(x["completion"] for x in sink),
                        "total": sum(x["total"] for x in sink),
                        "cost_usd": round(sum(x["cost_usd"] for x in sink), 6),
                        "calls": len(sink),
                        "models": sorted({x["model"] for x in sink if x.get("model")}),
                    }
                ]
        except Exception as e:
            logger.warning(
                f"[계측] state 적재 실패({type(e).__name__}) - 검수는 계속한다: {e}"
            )
        return out

    return wrapped


def summarize(timings, tokens) -> Dict[str, Any]:
    """노드별 목록을 발표·논문에서 바로 쓸 수 있는 집계로 접는다."""
    by_node: Dict[str, Dict[str, Any]] = {}
    for t in timings or []:
        n = t.get("node")
        if not n:
            continue
        d = by_node.setdefault(
            n, {"ms": 0, "runs": 0, "tokens": 0, "cost_usd": 0.0, "calls": 0}
        )
        d["ms"] += int(t.get("ms") or 0)
        d["runs"] += 1
    for k in tokens or []:
        n = k.get("node")
        if not n:
            continue
        d = by_node.setdefault(
            n, {"ms": 0, "runs": 0, "tokens": 0, "cost_usd": 0.0, "calls": 0}
        )
        d["tokens"] += int(k.get("total") or 0)
        d["cost_usd"] = round(d["cost_usd"] + float(k.get("cost_usd") or 0.0), 6)
        d["calls"] += int(k.get("calls") or 0)
    return {
        "by_node": by_node,
        "total_ms": sum(v["ms"] for v in by_node.values()),
        "total_tokens": sum(v["tokens"] for v in by_node.values()),
        "total_cost_usd": round(sum(v["cost_usd"] for v in by_node.values()), 6),
        "llm_calls": sum(v["calls"] for v in by_node.values()),
    }
