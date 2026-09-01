"""
Critic Agent - 2단 교차검증.

  · Stage A (LLM 없음) : Vision 결함 수 ↔ Policy 감점 정합성 등 사실 대조. 위반 시 즉시 HITL.
  · Stage B (GPT-4o-mini) : 판독 타당성 심사. Stage A 통과 + 결함 1건 이상일 때만 실행한다.

Stage B는 부가 검증이므로 fail-open이다. LLM 장애 시 Stage A 결과만으로 진행한다.
"""

import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage

from app.ai.agents.common import DEFECT_TRANSLATION_MAP
from app.ai.agents.llm import llm_mini
from app.ai.agents.schemas import CriticVerdict
from app.ai.state import WMSInspectionState


def critic_stage_a_integrity_check(defects: list, image_count: int, score) -> list[str]:
    """Vision 결함 목록과 Policy 산출 점수의 결정론적 정합성 대조 (LLM 미사용).
    critic_agent Stage A 본체 - admin/router.py HITL 재검증도 재사용."""
    integrity_issues: list[str] = []

    for i, d in enumerate(defects):
        if not isinstance(d, dict):
            integrity_issues.append(f"결함[{i}] 형식 오류")
            continue
        # BBox 누락: 프론트가 결함 위치를 표시할 수 없어 "투명 공개" 보증이 깨진다.
        if not isinstance(d.get("bbox"), dict):
            integrity_issues.append(f"결함[{i}]({d.get('type')}) BBox 좌표 누락")
        # image_index 범위 초과: VLM이 존재하지 않는 이미지를 지목한 환각 신호.
        idx = d.get("image_index")
        if image_count and isinstance(idx, int) and not (0 <= idx < image_count):
            integrity_issues.append(
                f"결함[{i}] image_index({idx})가 촬영 장수({image_count}) 범위를 벗어남"
            )

    # 결함이 있는데 감점이 0점(=100점 만점)이면 Vision과 Policy 보고가 모순된다.
    if defects and score == 100:
        integrity_issues.append(
            f"결함 {len(defects)}건이 보고되었으나 UBCI 감점이 0점(100점)으로 산출됨"
        )

    # 결함이 없는데 감점이 발생한 경우도 마찬가지로 모순이다.
    if not defects and score is not None and score < 100:
        integrity_issues.append(
            f"결함 0건인데 UBCI {score}점(감점 {100 - score}점)이 산출됨"
        )

    # 확신도를 YOLO 제보에서 그대로 베낀 결함 - 이미지를 보고 판단한 결과가 아니다.
    # Vision Agent가 결정론적으로 표시해 둔 플래그를 여기서 정합성 위반으로 승격시킨다.
    # (판독을 신뢰할 수 없으므로 자동 확정 금지 - 결함 자체는 근거로 보존한다)
    copied = [
        i
        for i, d in enumerate(defects)
        if isinstance(d, dict) and d.get("conf_copied_from_candidate")
    ]
    if copied:
        integrity_issues.append(
            f"결함 {len(copied)}건의 확신도가 YOLO 제보 값과 완전히 일치 - VLM이 이미지를 "
            f"직접 판단하지 않고 제보를 반환한 것으로 보임"
        )

    # 비접지 BBox - VLM이 위치를 못 잡고 좌표를 지어낸 패턴 (동일 좌표 반복 / 등차 나열).
    # 처리 계보는 conf_copied_from_candidate와 같다: 결함은 보존하되 자동 확정을 막고
    # 관리자가 실제 위치를 그리도록 HITL로 보낸다 (실측: LPN-260810-A030 · A012).
    ungrounded = [
        i
        for i, d in enumerate(defects)
        if isinstance(d, dict) and d.get("bbox_ungrounded")
    ]
    if ungrounded:
        integrity_issues.append(
            f"결함 {len(ungrounded)}건의 BBox가 지어낸 좌표 패턴(동일 좌표 반복/등차 나열) - "
            f"VLM이 위치를 특정하지 못한 것으로 보이므로 관리자가 좌표를 확인해야 함"
        )

    return integrity_issues


def critic_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Critic Agent: 판정 결과 애매성 평가 및 HITL 관리자 이관 판단 중...")
    revision = state.get("revision_count", 0)
    score = state.get("ubci_score")

    # Vision 판독 실패도 별도 분기를 만들지 않고 기존 재검수 루프에 태운다.
    # ubci_score가 None으로 남아 있으므로 아래 `score is None and revision < 2` 분기가
    # Vision 재판독을 지시하고(최대 2회), 소진되면 이 위의 revision >= 2 분기가
    # Supervisor를 통해 HITL로 이관한다. Rate limit/타임아웃 같은 일시 장애는 재시도로
    # 회복되고, 키 만료처럼 회복 불가한 장애만 사람에게 도달한다.
    vision_failed = bool(state.get("vision_failed"))

    if revision >= 2:
        cause = (
            "Vision 판독이 2회 연속 실패"
            if vision_failed
            else f"판정 애매성 지속 (UBCI {score}점)"
        )
        critic_text = f"최대 재검수 루프(2회) 초과 - {cause}. 자동 확정 불가로 HITL 관리자 수동 오버라이드로 이관합니다."
        return {
            "reason_code": "MAX_RETRIES_AMBIGUOUS_HITL",
            "repair_directive": "최대 재검수 횟수(2회) 초과 ➔ HITL 관리자 수동 오버라이드 이관",
            "revision_count": revision,
            "critic_text": critic_text,
            "executed_agents": ["critic_agent"],
            "messages": [
                AIMessage(
                    content="[Critic Agent] ⚠️ 최대 재검수 루프(2회) 초과 ➔ HITL 관리자 검수 이관"
                )
            ],
        }

    if score is not None and 58 <= score <= 66:
        critic_text = f"교차 검증 결과 UBCI {score}점은 NORMAL/REJECT 등급 경계선(58~66점) 구간 - 자동 확정 보류, HITL 이관"
        return {
            "reason_code": "BOUNDARY_AMBIGUOUS_HITL",
            "repair_directive": f"입고 등급 경계선(UBCI {score}점) 판정 애매 ➔ HITL 관리자 수동 오버라이드 이관",
            "revision_count": revision,
            "critic_text": critic_text,
            "executed_agents": ["critic_agent"],
            "messages": [
                AIMessage(
                    content=f"[Critic Agent] ⚠️ 입고 등급 경계선(UBCI {score}점) 판정 애매 ➔ HITL 관리자 개입 이관"
                )
            ],
        }

    if score is None and revision < 2:
        cause = (
            "Vision 판독 실패(외부 VLM 오류)"
            if vision_failed
            else "Policy Agent UBCI 점수 미산출"
        )
        critic_text = f"{cause} - Vision Agent 재판독 지시 (재시도 {revision + 1}/2회)"
        return {
            "reason_code": "REJECT",
            "repair_directive": "UBCI 점수 계산 누락. Vision Agent 재검수 지시",
            "revision_count": revision + 1,
            # 재시도 시 이전 실패 표식을 반드시 지운다. 남겨두면 재판독이 성공해도
            # Policy가 계속 점수 산출을 건너뛰어 무한히 재검수만 반복하게 된다.
            "vision_failed": False,
            "critic_text": critic_text,
            "executed_agents": ["critic_agent"],
            "messages": [
                AIMessage(
                    content=f"[Critic Agent] 🔄 {cause} ➔ Vision Agent 재검수 (재시도 {revision + 1}/2회)"
                )
            ],
        }

    # --- 실질 교차검증 (Cross-Check) ---
    # [수정 이력] 이전 Critic은 점수 구간(58~66)과 재시도 횟수만 확인해, 이름에 붙은
    # "Cross-Check / 환각 방어" 역할을 실제로는 전혀 수행하지 않았다. Vision이 보고한 결함과
    # Policy가 실제로 감점한 항목이 어긋나도(예: 결함 3건인데 감점 1건) 그대로 통과했다.
    # LLM 없이 결정론적으로 검증 가능한 항목들을 여기서 대조한다.
    defects = state.get("defects") or []
    image_count = len(state.get("image_paths") or [])
    # HITL 재검증(admin/router.py)과 공유하는 별도 함수로 위임 (배경: 33번 문서).
    integrity_issues = critic_stage_a_integrity_check(defects, image_count, score)

    if integrity_issues:
        detail = " / ".join(integrity_issues[:5])
        critic_text = f"교차 검증 실패 - Vision·Policy 보고 불일치 감지: {detail}. 자동 확정 대신 관리자 검토로 이관합니다."
        return {
            "reason_code": "HUMAN_REQUIRED",
            "repair_directive": f"정합성 위반: {detail}",
            "revision_count": revision,
            "critic_text": critic_text,
            "executed_agents": ["critic_agent"],
            "messages": [
                AIMessage(
                    content=f"[Critic Agent] ⚠️ 정합성 위반 {len(integrity_issues)}건 ➔ HITL 이관"
                )
            ],
        }

    # --- Stage B: LLM 판독 타당성 심사 (GPT-4o-mini) ---
    #
    # Stage A(위)가 산술·구조 정합성을 결정론적으로 검증했다면, Stage B는 결정론적으로
    # 판단할 수 없는 것 - "Vision이 본 것이 실제로 그 결함이 맞는가" - 를 심사한다.
    # 예: 내지 대부분을 덮는 DMG_INT_DOODLE(인쇄된 표를 낙서로 오탐), 앞/뒤 표지에 거의
    # 동일 좌표로 중복 보고된 결함, special_notes와 모순되는 결함 등.
    #
    # 비용 설계: 결함이 0건이면 심사할 대상 자체가 없으므로 호출하지 않는다. 물량의 다수를
    # 차지하는 MINT 건에서는 LLM 호출이 0회라 전체 비용 증가가 미미하다.
    # 실패 시 fail-open: 부가 검증이므로 LLM 장애가 파이프라인을 멈추게 하지 않는다.
    llm_verdict = None
    if llm_mini and defects:
        try:
            evidence = [
                {
                    "index": i,
                    "type": d.get("type"),
                    "image_index": d.get("image_index"),
                    "bbox": d.get("bbox"),
                    "ratio": d.get("ratio"),
                    "confidence": d.get("confidence"),
                    # 크롭을 직접 본 증거 대조 검증의 판정. Stage B는 이미지를 보지 않으므로
                    # 이 값을 좌표 휴리스틱으로 뒤집지 않도록 프롬프트에서 명시한다.
                    "verified": d.get("verify_visible") or "미심사",
                    "deduction": d.get(
                        "applied_deduction", d.get("preliminary_deduction")
                    ),
                }
                for i, d in enumerate(defects)
            ]
            verdict_prompt = f"""당신은 중고도서 비전 검수 결과를 감리하는 Critic입니다.
Vision AI가 보고한 결함 판독이 타당한지 보수적이고 엄격하게 심사하세요.

[심사 대상]
- 도서명: {state.get("book_title") or "미상"}
- 검수 이미지 장수: {image_count}장
- Vision 특이사항: {state.get("special_notes") or "없음"}
- 판독 결함 목록(JSON): {json.dumps(evidence, ensure_ascii=False)}
- Policy 산출 점수: UBCI {score}점

[중요 - 당신은 이미지를 보지 않습니다]
당신에게는 좌표와 메타데이터만 주어집니다. 따라서 **"실제로 손상이 보이는가"는 판단하지
마세요.** 그 판단은 결함 부위를 확대한 크롭을 직접 본 증거 대조 검증이 이미 수행했고,
그 결과가 아래 목록의 verified 필드에 있습니다. 좌표만 보고 그 판정을 뒤집지 마세요.
당신의 역할은 **좌표·개수·유형의 구조적 모순**을 찾는 것으로 한정됩니다.

[모서리 마모(DMG_EDGE_WEAR)에 대한 주의]
한 권에는 모서리가 여러 개이고 보통 함께 닳습니다. 같은 구석이 앞표지·뒤표지·책등 컷에
모두 잡히기도 합니다. 따라서 **마모가 한 이미지에 여러 건 보고되거나 여러 컷에 걸쳐
보고되는 것은 정상이며 오탐의 근거가 아닙니다.** 감점은 건수가 아니라 "책 기준으로 서로
다른 모서리 몇 곳인가"로 산정되며(중복은 Policy가 좌표로 합칩니다), 이 계산은 이미
결정론적으로 끝나 있습니다. 마모 건수가 많다는 이유로 REJECTED를 내지 마세요.

[REJECTED로 판단해야 하는 경우]
1. BBox가 이미지 대부분(면적 50% 이상)을 덮는 결함 - 인쇄된 본문/표/삽화를 결함으로
   오탐했을 가능성이 높습니다. (좌표계는 0~1000 기준)
2. 마모가 **아닌** 유형이 서로 다른 이미지에 거의 동일한 좌표로 중복 보고된 경우.
3. 결함 유형과 발견 위치가 물리적으로 모순되는 경우
   (예: 표지에만 생기는 손상이 내지에서 보고됨).
4. special_notes의 서술과 결함 목록이 서로 모순되는 경우.

위 어디에도 해당하지 않으면 APPROVED로 판단하세요.
오탐이 의심되는 항목이 있으면 suspect_indices에 해당 index를 넣으세요.
"""
            structured_critic = llm_mini.with_structured_output(CriticVerdict)
            llm_verdict = structured_critic.invoke(
                [HumanMessage(content=verdict_prompt)]
            )
        except Exception as e:
            print(
                f"[Critic Agent] Stage B LLM 심사 실패 - 결정론적 판정만으로 진행: {e}"
            )

    if llm_verdict and llm_verdict.decision == "REJECTED":
        suspect = (
            f" (오탐 의심 인덱스: {llm_verdict.suspect_indices})"
            if llm_verdict.suspect_indices
            else ""
        )
        critic_text = f"판독 타당성 심사 반려 - {llm_verdict.reason}{suspect}"

        # 재검수 여유가 남아 있으면 Vision에 재판독을 지시하고, 소진됐으면 사람에게 넘긴다.
        if revision < 2:
            return {
                "reason_code": "REJECT",
                "repair_directive": f"판독 타당성 미달: {llm_verdict.reason}. 지목된 결함을 재확인할 것{suspect}",
                "revision_count": revision + 1,
                # 재판독을 위해 이전 결함 목록을 비운다 (vision_agent는 defects가 비어야 재호출한다).
                "defects": [],
                "critic_text": f"{critic_text} ➔ Vision Agent 재판독 지시 (재시도 {revision + 1}/2회)",
                "executed_agents": ["critic_agent"],
                "messages": [AIMessage(content=f"[Critic Agent] 🔄 {critic_text}")],
            }

        return {
            "reason_code": "MAX_RETRIES_AMBIGUOUS_HITL",
            "repair_directive": f"판독 타당성 미달이 재검수 후에도 해소되지 않음: {llm_verdict.reason}",
            "revision_count": revision,
            "critic_text": f"{critic_text} ➔ 재검수 소진, HITL 관리자 검수 이관",
            "executed_agents": ["critic_agent"],
            "messages": [
                AIMessage(content=f"[Critic Agent] ⚠️ {critic_text} ➔ HITL 이관")
            ],
        }

    grade_label = (
        "S급(MINT)"
        if (score or 0) >= 95
        else (
            "A급(GOOD)"
            if (score or 0) >= 85
            else ("B급(NORMAL)" if (score or 0) >= 65 else "C급(REJECT)")
        )
    )
    verdict_note = (
        f" / 판독 타당성 심사 승인({llm_verdict.reason})" if llm_verdict else ""
    )
    critic_text = (
        f"교차 검증 통과 - 결함 {len(defects)}건의 BBox/image_index 정합성 및 "
        f"산출 점수(UBCI {score}점)와 {grade_label} 등급 분기 조건 확인{verdict_note}, 보증서 발행 승인"
    )
    return {
        "reason_code": "OK",
        "repair_directive": None,
        "revision_count": revision,
        "critic_text": critic_text,
        "executed_agents": ["critic_agent"],
        "messages": [
            AIMessage(
                content="[Critic Agent] 판정 명확성 검증 완료 ➔ Report Agent 보증서 발행 승인 (OK)"
            )
        ],
    }
