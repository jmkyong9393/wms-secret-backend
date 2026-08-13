"""
Detector 노드 - WBF 3-YOLO 앙상블 사전탐지.

LLM을 쓰지 않는 결정론적 노드다. 결함 후보를 좌표와 함께 뽑아 Vision Agent에게
제보로 넘긴다. 판정 권한은 없다 (프리즈 규정: Detector = 결정론적, VLM = 의미 판독).
"""
import os
from typing import Any, Dict, List

from langchain_core.messages import AIMessage

from app.ai.state import WMSInspectionState
from app.ai.agents.common import (
    DEFECT_TRANSLATION_MAP, INNER_PAGE_EXCLUDED_TYPES, TRACK1_IMAGE_COUNT,
    YOLO_TO_UBCI_TYPE, _ensure_local_path, _is_inner_page,
)

# ==========================================
# 0. Detector Node (WBF 3-YOLO 앙상블 사전탐지 - LLM 미사용, 결정론적)
# ==========================================
def detector_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    WBF(Weighted Box Fusion) 3-YOLO 앙상블로 결함 후보를 사전 탐지한다. LLM을 쓰지 않는다.

    [분리 배경] 예전에는 vision_agent 하나가 이 YOLO 탐지 + GPT-4o VLM 판독 + GPT-4o-mini
    검증 3단계를 모두 수행했다. 그 결과 VLM 호출이 실패하면 YOLO가 이미 찾아둔 결함 후보까지
    함께 사라져 "결함 0건"이 되었고, 그것이 MINT(무결점) 판정으로 이어졌다. 결정론적 탐지와
    확률적 LLM 판독은 실패 특성이 전혀 다르므로 노드를 분리해, VLM이 죽어도 YOLO 결과는
    상태에 남아 후속 판정 근거로 쓰이게 한다.
    """
    image_paths = state.get("image_paths") or []
    yolo_candidates = []

    # --- Track 1 범위 한정 ---
    # 촬영 규격상 인덱스 0·1·2는 앞면·뒷면·책등으로 고정된다. 이 세 각도는 학습셋(Roboflow 811장)에 같은 구도가 존재하므로 WBF 앙상블이 담당한다.
    # - 앞뒤 표지 약 50%, 책등 약 40%
    # - 반면 책배(종이 단면)는 80장 표본에 2~3장뿐이라 사실상 학습된 적이 없다.
    # 인덱스를 규격으로 고정하면 이 세 장은 VLM 분류를 거칠 필요가 없어, "VLM이 표지를 책배로 오분류하는" 실패 경로가 원천 차단된다.
    # 3번 이후(책배·속지)는 vision_agent가 GPT-4o로 직접 판독한다. 모델이 배운 적 없는 면에 바운딩 박스를 강요하지 않는다.
    # 얇은 문고본·중철 제본은 책등 촬영을 스킵할 수 있으므로, 실제 장수가 3장 미만이면 있는 만큼만 처리한다.
    track1_paths = image_paths[:TRACK1_IMAGE_COUNT]

    try:
        from app.ai.wbf_detector import wbf_detector
        for idx, path in enumerate(track1_paths):
            local_path = _ensure_local_path(path)
            if not local_path:
                continue
            for d in wbf_detector.detect_defects_wbf(local_path):
                yolo_candidates.append({
                    "image_index": idx,
                    "type": YOLO_TO_UBCI_TYPE.get(d["defect_type"], d["defect_type"]),
                    "confidence": d["confidence"],
                    "bbox": d["bbox"],
                })
        skipped = len(image_paths) - len(track1_paths)
        detector_text = (
            f"WBF 3-YOLO 앙상블 사전탐지 완료 - Track 1 {len(track1_paths)}장"
            f"(앞면·뒷면·책등)에서 결함 후보 {len(yolo_candidates)}건 검출"
            + (f" / 책배·속지 {skipped}장은 VLM 판독으로 회부" if skipped > 0 else "")
        )
    except Exception as e:
        detector_text = f"WBF 앙상블 사전탐지 실패({type(e).__name__}) - GPT-4o VLM 단독 판독으로 진행"
        print(f"[Agent] Detector Node: {detector_text}")

    print(f"[Agent] Detector Node: {detector_text}")
    return {
        "yolo_candidates": yolo_candidates,
        "detector_text": detector_text,
        "executed_agents": ["detector_node"],
        "messages": [AIMessage(content=f"[Detector] {detector_text}")],
    }


# ==========================================
# 1. Vision Agent (GPT-4o VLM 정밀검수 -> GPT-4o-mini 예비감점 검증)
# ==========================================
# vision_agent 내부 리터럴에서 모듈 상수로 승격. 문자열은 한 글자도 바꾸지 않았다.
# - 판독기 A/B 측정(app/scripts/ab_vision_sonnet.py)이 **같은 프롬프트**를 써야 결과를 비교할 수 있는데, 복제해 두면 한쪽만 고쳐질 때 비교가 조용히 무의미해진다.
def build_yolo_hint(yolo_candidates: List[Dict[str, Any]]) -> str:
    """YOLO 사전탐지 후보를 판독 프롬프트에 붙일 문구로 만든다.

    [2026-08-06] 후보를 JSON 그대로(=confidence 포함) 넣으면 모델이 그것을 **답으로 베낀다.**
    실측: 후보 5건과 확정 결함 5건의 confidence가 소수점 4자리까지 동일
    (0.7578 / 0.7947 / 0.5241 / 0.4370 / 0.5903, job b7b34ae1). 후보에 잡음이 많을 때는
    말이 안 되는 것들을 걸러내며 "판독하는 것처럼" 보였지만, 후보 품질이 올라가자 전건
    복사가 드러났다. 즉 판독이 아니라 통과 도장이었다.

    그래서 후보에서 **confidence를 제거**하고 위치·유형만 넘긴다. 베낄 확신도가 없으면
    스스로 판단해 적을 수밖에 없다. 동시에 후보를 "정답 목록"이 아니라 **기각해야 할 수도
    있는 제보**로 제시한다.

    vision_agent와 판독기 A/B 측정(app/scripts/ab_vision_sonnet.py)이 이 함수를 공유한다.
    문구를 복제해 두면 한쪽만 고쳐질 때 비교가 조용히 무의미해진다.
    """
    if not yolo_candidates:
        return "\n\n[사전 YOLO 제보 없음 - 이미지에서 직접 결함을 판독할 것]"

    # Detector가 소유한 유형은 제보에서 뺀다 - 단, 속지 컷에 한해서다. 보여주면 다시 재심 구조가 되고 그게 전건 승인의 원인이었다.
    # 표지·책등(Track 1) 후보는 그대로 제시한다. 그쪽은 VLM의 종합 판단을 유지한다.
    hint_items = [
        {k: v for k, v in c.items() if k != "confidence"}
        for c in yolo_candidates
        if not (
            _is_inner_page(c.get("image_index"))
            and YOLO_TO_UBCI_TYPE.get(c.get("defect_type", ""), "") in INNER_PAGE_EXCLUDED_TYPES
        )
    ]
    if not hint_items:
        return "\n\n[사전 YOLO 제보 없음 - 이미지에서 직접 결함을 판독할 것]"
    return (
        "\n\n[사전 YOLO 제보 - **정답이 아니다**. 오탐이 섞여 있으며 전부 기각해도 된다]\n"
        f"{json.dumps(hint_items, ensure_ascii=False)}\n"
        "위 좌표를 눈으로 확인해 실제로 결함이 보이는 것만 채택하고, 보이지 않으면 버려라.\n"
        "제보에 없더라도 직접 본 결함은 반드시 추가하라.\n"
        "confidence는 제보 값이 아니라 **당신이 이미지를 보고 판단한 확신도**를 적어라."
    )


