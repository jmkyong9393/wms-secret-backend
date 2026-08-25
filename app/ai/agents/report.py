"""
Report Agent - 고객 공개용 품질 보증서 발행, 그리고 HITL 인계 노드.

[고객 노출 경계] 보증서는 고객이 QR로 열어보는 문서다.
  · 귀책(누구 잘못인지)을 쓰지 않는다 - 표준 운영 정책서 제0장 ④에 따라 AI가 단독 확정하지 않는 값이며, 단정적으로 적히면 그 문서가 분쟁 근거가 된다.
  · 근거 조항은 번호만 남긴다 - "제13조 고객 귀책 추정 기준"처럼 제목에 귀책이 든 조항이 있어 그대로 실으면 고객이 책임 통보로 읽는다.
  · 내부 chunk_id를 노출하지 않는다 (제0조의2 ⑥).
"""

import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage

from app.ai.state import WMSInspectionState
from app.ai.agents.common import DEFECT_TRANSLATION_MAP, now_kst
from app.ai.agents.llm import llm_mini
from app.ai.agents.schemas import CertificateDocument


def _grade_label(ubci_score: int) -> str:
    if ubci_score >= 95:
        return "S급 (MINT)"
    if ubci_score >= 85:
        return "A급 (GOOD)"
    if ubci_score >= 65:
        return "B급 (NORMAL)"
    return "REJECT C급 (폐기)"


def _fallback_certificate(
    ubci_score: int, defects: List[dict], special_notes: Optional[str]
) -> dict:
    """
    LLM 호출이 불가능하거나 실패했을 때 쓰는 결정론적 보증서 본문.
    문장은 여기(백엔드)에서만 만든다 - 프론트가 등급별 문장을 하드코딩하지 않게 하기 위함.
    """
    grade_str = _grade_label(ubci_score)
    findings = []
    for d in defects:
        dtype = str(d.get("type") or "")
        label = DEFECT_TRANSLATION_MAP.get(dtype, dtype or "상태 결함")
        deduction = int(d.get("preliminary_deduction") or 0)
        findings.append(
            {
                "image_index": int(d.get("image_index") or 0),
                "label": label,
                "deduction": deduction,
                "reason": f"{label} 흔적이 확인되어 UBCI {deduction}점을 차감했습니다. 읽는 데는 지장이 없는 수준입니다.",
            }
        )

    if not findings:
        headline = "꼼꼼히 검수했지만, 트집 잡을 곳이 없었습니다"
        condition_detail = "결함 없음. 표지 모서리부터 내지 마지막 장까지 전수 스캔했지만 감점 사유를 단 한 건도 찾지 못했습니다."
        care_tip = "직사광선만 피해 보관하시면 지금 이 상태가 오래갑니다."
    else:
        headline = f"정직하게 {len(findings)}가지 사용 흔적을 짚어드립니다"
        condition_detail = (
            f"총 {len(findings)}건의 사용 흔적이 확인되어 UBCI {grade_str} 판정을 받았습니다. "
            "숨기지 않고 아래에 그대로 공개하며, 해당 감점은 이미 판매가에 반영되어 있습니다."
        )
        care_tip = "이미 반영된 흔적이니 마음 편히 읽으셔도 좋습니다."

    summary = (
        f"Nexus 비전 검수 AI가 표지 훼손율과 내지 전 페이지를 픽셀 단위로 판독했습니다. "
        f"종합 판정 결과 UBCI {ubci_score}점, {grade_str}으로 확정되었습니다."
    )
    if special_notes:
        condition_detail += f" 참고로 {special_notes} 점도 함께 확인되었습니다."

    return {
        "headline": headline,
        "summary": summary,
        "condition_detail": condition_detail,
        "findings": findings,
        "care_tip": care_tip,
    }


# 고객 보증서에 실리면 안 되는 귀책 표현. 표준 운영 정책서 제0장 ④에 따라 귀책은 관리자가 증빙 2개 이상으로 확정하는 값이며, 고객이 받는 문서에 단정적으로 적히면 그 문서가 분쟁의 근거가 된다.
_LIABILITY_WORDS = (
    "귀책",
    "과실",
    "부주의",
    "책임이 있",
    "잘못으로",
    "고객 책임",
    "고객의 책임",
    "배송사 책임",
    "판매자 책임",
    "물류센터 책임",
)


def _sanitize_policy_basis(basis: Optional[List[str]]) -> List[str]:
    """
    보증서에 실릴 근거 조항 표기를 고객 노출 기준에 맞게 정리한다.
    [왜 조항 제목을 자르는가]
    조항 번호만 남기고 설명 제목은 버린다. 우리 정책서에는 "제13조 고객 귀책 추정 기준"처럼 제목 자체에 귀책이 들어간 조항이 있어서, 본문에 귀책 표현을 안 써도 근거 목록에 "고객 귀책"이 그대로 노출된다. 고객은 이를 책임 통보로 읽는다. 조항 번호만 있어도 근거 추적에는 충분하다.
    내부 chunk_id가 섞여 들어온 항목도 제거한다 (표준 운영 정책서 제0조의2 ⑥).
    """
    out: List[str] = []
    for raw in basis or []:
        text = str(raw or "").strip()
        if not text or re.search(r"[a-z0-9_]{6,}", text):  # chunk_id 형태
            continue
        # "WMS 표준 운영 정책서 제13조 고객 귀책 추정 기준" -> "WMS 표준 운영 정책서 제13조"
        m = re.match(r"(.*?제\s*\d+조(?:의\d+)?(?:\s*제?\s*\d+항)?)", text)
        cleaned = (m.group(1) if m else text).strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def build_certificate_document(state: WMSInspectionState) -> dict:
    """
    고객 공개용 보증서 본문을 생성한다.

    [설계 노트] MINT Fast-track(auto_refund_agent)과 일반 경로(report_agent)가 같은 품질의 문서를 내도록 문서 생성 로직을 이 함수 하나로 모았다. 노드를 병합한 것이 아니라 두 노드가 같은 헬퍼를 호출하는 형태이므로, 4-Agent 분리 구조와 모델 배정(프리즈 규정)은 그대로다. 문서 생성 LLM은 규정대로 GPT-4o-mini(llm_mini)를 쓴다.
    """
    ubci_score = state.get("ubci_score")
    if ubci_score is None:
        ubci_score = 100

    all_defects = state.get("defects") or []
    # 감점에 실제로 반영된 항목만 고객 문서의 대상이다. 걸러진 항목은 "검토했으나 결함이 아니라고 판단한 것"이므로 결함으로 보이면 안 된다. 다만 **입력에서 지우지는 않고** 아래 프롬프트에 사실로 알려 준 뒤 출력 형식으로 통제한다 - 몇 건을 검토해서 몇 건을 확정했는지는 문서의 신뢰도를 만드는 정보라 작성자가 알아야 한다.
    defects = [
        d
        for d in all_defects
        if not d.get("evidence_suspect") and d.get("deduction_scope") != "excluded"
    ]
    dismissed_cnt = len(all_defects) - len(defects)
    special_notes = state.get("special_notes")
    book_title = state.get("book_title") or "본 도서"
    grade_str = _grade_label(ubci_score)

    if not llm_mini:
        return _fallback_certificate(ubci_score, defects, special_notes)

    defect_brief = [
        {
            "type": d.get("type"),
            "korean_label": DEFECT_TRANSLATION_MAP.get(
                str(d.get("type") or ""), d.get("type")
            ),
            "image_index": d.get("image_index") or 0,
            # Policy가 실제로 적용한 감점. preliminary_deduction은 Vision 단계의 예비값이라 그룹 산정(마모 부위 합산)·Cap·오탐 제외를 전혀 반영하지 않는다 - 보증서에 실제와 다른 감점이 찍히던 원인.
            "deduction": d.get(
                "applied_deduction", d.get("preliminary_deduction") or 0
            ),
            "ratio": d.get("ratio"),
            "text_overlap": d.get("text_overlap"),
        }
        for d in defects
    ]

    # 전건 기각 건은 "무결점"이 아니라 "미확정"이다. 알려주지 않으면 목록이 비어 보여 LLM이 "결함 없음" 톤으로 쓴다.
    unverified = bool(state.get("score_unverified"))
    unverified_block = (
        "\n[중요 - 판정 미확정]\n"
        f"이 도서는 결함 {len(defects)}건이 보고됐으나 증거 대조 검증이 전건을 오탐으로 "
        "지목해 점수의 근거가 확정되지 않았습니다. 무결점·최상급이라는 표현을 절대 쓰지 말고, 관리자 확인이 진행 중이라는 사실을 담담하게 밝히세요. 아래 규칙 1(결함 없음 문구)은 이 경우 적용하지 않습니다.\n"
        if unverified
        else ""
    )

    # ── 반품 처분 안내 (Policy Stage B 결과) ────────────────────────────────
    # [고객 노출 경계 - CRITICAL]
    #  · 귀책(liability)은 넣지 않는다. 표준 운영 정책서 제0장 ④에 따라 AI가 단독 확정하지 않는 값인데, 고객이 받는 보증서에 "고객 귀책"이라고 찍히면 그 문서가 분쟁 증거가 된다.
    #  · chunk_id는 넣지 않는다 (제0조의2 ⑥: 고객 응대 문구에 내부 식별자 노출 금지).
    #  · 관리자 확인이 필요한 건은 확정된 것처럼 쓰지 않는다.
    rp = state.get("return_policy") or {}
    policy_block = ""
    if rp.get("stage_b_status") == "OK" and rp.get("return_accepted") is not None:
        basis = [
            f"{c.get('doc_title')} {c.get('clause_ref')}".strip()
            for c in (rp.get("cited_clauses") or [])
        ]
        policy_block = (
            "\n[반품 처분 판단 - policy_notice / policy_basis 작성용]\n"
            f"- 반품 수용 여부: {'수용' if rp.get('return_accepted') else '수용 불가'}\n"
            f"- 반품 배송비 부담: {rp.get('shipping_fee_bearer') or '미확정'}\n"
            f"- 근거 조항: {', '.join(basis) or '없음'}\n"
            f"- 관리자 확인 필요: {'예' if rp.get('requires_human') else '아니오'}\n"
            "위 내용을 policy_notice에 1~2문장으로 고객 눈높이에 맞게 쓰고, 근거 조항을\n"
            "policy_basis 배열에 그대로 옮기세요. 누구의 잘못인지(귀책)는 쓰지 마세요.\n"
            "관리자 확인이 필요하면 확정된 것처럼 쓰지 말고 검토 중임을 밝히세요.\n"
        )

    prompt = f"""당신은 중고도서 품질 보증서를 쓰는 카피라이터 겸 검수 기록관입니다.
아래 AI 검수 결과를 바탕으로, 실제 구매 고객이 QR로 열어보는 보증서 본문을 작성하세요.
{policy_block}

[검수 결과]
- 도서명: {book_title}
- UBCI 최종 점수: {ubci_score}점 ({grade_str})
- 확정 결함 목록(JSON): {json.dumps(defect_brief, ensure_ascii=False)}
- 정성적 특이사항: {special_notes or "없음"}
- 참고: 정밀 대조에서 결함이 아닌 것으로 판단해 최종 목록에서 뺀 후보 {dismissed_cnt}건이 있습니다. 이 사실은 문서에 쓰지 마세요. 위 목록이 이미 확정분만 담고 있다는 뜻입니다.
{unverified_block}
[작성 규칙]
1. 결함 목록이 완전히 빈 경우에만 condition_detail에 "결함이 없다"는 사실을 명시하되, "해당 없음" 같은 사무적 표현 대신 위트 있게 풀어 쓰세요.
   (예: "샅샅이 뒤졌지만 트집 잡을 곳이 없었습니다" 같은 톤)
   결함이 하나라도 있으면 이 톤을 쓰지 마세요. "결함이 없다는 사실을 찾기 어려웠다" 같은 문장은 있는 결함을 없는 것처럼 흐리는 표현이라 금지합니다.
2. 결함이 있으면 절대 축소하거나 숨기지 말고 정직하게 쓰되, 고객이 불안해지지 않도록 정중하고 위트 있게 포장하세요. findings 배열에 결함별로 1건씩 채우고, 각 항목의 image_index/deduction은 위 JSON 값을 그대로 옮기세요.
3. 과장 광고 금지 - "최고급", "완벽한 신품" 같은 단정적 표현은 쓰지 마세요.
4. 모든 문장은 한국어 존댓말. 이모지는 쓰지 마세요.
5. headline은 20자 내외 한 줄, summary는 2~3문장, condition_detail은 2~4문장.

[출력 형식 - findings 배열]
6. findings는 탐지 건수가 아니라 고객이 이해하는 항목 단위로 씁니다.
   - 위 JSON의 항목 수와 findings 길이를 맞추려 하지 마세요.
   - 같은 label이 여러 번 나오면 부위별로 묶어 한 건으로 쓰세요. 모서리 마모는 한 구석이 여러 컷에 걸쳐 잡히므로 건수를 그대로 세면 실제보다 심해 보입니다.
   - `deduction`이 여러 항목에 같은 값으로 적혀 있으면 그것은 묶음 합계입니다. 묶은 항목에 그 값을 한 번만 쓰고, 항목마다 더하지 마세요.
   - `location`에는 고객이 읽는 위치를 쓰세요 (예: "앞표지 아래쪽 모서리", "책등 하단"). 좌표나 인덱스 표기는 쓰지 않습니다.

7. 내부 검수 용어를 노출하지 마세요. 이 문서는 고객이 읽습니다.
   금지: "오탐", "제외된 항목", "감점 제외", "묶음 산정", "판독", "검증", "BBox",
   "좌표", "인덱스", "재검수", "HITL" 등 내부 처리 과정을 가리키는 표현.
   "또 발견되었습니다", "반복적으로 확인되었습니다" 같이 건수를 세는 표현도 쓰지 마세요.

8. 귀책(누구의 잘못인지)을 쓰지 마세요. "고객 부주의", "고객 과실", "배송사 잘못",
   "물류센터 책임" 같은 표현은 금지입니다. 귀책은 관리자가 증빙을 확인해 확정하는 값이며, 이 문서에 단정적으로 적히면 분쟁의 근거가 됩니다.
   비용 부담 주체는 규정에 따른 안내로만 담담하게 쓰세요.
   위 [반품 처분 판단] 블록이 없으면 policy_notice는 null, policy_basis는 빈 배열로 두세요.
"""

    try:
        structured = llm_mini.with_structured_output(CertificateDocument)
        doc: CertificateDocument = structured.invoke([HumanMessage(content=prompt)])
        result = doc.model_dump()
        # findings는 부위 단위로 묶이므로 결함 개수와 일치할 필요가 없다. 다만 확정 결함이 있는데 비면 결함을 숨긴 문서가 되고, 결함 수보다 많으면 지어낸 것이다.
        findings = result.get("findings") or []
        if defects and (not findings or len(findings) > len(defects)):
            result["findings"] = _fallback_certificate(
                ubci_score, defects, special_notes
            )["findings"]
        elif not defects and findings:
            result["findings"] = []

        # 묶음 감점은 한 번만 표시한다. 같은 라벨·같은 값이 반복되면 첫 항목만 남긴다. (합계가 항목마다 반복되면 고객이 그 수를 더해서 읽는다.)
        seen: set = set()
        for f in result.get("findings") or []:
            key = (f.get("label"), f.get("deduction"))
            if f.get("deduction") and key in seen:
                f["deduction"] = (
                    0  # 같은 묶음의 두 번째 이후 항목 - 합계 중복 표기 방지
                )
            else:
                seen.add(key)

        # 처분 판단이 없거나 관리자 확인 중이면 안내 문구를 지운다. LLM이 빈 블록에도 그럴듯한 문장을 지어내는 경우가 있는데, 보증서에 근거 없는 반품 안내가 실리면 고객이 그것을 확정 통보로 읽는다.
        if not policy_block:
            result["policy_notice"] = None
            result["policy_basis"] = []
        else:
            result["policy_basis"] = _sanitize_policy_basis(result.get("policy_basis"))

        # 귀책 표현이 새어 나갔으면 안내 자체를 내린다.
        notice = result.get("policy_notice") or ""
        if notice and any(w in notice for w in _LIABILITY_WORDS):
            print(
                "[Report Agent] 보증서에서 귀책 표현이 감지되어 반품 안내를 제거합니다."
            )
            result["policy_notice"] = None
            result["policy_basis"] = []
        return result
    except Exception as e:
        print(f"[Report Agent] 보증서 문서 생성 실패, 결정론적 폴백 사용: {e}")
        return _fallback_certificate(ubci_score, defects, special_notes)


# ==========================================
# 4. Report Agent(디지털 WMS 검수 보증서 및 종합 검수 소견 발행)
# ==========================================
# [구조 변경 - 프리즈 예외 승인 (2026-08-04)] auto_refund_agent 노드 제거.
# "MINT 자동 매입/환불"이라는 비즈니스 기능은 auto_refund_eligible 플래그로 보존되어 워커(execute_wms_action)가 집행한다.
def report_agent(state: WMSInspectionState) -> WMSInspectionState:
    ubci_score = state.get("ubci_score")
    if ubci_score is None:
        ubci_score = 100
    cert_id = f"CERT-{now_kst().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    doc = build_certificate_document(state)
    doc["cert_id"] = cert_id
    doc["grade"] = _grade_label(ubci_score)
    doc["ubci_score"] = ubci_score

    # 무결점(MINT) 확정 건은 관리자 개입 없이 자동 매입/환불 대상이 된다. 단, 이 플래그는 등급이 Policy 산정 + Critic 교차검증을 모두 통과한 뒤에만 세워지므로, 예전 Fast-track처럼 검증을 건너뛴 채 금전 결정이 확정되는 일은 없다.
    auto_refund_eligible = bool(state.get("is_mint")) and ubci_score >= 95

    return {
        "ubci_score": ubci_score,
        "final_report": doc["summary"],
        "report_text": doc["summary"],
        "certificate": doc,
        "auto_refund_eligible": auto_refund_eligible,
        "executed_agents": ["report_agent"],
        "messages": [
            AIMessage(
                content=f"[Report Agent] 디지털 품질 보증서 발행 완료 ({cert_id}) - {doc['headline']}"
            )
        ],
    }


def human_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    HITL 인계 지점(handoff station).
    이 노드는 스스로 판단하지 않는다 - Supervisor가 3개 에이전트 보고를 종합해 "ESCALATE_HUMAN"을 결정했을 때만 도달하며, 여기서는 그 지시를 집행해 작업을 관리자 결재 대기 상태로 표시하고 그래프를 종료한다(app/ai/supervisor.py에서 human_node -> END).
    이후 실제 재개는 관리자가 POST /admin/hitl/override로 처리한다.
    """
    rationale = state.get("supervisor_rationale") or "판정 애매성으로 관리자 검토 필요"
    print(f"[Agent] Human Node (HITL): Supervisor 이관 지시 집행 - {rationale}")
    return {
        "reason_code": "AWAITING_HUMAN_REVIEW",
        "messages": [
            AIMessage(
                content=f"[Human Node (HITL)] 관리자 수동 검수 대기 (HITL_REQUIRED) - {rationale}"
            )
        ],
    }
