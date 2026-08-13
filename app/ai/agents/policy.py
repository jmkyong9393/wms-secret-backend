"""
Policy Agent - 2단 구조.

  · Stage A (LLM 없음) : UBCI 감점 매트릭스로 점수·등급을 확정한다.
                         수치의 정본은 app/core/ubci_matrix.py다.
  · Stage B (GPT-4o-mini + RAG) : 반품 수용·배송비 부담·귀책 후보를 판단한다.

[경계 - CRITICAL]
Stage B는 점수·등급에 쓰지 않는다. ReturnPolicyVerdict 스키마에 점수 필드가 없고, 호출은 점수 확정 뒤에 일어나며, 결과는 return_policy 키에만 담긴다.
매입가를 정하는 값에 LLM이 개입하면 재현성과 감사 추적성이 깨진다.
"""
import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage

from app.ai.state import WMSInspectionState
from app.core import ubci_matrix as UM
from app.ai.agents.common import DEFECT_TRANSLATION_MAP
from app.ai.agents.llm import llm_mini
from app.ai.agents.schemas import ReturnPolicyVerdict

def _effective_ratio(d: Dict[str, Any], default: int = 5) -> int:
    """감점 구간 판정에 쓸 면적비(%)를 돌려준다. VLM이 비우면 **BBox 면적에서 유도**한다.

    [배경] VLM이 `ratio`를 0으로 내려보내는 경우가 실측으로 확인됐다(LPN-260806-A001, 결함 5건 전부 ratio=0). 면적 기반 3단계 구간(마모·오염·찢어짐·긁힘 등)은 이 값이 0이면 전부 최하위 구간으로 떨어져 심각도 차등이 통째로 무력화된다.

    좌표는 이미 있으므로 계산할 수 있다. BBox는 0~1000 정규화이므로 넓이비는 (w/1000)x(h/1000)이고, 이는 "결함이 이미지에서 차지하는 면적 비율"이라는 UBCI 규정의 정의와 같은 축이다. 
    실제 손상부는 박스보다 작을 수 있으나 단조(마모가 클수록 박스도 크다)하므로 구간 판정 목적에는 충분하다.
    좌표조차 없으면 근거가 없으므로 기존 기본값(5%)을 유지한다 - 없는 값을 크게 잡아 감점을 부풀리지 않는다.
    """
    try:
        r = int(d.get("ratio") or 0)
    except (TypeError, ValueError):
        r = 0
    if r > 0:
        return r

    bbox = d.get("bbox") or {}
    try:
        w = (int(bbox["xmax"]) - int(bbox["xmin"])) / 1000.0
        h = (int(bbox["ymax"]) - int(bbox["ymin"])) / 1000.0
    except (KeyError, TypeError, ValueError):
        return default
    if w <= 0 or h <= 0:
        return default

    # 감사 추적: 이 값이 VLM 보고가 아니라 좌표에서 유도된 것임을 남긴다.
    d["ratio_source"] = "bbox"
    return max(1, round(w * h * 100))


def _edge_wear_profile(defects: List[Dict[str, Any]]) -> tuple[set, int]:
    """모서리 마모를 **책 기준 모서리 위치**로 묶어 (위치 집합, 최대 면적비)를 돌려준다.

    [왜 건수로 세지 않는가]
    한 권에는 모서리가 여러 개고 보통 함께 닳는다. 게다가 같은 구석이 앞표지 컷·뒤표지 컷·책등 컷에 모두 잡히므로, 검출 건수로 감점하면 촬영 각도와 컷 수에 따라 같은 책이 다른 점수를 받는다(재현성 붕괴). 그래서 "서로 다른 모서리 몇 곳이 닳았는가"로 센다.

    [왜 image_index로 묶지 않는가]
    컷 번호는 물리적 면이 아니다. 앞표지를 찍을 때 책등 쪽 모서리가 같이 보이고, 그 다음 책등 컷에서 같은 모서리가 다시 잡힌다.
    컷으로 세면 한 곳을 두 곳으로 세어 이중 감점된다.

    [어떻게 공간으로 계산하는가]
    BBox는 컷마다 독립 좌표계(0~1000)라 컷을 가로질러 직접 겹쳐볼 수 없다. 대신 각 박스를 책을 정면에서 본 좌표계(book frame)의 모서리 라벨로 환산해 집합으로 만든다.
    - 세로: 중심 y < 33% → TOP, > 67% → BOTTOM, 그 사이 → MID
    - 가로: 앞표지(0)는 화면 좌우 그대로, 뒤표지(1)는 좌우를 뒤집는다
    (책을 돌려 찍으므로 실제 같은 쪽 모서리가 화면에서는 반대편에 온다.)
    - 책등(2)은 좁은 띠라 가로 위치가 의미 없다. 세로 위치만 확정된 와일드카드로 두고, 같은 세로 구간에 이미 표지 모서리가 있으면 그것과 동일한 곳으로 간주해 합친다.
    - 그 외 컷(속지·책배 등)은 표지와 좌표 기준이 달라 섞으면 오히려 왜곡되므로, 가로를 판정하지 않고 책등과 같은 와일드카드 규칙을 따른다.

    한국 단행본은 좌철(제본이 왼쪽)이 일반적이라는 관례를 쓰지 않는다. 좌/우를 그대로 쓰되 뒤표지만 반전하므로, 제본 방향을 몰라도 앞뒤 대응이 맞는다.
    """
    corners: set = set()
    wildcard_rows: set = set()   # 가로를 알 수 없는 검출의 세로 구간
    max_ratio = 0

    for d in defects:
        dtype = str(d.get("type", "") or d.get("label", ""))
        if not ("WEAR" in dtype or "마모" in dtype):
            continue
        if d.get("evidence_suspect"):     # 오탐 지목분은 위치 계산에서도 뺀다
            continue

        max_ratio = max(max_ratio, _effective_ratio(d, default=0))

        bbox = d.get("bbox") or {}
        try:
            cy = (int(bbox["ymin"]) + int(bbox["ymax"])) / 2
            cx = (int(bbox["xmin"]) + int(bbox["xmax"])) / 2
        except (KeyError, TypeError, ValueError):
            # 좌표가 없으면 위치를 특정할 수 없다. 새 모서리로 세면 근거 없이 감점이 늘어나므로 와일드카드(중앙 행)로 처리해 기존 모서리에 흡수시킨다.
            wildcard_rows.add("MID")
            continue

        row = "TOP" if cy < 333 else ("BOTTOM" if cy > 667 else "MID")
        idx = d.get("image_index")

        if idx == 0:                      # 앞표지 - 화면 좌우 그대로
            col = "L" if cx < 500 else "R"
        elif idx == 1:                    # 뒤표지 - 좌우 반전
            col = "R" if cx < 500 else "L"
        else:                             # 책등·기타 컷 - 가로 판정 불가
            wildcard_rows.add(row)
            continue

        corners.add((row, col))

    # 와일드카드는 같은 세로 구간에 표지 모서리가 이미 있으면 그것과 같은 곳으로 본다. 없을 때만 독립된 한 곳으로 계산한다 (책등만 닳은 경우).
    for row in wildcard_rows:
        if not any(r == row for r, _ in corners):
            corners.add((row, "SIDE"))

    return corners, max_ratio



#   Statute  = 법령 - 계약 여부와 무관하게 모두에게 적용된다
# 타사 공개 약관(Contract)·운영정책(Policy)·가이드(Guideline)는 리서치로 수집한 자료이지 우리가 체결한 계약이 아니므로 판정 근거가 아니라 설계 참고 자료다.
# 실제 고객사 계약이 체결되면 그 계약서를 Contract로 등재하고 이 집합에 추가한다.
BINDING_AUTHORITY = {"Internal", "Statute"}


def evaluate_return_policy(
    book_title: str,
    defects: List[Dict[str, Any]],
    platform: str = "",
    return_reason: str = "",
) -> Optional[Dict[str, Any]]:
    """
    [Policy Agent Stage B] 거래 처분 판단 - 반품 수용 / 배송비 부담 / 귀책 후보.

    감점 매트릭스(Stage A)로 환원되지 않는 규정 해석을 담당한다. 지식베이스 71청크 중 중고 검수 관련은 6건뿐이고 나머지 90%가 환불(38)·반품(33)·배송(39)·취소(13) 같은 거래 처분 규정이다 - 이 데이터가 실제로 쓰이는 자리가 여기다.

    [fail-safe - Stage A와 반대다]
    Stage A의 근거 조항 인용은 실패해도 조용히 넘어간다(fail-open). 점수가 이미 확정돼 있어 인용이 없어도 판정이 성립하기 때문이다. Stage B는 반대로 fail-safe다.
    규정 근거를 찾지 못했는데 "반품 수용"을 임의로 정하면 금전적 확정이 근거 없이 실행된다. 그래서 다음 경우 전부 requires_human=True로 사람에게 넘긴다.
    - RAG 검색 결과 0건 (표준 운영 정책서 제0조의2 ⑤)
    - LLM 호출 실패
    - 모델이 인용 없이 결론만 낸 경우
    반환: verdict dict, 또는 판단 자체를 시도할 수 없으면 None.
    """
    from app.core.rag_service import search_policy

    defect_types = sorted({str(d.get("type") or "") for d in (defects or []) if d.get("type")})
    query = " ".join(filter(None, [
        platform,
        return_reason or "중고 도서 반품 수용 기준",
        "반품 가능 여부 배송비 부담 환불 처리 귀책 판단",
        " ".join(defect_types),
    ])).strip()

    # 플랫폼(테넌트)이 특정되면 해당 플랫폼 규정과 공통 규정을 함께 본다.
    # 표준 운영 정책서 제0조의2 ②·③.
    where = {"platform": {"$in": [platform, "Common"]}} if platform else None
    clauses = search_policy(query, k=8, where=where)
    if not clauses and where:
        clauses = search_policy(query, k=8)   # 플랫폼 규정이 없으면 전체에서 다시

    # ── 직접 판정 근거 / 참고 자료 분리 (표준 운영 정책서 제0조의1 ③) ──────────
    # "외부 플랫폼 리서치 데이터뿐이고 고객사 계약 또는 법령 근거가 확인되지 않은 경우, 해당 조항은 직접 판정 근거가 아니라 WMS 기본 정책 설계 참고 근거로만 사용한다."
    # 지식베이스의 교보·YES24·쿠팡 약관은 공개 약관을 리서치로 수집한 것이지 우리가 체결한 계약이 아니다. 따라서 판정은 우리 규정(Internal)과 법령(Statute)으로만 하고, 타사 약관은 업계 관행을 이해하는 맥락으로만 모델에 준다.
    # (실제 고객사 계약이 체결되면 그 계약을 Contract로 등재하고 이 집합에 추가한다)
    binding = [c for c in clauses if c.get("authority_level") in BINDING_AUTHORITY]
    reference = [c for c in clauses if c.get("authority_level") not in BINDING_AUTHORITY]

    # 우리 규정·법령에서 근거를 찾지 못하면 판단하지 않는다. 남의 약관으로 우리 처분을 정하지 않는다.
    if not binding:
        return {
            "return_accepted": None,
            "shipping_fee_bearer": None,
            "liability": "UNDETERMINED",
            "refund_ratio": None,
            "cited_clauses": [],
            "reference_clauses": [
                {k: c.get(k) for k in ("doc_title", "clause_ref", "authority_level")}
                for c in reference
            ],
            "requires_human": True,
            "rationale": (
                "우리 표준 운영 정책서와 법령에서 적용할 조항을 찾지 못해 자동 처분을 보류하고 "
                "관리자 결재로 이관합니다. (타사 약관은 표준 운영 정책서 제0조의1 ③에 따라 "
                "직접 판정 근거로 쓰지 않습니다)"
            ),
            "stage_b_status": "NO_BINDING_CLAUSE",
        }

    if not llm_mini:
        return {
            "return_accepted": None, "shipping_fee_bearer": None,
            "liability": "UNDETERMINED", "refund_ratio": None,
            # 실패 경로에서도 타사 약관은 인용에 넣지 않는다 (제0조의1 ③).
            "cited_clauses": [
                {k: c.get(k) for k in ("chunk_id", "doc_title", "clause_ref", "authority_rank")}
                for c in binding
            ],
            "reference_clauses": [
                {k: c.get(k) for k in ("doc_title", "clause_ref", "authority_level")}
                for c in reference
            ],
            "requires_human": True,
            "rationale": "규정 조항은 조회했으나 판단 모델을 사용할 수 없어 관리자 결재로 이관합니다.",
            "stage_b_status": "LLM_UNAVAILABLE",
        }

    clause_block = json.dumps(
        [{k: c.get(k) for k in ("chunk_id", "doc_title", "clause_ref", "authority_rank", "content")}
         for c in binding],
        ensure_ascii=False, indent=1,
    )
    reference_block = json.dumps(
        [{k: c.get(k) for k in ("doc_title", "clause_ref", "content")} for c in reference],
        ensure_ascii=False, indent=1,
    ) if reference else "(없음)"
    defect_block = json.dumps(
        [{"type": d.get("type"), "label": DEFECT_TRANSLATION_MAP.get(str(d.get("type")), ""),
          "description": d.get("description")} for d in (defects or [])],
        ensure_ascii=False, indent=1,
    )

    prompt = f"""당신은 중고도서 물류센터의 반품 정책 심사관입니다.
아래 규정 조항만을 근거로 이 건의 거래 처분을 판단하세요.

[중요 - 당신이 판단하지 않는 것]
도서의 상태 점수(UBCI)와 등급은 이미 별도의 결정론적 산식이 확정했습니다.
점수·등급·매입가는 당신의 판단 대상이 아니며 언급하지 마세요.
당신이 정할 것은 반품 수용 여부, 배송비 부담 주체, 귀책 후보뿐입니다.

[검수 건]
- 도서명: {book_title or "미상"}
- 판매 플랫폼: {platform or "미상"}
- 반품 사유: {return_reason or "미기재"}
- 검출된 결함:
{defect_block}

[직접 판정 근거 — 우리 표준 운영 정책서와 법령. 오직 여기서만 결론을 도출하세요]
{clause_block}

[업계 참고 자료 — 타사 공개 약관. 맥락 이해용이며 판정 근거로 인용 금지]
{reference_block}

[판단 규칙]
1. 결론은 [직접 판정 근거]에서만 도출하세요. [업계 참고 자료]의 타사 약관은 우리가 체결한 계약이 아니라 리서치로 수집한 공개 자료입니다. 표준 운영 정책서 제0조의1 ③에 따라 직접 판정 근거로 쓸 수 없습니다. cited_clauses에 넣지 마세요.
2. [직접 판정 근거]에 없는 내용을 근거로 삼지 마세요. 조항을 지어내지 마세요.
3. 근거가 불충분한 항목은 값을 비우고(null) requires_human을 true로 두세요. "아마도"로 채우지 마세요 - 금전이 걸린 판단입니다.
4. 귀책(liability)은 증빙 2개 이상이 서로 일치할 때만 특정하고, 그렇지 않으면 반드시 UNDETERMINED로 두세요. AI는 귀책을 단독 확정하지 않습니다.
5. 귀책이 UNDETERMINED이면 shipping_fee_bearer를 CUSTOMER로 두지 마세요. 표준 운영 정책서 제16조의3 ②: "귀책이 불명확한 상태에서 고객에게 배송비 또는 반품비를 자동 차감하지 않는다." 이 경우 null로 두고 관리자 결재로 넘기세요.
6. authority_rank가 낮은 조항(1=법령)이 높은 조항(5=내부 기준)과 충돌하면 법령을 따르세요.
7. cited_clauses에는 실제로 판단 근거로 쓴 조항만 넣으세요.
8. rationale은 2~4문장 한국어로, 인용한 조항명을 문장 안에 밝혀 쓰세요.
"""

    try:
        structured = llm_mini.with_structured_output(ReturnPolicyVerdict)
        verdict: ReturnPolicyVerdict = structured.invoke(prompt)
    except Exception as e:
        print(f"[Policy Stage B] 처분 판단 실패({e}) - 관리자 결재로 이관합니다.")
        return {
            "return_accepted": None, "shipping_fee_bearer": None,
            "liability": "UNDETERMINED", "refund_ratio": None,
            # 실패 경로에서도 타사 약관은 인용에 넣지 않는다 (제0조의1 ③).
            "cited_clauses": [
                {k: c.get(k) for k in ("chunk_id", "doc_title", "clause_ref", "authority_rank")}
                for c in binding
            ],
            "reference_clauses": [
                {k: c.get(k) for k in ("doc_title", "clause_ref", "authority_level")}
                for c in reference
            ],
            "requires_human": True,
            "rationale": "처분 판단 중 오류가 발생해 관리자 결재로 이관합니다.",
            "stage_b_status": "LLM_ERROR",
        }

    out = verdict.model_dump()
    out["reference_clauses"] = [
        {k: c.get(k) for k in ("doc_title", "clause_ref", "authority_level")} for c in reference
    ]
    # 타사 약관을 판정 근거로 인용했으면 걷어낸다 (제0조의1 ③). 프롬프트로 금지했지만 모델이 지키지 않을 수 있으므로 코드로 거른다.
    binding_ids = {c.get("chunk_id") for c in binding}
    dropped = [c for c in (out.get("cited_clauses") or []) if c.get("chunk_id") not in binding_ids]
    if dropped:
        out["cited_clauses"] = [
            c for c in out["cited_clauses"] if c.get("chunk_id") in binding_ids
        ]
        print(f"[Policy Stage B] 타사 약관 인용 {len(dropped)}건 제거 (제0조의1 ③)")

    # 표준 운영 정책서 제16조의3 ②: 귀책이 불명확한 상태에서 고객에게 배송비·반품비를 자동 차감하지 않는다. 모델이 두 값을 모순되게 채우면 여기서 바로잡는다.
    if out.get("liability") == "UNDETERMINED" and out.get("shipping_fee_bearer") == "CUSTOMER":
        out["shipping_fee_bearer"] = None
        out["requires_human"] = True
        out["rationale"] = ((out.get("rationale") or "").strip() + " (귀책이 확정되지 않아 고객 비용 부담을 자동 적용하지 않습니다 — 제16조의3 ②)").strip()

    # 모델이 인용 없이 결론만 낸 경우를 코드로 막는다. 프롬프트로 부탁하는 것만으로는 보장되지 않으므로, 인용이 비었으면 결론을 무효화하고 사람에게 넘긴다.
    if not out.get("cited_clauses"):
        out.update({
            "return_accepted": None,
            "shipping_fee_bearer": None,
            "refund_ratio": None,
            "requires_human": True,
            "stage_b_status": "UNCITED_VERDICT",
        })
        out["rationale"] = (
            "규정 조항 인용 없이 결론이 산출되어 자동 처분을 보류합니다. " + (out.get("rationale") or "")).strip()
    else:
        out["stage_b_status"] = "OK"
    # 귀책 미확정이면 사람 결재가 필요하다 (제0장 ④).
    if out.get("liability") == "UNDETERMINED":
        out["requires_human"] = True

    return out


def policy_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Policy Agent: UBCI v2.0.0.0 공식 감점 매트릭스 & 텍스트 침범 가중치 적용 연산 중...")

    # Vision Agent가 판독에 실패한 건은 점수를 산출하지 않는다. 결함 목록이 비어 있다는 사실이 "무결점"을 뜻하지 않기 때문에, 여기서 100점을 매기면 판독 실패가 그대로 최고 등급 자동 승인으로 이어진다. ubci_score를 None으로 남겨두면 Critic이 재검수(최대 2회) 후 HITL 이관까지 기존 루프로 처리한다.
    if state.get("vision_failed"):
        skip_text = "Vision 판독 실패로 UBCI 점수 산출을 보류합니다 (재검수 루프로 회부)."
        print(f"[Agent] Policy Agent: {skip_text}")
        return {
            "ubci_score": None,
            "policy_text": skip_text,
            "executed_agents": ["policy_agent"],
            "messages": [AIMessage(content=f"[Policy Agent] {skip_text}")],
        }

    defects = state.get("defects") or []
    book_title = str(state.get("book_title") or state.get("title") or "")
    book_category = str(state.get("book_category") or "")

    # 제목 키워드 목록이 "수험서/문제집/기출/자격검정/실전문제/학습/교재/AIVLE/SQL" 9개뿐이라, 실습문제가 실린 프로그래밍 입문서 다수가 걸리지 않았다.
    # (실측: "쉽게 풀어쓴 C언어 Express" - 손글씨 문제풀이 12건이 Cap 없이 건당 -10점씩 누적되어 REJECT 40점까지 떨어짐). 카탈로그 전수 조사 결과 "Do it!", "혼자 공부하는", "이것이 취업을 위한 코딩 테스트다" 등도 동일하게 누락돼 있었다.
    # 키워드를 넓히고, category_type("컴퓨터/모바일" 등)을 2차 신호로 추가한다.
    # 이 Cap은 DMG_INT_DOODLE(낙서)에만 적용되므로 오탐(비문제집을 문제집으로 오판)의 대가는 "낙서 감점이 15점에서 멈춘다" 정도이고, 누락의 대가(매입가 부당 하락)보다 훨씬 가볍다 - 넓게 잡는 쪽이 안전하다.
    _WORKBOOK_TITLE_KEYWORDS = [
        "수험서", "문제집", "기출", "자격검정", "실전문제", "학습", "교재", "AIVLE", "SQL",
        "입문", "실습", "자습서", "코딩", "프로그래밍", "알고리즘", "예제", "테스트", "인터뷰",
        "워크북", "연습", "풀이", "Do it", "혼자 공부하는", "풀어쓴", "with 클로드", "with 코드",
    ]
    _WORKBOOK_CATEGORY_KEYWORDS = ["컴퓨터", "IT", "프로그래밍", "자격증", "수험서"]
    is_workbook = (
        any(k in book_title for k in _WORKBOOK_TITLE_KEYWORDS)
        or any(k in book_category for k in _WORKBOOK_CATEGORY_KEYWORDS)
    )

    # 모서리 마모는 건수가 아니라 "책의 서로 다른 모서리 몇 곳이 닳았는가"로 센다.
    # (같은 구석이 앞표지·뒤표지·책등 컷에 모두 잡히면 물리적으로는 한 곳이다)
    wear_corners, wear_max_ratio = _edge_wear_profile(defects)

    deduction_items = []
    total_deduction = 0
    suspect_excluded = []  # 증거 대조 검증이 오탐으로 지목해 감점에서 제외한 항목
    is_fatal_reject = False
    fatal_reason = ""
    edge_wear_added = False
    doodle_workbook_added = False
    wear_group_ded = wear_group_spread = 0
    wear_group_sev = wear_group_ratio = None

    for d in defects:
        dtype = str(d.get("type", "") or d.get("label", ""))
        ratio = _effective_ratio(d)   # VLM이 비우면 BBox 면적에서 유도
        page_cnt = d.get("page_count") or d.get("pages") or 1
        text_overlap = d.get("text_overlap", False) or "본문" in str(d.get("description", ""))
        label = DEFECT_TRANSLATION_MAP.get(dtype) or dtype or "상태 결함"

        # 증거 대조 검증(verify_defects_with_images)이 오탐으로 지목한 결함은 감점하지 않는다. 목록에서는 지우지 않으므로 HITL 화면과 BBox 오버레이에는 그대로 보이고, 다만 매입가를 좌우하는 점수에는 반영하지 않는다 - 판독이 증거와 어긋난다고 판정된 항목으로 판매자에게 불이익을 주지 않기 위함이다.
        # 전부 오탐으로 걸러져 감점이 0이 되면 score가 100이 되는데, 그 경우는 critic_agent Stage A의 "결함 N건인데 감점 0점" 정합성 검사가 잡아 HITL로 이관한다. (프리즈 규정: "검수하지 못했다"와 "흠이 없다"를 같게 취급하지 않는다)
        if d.get("evidence_suspect"):
            suspect_excluded.append(label)
            d["applied_deduction"] = 0
            d["deduction_scope"] = "excluded"
            d["deduction_note"] = "증거 대조 검증이 오탐으로 지목 - 감점 제외"
            continue

        # 🚨 치명적 결함 즉시 반려 (UBCI Spec Section 1 & Section 4)
        if "WET" in dtype or "WATER" in dtype or "WARPING" in dtype or "침수" in dtype or "휨" in dtype:
            is_fatal_reject = True
            fatal_reason = (
                "🚨 액체 오염(Water Stain) 또는 페이지 휨(Warping) 감지 ➔ 즉시 반려(REJECT) "
                f"(표준 운영 정책서 {UM.REJECT_CLAUSE_WET})"
            )
            deduction_items.append((label, 100, f"{label} (치명적 결함 ➔ 즉시 반려)"))
            break

        if "WEAR" in dtype or "마모" in dtype:
            # 마모는 부위 단위 그룹 감점이다. 개별 BBox에 쪼개 붙이면 화면에서 건당
            # 감점처럼 읽히므로 그룹임을 명시한다.
            d["deduction_scope"] = "group"
            d["deduction_group"] = "EDGE_WEAR"
            if not edge_wear_added:
                # 심각도(최대 면적비) + 확산도(서로 다른 모서리 수)로 산정하고 총 -15점 Cap.
                # 종전에는 상태와 무관하게 -5 단일 고정이라, 살짝 닳은 책과 헤질 정도로 닳은
                # 책이 같은 점수를 받았고 모서리 마모만으로는 어떤 경우에도 S급(>=95)을
                # 벗어날 수 없었다(=95점 고정). 등급이 매입가를 결정하므로 차등이 필요하다.
                base_ded = UM.EDGE_WEAR.tier_for(wear_max_ratio)
                spread = max(1, len(wear_corners))
                spread_ded = (spread - 1) * UM.EDGE_WEAR_SPREAD_STEP
                wear_ded = min(UM.EDGE_WEAR_CAP, base_ded + spread_ded)

                sev = "경미" if base_ded == UM.EDGE_WEAR.minor else ("보통" if base_ded == UM.EDGE_WEAR.moderate else "심함")
                detail = f"모서리 마모 (-{wear_ded}점, {sev} 면적 {wear_max_ratio}% / 마모 부위 {spread}곳"
                detail += f", 총 -{UM.EDGE_WEAR_CAP}점 Cap 적용" if base_ded + spread_ded > UM.EDGE_WEAR_CAP else ""
                detail += f", {UM.EDGE_WEAR.clause})"

                deduction_items.append((label, wear_ded, detail))
                total_deduction += wear_ded
                edge_wear_added = True
                wear_group_ded, wear_group_spread = wear_ded, spread
                wear_group_sev, wear_group_ratio = sev, wear_max_ratio
        elif "DOODLE" in dtype or "필기" in dtype or "낙서" in dtype:
            if is_workbook:
                cap = UM.WORKBOOK_DOODLE_CAP
                d["deduction_scope"] = "group"
                d["deduction_group"] = "WORKBOOK_DOODLE"
                d["applied_deduction"] = cap
                d["deduction_note"] = f"수험서/문제집 전체 필기 -{cap}점 단일 Cap (건별 합산 아님)"
                if not doodle_workbook_added:
                    deduction_items.append((
                        label, cap,
                        f"수험서/문제집 도서 전체 필기/낙서 (-{cap}점 단일 고정 Cap, {UM.WORKBOOK_DOODLE.clause})",
                    ))
                    total_deduction += cap
                    doodle_workbook_added = True
            else:
                base_ded = UM.DOODLE.severe if page_cnt > UM.DOODLE_PAGE_THRESHOLD else UM.DOODLE.minor
                multiplier = UM.TEXT_OVERLAP_MULTIPLIER if text_overlap else 1.0
                final_ded = int(base_ded * multiplier)
                total_deduction += final_ded
                overlap_str = f" (본문 텍스트 침범 x{UM.TEXT_OVERLAP_MULTIPLIER} 가중치)" if text_overlap else ""
                deduction_items.append((label, final_ded, f"{label} (-{final_ded}점{overlap_str}, {UM.DOODLE.clause})"))
                d["applied_deduction"] = final_ded
                d["deduction_scope"] = "single"
                d["deduction_note"] = (
                    f"{'페이지 %d장 초과' % UM.DOODLE_PAGE_THRESHOLD if page_cnt > UM.DOODLE_PAGE_THRESHOLD else '페이지 %d장 이하' % UM.DOODLE_PAGE_THRESHOLD}"
                    f"{overlap_str}"
                )
        # --- 오염(STAIN) : 면적 기준 3단계 ---
        # 국소적 결함이라 넓을수록 심각하므로 기존 매트릭스와 동일하게 ratio를 축으로 쓴다.
        # doodle 분기와 같은 패턴으로 여기서 직접 append하고 아래 공용 1.5배 가중치를 타지 않는다 - 면적 구간에서 이미 심각도를 반영하므로 이중 가중이 된다(-20 x 1.5 = -30).
        elif "STAIN" in dtype or "오염" in dtype or "얼룩" in dtype:
            base_ded = UM.STAIN.tier_for(ratio)
            total_deduction += base_ded
            deduction_items.append((label, base_ded, f"{label} (-{base_ded}점, 면적 {ratio}%, {UM.STAIN.clause})"))
            d["applied_deduction"] = base_ded
            d["deduction_scope"] = "single"
            d["deduction_note"] = f"면적 {ratio}% 구간"

        # --- 변색/황변(DISCOLOR) : 강도 기준 3단계 ---
        # 황변은 페이지 전면에 나타나므로 ratio가 항상 ~100%가 되어 면적이 의미를 갖지 못한다. (면적으로 재면 세월 먹은 정상적인 헌책이 매번 최고 감점을 맞는다.)
        # VLM이 level 1~3으로 강도를 보고하며, 중고책의 자연 노화를 불량으로 폐기하지 않도록 L1~L2는 등급에 영향을 주지 않는 수준으로 관대하게 설계했다.
        # 이 타입도 text_overlap 가중치에서 제외한다 - 전면적 결함이라 가중치가 항상 발동해 차등 기능을 하지 못하고 모든 변색 감점을 1.5배로 부풀리기만 한다.
        elif "DISCOLOR" in dtype or "변색" in dtype or "황변" in dtype:
            level = d.get("level")
            try:
                level = int(level)
            except (TypeError, ValueError):
                level = 1  # 강도 미보고 시 가장 관대한 단계로 처리
            level = min(3, max(1, level))
            base_ded = UM.DISCOLOR_BY_LEVEL[level]
            total_deduction += base_ded
            deduction_items.append((label, base_ded, f"{label} (-{base_ded}점, 강도 L{level}, {UM.DISCOLOR.clause})"))
            d["applied_deduction"] = base_ded
            d["deduction_scope"] = "single"
            d["deduction_note"] = f"황변 강도 L{level} (면적 아님)"

        else:
            if "SCRATCH" in dtype or "긁힘" in dtype or "스크래치" in dtype:
                rule = UM.SCRATCH
            elif "TEAR" in dtype or "찢어짐" in dtype or "찢김" in dtype:
                rule = UM.TEAR
            elif "STICKER" in dtype or "스티커" in dtype:
                rule = UM.STICKER
            elif "CRUSH" in dtype or "찍힘" in dtype or "구겨짐" in dtype or "찌그러짐" in dtype:
                rule = UM.CRUSH
            elif "SPINE" in dtype or "갈라짐" in dtype:
                rule = UM.SPINE_CRACK
            elif "BINDING" in dtype or "제본" in dtype:
                if ratio >= UM.BINDING_FATAL_RATIO:
                    is_fatal_reject = True
                    fatal_reason = f"🚨 제본 완전 벌어짐 ➔ 즉시 반려(REJECT) ({UM.REJECT_CLAUSE_BINDING})"
                    break
                rule = UM.BINDING_LOOSE
            elif "SIGNATURE" in dtype or "서명" in dtype or "이름" in dtype:
                rule = UM.SIGNATURE
            elif "STAMP" in dtype or "도장" in dtype:
                rule = UM.STAMP
            else:
                rule = UM.DEFAULT

            base_ded = rule.tier_for(ratio)
            multiplier = UM.TEXT_OVERLAP_MULTIPLIER if (text_overlap and rule.text_overlap_weighted) else 1.0
            final_ded = int(base_ded * multiplier)
            total_deduction += final_ded
            overlap_str = f" (본문 텍스트 침범 x{UM.TEXT_OVERLAP_MULTIPLIER} 가중치)" if multiplier != 1.0 else ""
            deduction_items.append((label, final_ded, f"{label} (-{final_ded}점{overlap_str}, {rule.clause})"))
            d["applied_deduction"] = final_ded
            d["deduction_scope"] = "single"
            d["deduction_note"] = f"면적 {ratio}% 구간{overlap_str}"

    # 마모 그룹 감점을 소속 결함 전체에 동일하게 새긴다. 건별 합산이 아니라는 사실을 화면이 그대로 읽을 수 있어야 한다. (오버레이가 지어내지 않도록 값과 문구를 함께 준다).
    if edge_wear_added:
        for d in defects:
            if d.get("deduction_group") != "EDGE_WEAR" or d.get("deduction_scope") == "excluded":
                continue
            d["applied_deduction"] = wear_group_ded
            d["deduction_note"] = (
                f"모서리 마모 {wear_group_spread}곳 합산 -{wear_group_ded}점 ({wear_group_sev} 면적 {wear_group_ratio}%) - 건별 합산 아님"
            )

    score_unverified = False
    if is_fatal_reject:
        score = 0
        grade_str = "REJECT C급 (폐기)"
        decision_str = "REJECT"
        policy_text = f"UBCI v2.0.0.0 사내 수석 룰 적용 ➔ {fatal_reason}"
    else:
        score = max(0, min(UM.BASE_SCORE, UM.BASE_SCORE - total_deduction))
        grade_str = UM.grade_for(score)
        decision_str = UM.decision_for(score)

        # 검증이 판독을 전부 기각해 감점 근거가 남지 않은 상태는 "흠이 없다"가 아니라 "판독하지 못했다"이므로 무결점 등급을 주지 않고 판정을 보류한다.
        score_unverified = bool(defects) and total_deduction == 0 and bool(suspect_excluded)
        if score_unverified: # noqa: SIM102 - 아래 분기들이 이 플래그를 함께 읽는다
            grade_str = "판정 보류 (증거 대조 전건 반려)"
            decision_str = "HITL"

        if deduction_items:
            deduction_str = " + ".join([item[2] for item in deduction_items])
            policy_text = f"UBCI v2.0.0.0 공식 매트릭스 적용 ➔ {deduction_str} = 총 {total_deduction}점 감점 (UBCI {score}점 / {grade_str} / 처분: {decision_str})"
        elif score_unverified:
            # "결함 없음"이라고 쓰면 안 된다 - 결함은 보고됐고 검증이 그것을 기각했을 뿐이다.
            policy_text = (
                f"UBCI v2.0.0.0 공식 매트릭스 적용 ➔ 보고된 결함 {len(defects)}건이 증거 대조 "
                f"검증에서 **전건** 오탐으로 지목되어 감점 근거가 남지 않았습니다 "
                f"(UBCI 산출 불가 / {grade_str} / 처분: {decision_str})"
            )
        else:
            policy_text = f"UBCI v2.0.0.0 공식 매트릭스 적용 ➔ 결함 없음 (UBCI {score}점 / {grade_str} / 처분: {decision_str})"
        # 감점에서 제외된 항목은 감사 추적을 위해 반드시 남긴다. 기록하지 않으면 "Vision은 결함을 보고했는데 Policy가 조용히 무시한" 것처럼 보인다.
        if suspect_excluded:
            policy_text += (
                f" / 증거 대조 검증에서 오탐으로 지목되어 감점 제외: "
                f"{', '.join(suspect_excluded)} ({len(suspect_excluded)}건)"
            )
            # 같은 유형이 이미 감점에 반영돼 있으면(Cap·묶음 산정 타입) 제외해도 총점은 그대로다.
            # 그 사실을 밝히지 않으면 HITL 검수자와 보증서 독자가 "오탐을 빼서 점수가 올라갔다"고 잘못 읽는다.
            scored_labels = {item[0] for item in deduction_items}
            if all(lb in scored_labels for lb in suspect_excluded):
                policy_text += " (동일 유형이 이미 감점에 반영되어 총점 변동 없음)"

    # --- RAG 근거 조항 인용 (Grounding) ---
    # [중요] 점수(score)는 위에서 이미 결정론적 산식으로 확정됐다. 아래 검색은 그 감점의 출처를 규정집에서 찾아 붙이기만 하며, 어떤 경우에도 score를 바꾸지 않는다.
    # 검색 결과가 점수에 영향을 주면 같은 도서가 실행할 때마다 다른 등급을 받게 되어 UBCI 등급의 재현성과 감사 추적성이 깨진다 (등급은 매입가를 결정하는 값이다).
    # RAG 서버가 죽어 있거나 인덱스가 없으면 조용히 빈 목록을 반환한다(fail-open).
    deduction_basis = []
    try:
        from app.core.rag_service import cite_deduction_basis

        cited_types = set()
        for d in defects:
            dtype = str(d.get("type") or "")
            if not dtype or dtype in cited_types:
                continue
            cited_types.add(dtype)
            basis = cite_deduction_basis(dtype, DEFECT_TRANSLATION_MAP.get(dtype, ""))
            if basis:
                deduction_basis.append({"defect_type": dtype, **basis})
    except Exception as e:
        print(f"[Policy Agent] 근거 조항 인용 실패 - 점수는 그대로 유지하고 인용만 생략합니다: {e}")

    if deduction_basis:
        # 여러 결함이 같은 조항을 근거로 삼는 경우가 흔하므로 조항 단위로 중복을 제거한다. (순서는 유지 - dict가 삽입 순서를 보존)
        refs = ", ".join(dict.fromkeys(f"{b['doc_title']} {b['clause_ref']}" for b in deduction_basis))
        policy_text += f" | 근거 조항: {refs}"

    # ── Stage B: 거래 처분 판단 (LLM + RAG) ──────────────────────────────
    # [배치 규정 - CRITICAL] 반드시 점수(score)·등급(grade_str)이 확정된 **뒤**에 호출한다. 앞에서 돌면 Stage B의 출력이 점수 산정 문맥에 들어가 결정론이 깨진다.
    # 아래 호출은 score/grade_str/decision_str 어느 것도 수정하지 않으며, 결과는 return_policy 키에만 담긴다.
    # 호출 조건: 결함이 1건 이상일 때만. 무결점 건은 다툴 처분이 없어 규정 해석이 불필요하고, MINT 물량에 LLM 비용을 태우지 않는다.
    return_policy = None
    if defects:
        try:
            return_policy = evaluate_return_policy(
                book_title=book_title,
                defects=defects,
                platform=str(state.get("platform") or ""),
                return_reason=str(state.get("return_reason") or ""),
            )
        except Exception as e:
            # Stage B 실패가 점수 산출을 무효화해서는 안 된다. 처분만 사람에게 넘긴다.
            print(f"[Policy Stage B] 예기치 못한 오류({e}) - 처분 판단을 생략합니다.")
            return_policy = {
                "return_accepted": None, "shipping_fee_bearer": None,
                "liability": "UNDETERMINED", "refund_ratio": None,
                "cited_clauses": [], "requires_human": True,
                "rationale": "처분 판단을 수행하지 못해 관리자 결재로 이관합니다.",
                "stage_b_status": "EXCEPTION",
            }

    if return_policy and return_policy.get("stage_b_status") == "OK":
        accepted = return_policy.get("return_accepted")
        if accepted is not None:
            policy_text += f" | 처분 판단: 반품 {'수용' if accepted else '거절'}"
            if return_policy.get("requires_human"):
                policy_text += " (관리자 확인 필요)"

    return {
        "defects": defects,
        "ubci_score": score,
        "policy_text": policy_text,
        "deduction_basis": deduction_basis,
        # Stage B(거래 처분) 판단 결과. 점수·등급과는 별도 키에 담긴다.
        "return_policy": return_policy,
        # 증거 대조가 판독을 전건 기각해 점수의 근거가 남지 않은 상태.
        # Report Agent가 "결함 없음" 문구를 쓰지 못하게 하고, Critic이 HITL로 이관한다.
        "score_unverified": score_unverified,
        "reason_code": None,
        "repair_directive": None,
        "executed_agents": ["policy_agent"],
        "messages": [AIMessage(content=f"[Policy Agent] {policy_text}")]
    }
