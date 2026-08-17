"""
Vision Agent - GPT-4o 멀티모달 판독과 증거 대조 검증.

[구성]
  · VISION_PROMPT_BASE / vision_agent  : 촬영 컷을 읽어 결함을 확정한다
  · verify_defects_with_images         : 확정 결함을 BBox 크롭으로 재심사한다

증거 대조 검증은 판독과 별도 함수·별도 프롬프트다. 판독 프롬프트에 합치면 자기가 낸 결론을 자기가 심사하게 되어 동조 편향이 생긴다 (프리즈 규정).
"""
import base64
import io
import json
import os
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.ai.state import WMSInspectionState
from app.ai.agents.common import (
    DEFECT_TRANSLATION_MAP, INNER_PAGE_EXCLUDED_TYPES, TRACK1_IMAGE_COUNT,
    YOLO_TO_UBCI_TYPE, VLM_MAX_IMAGE_EDGE,
    _downscale_for_vlm, _ensure_local_path, _is_inner_page, _load_image_as_base64,
)
from app.ai.agents.detector import build_yolo_hint
# llm_mini는 예비 감점 검증(Stage 3) 제거로 이 노드에서 쓰지 않는다.
# Vision Agent는 GPT-4o만 쓴다 — 1차 판독(llm_vlm) + 증거 대조 검증(llm_verify).
from app.ai.agents.llm import llm_verify, llm_vlm
from app.ai.agents.schemas import CriticVerdict, DefectEvidenceVerdict, VisionResult

VISION_PROMPT_BASE = """당신은 WMS 디지털 품질 검수 센터의 수석 AI 비전(VLM) 검수원입니다.
OpenCV CLAHE(Contrast Limited Adaptive Histogram Equalization) 동적 명암 전처리가 완료된 도서 이미지(앞표지, 뒷표지, 속지)를 시각적으로 정밀 분석하여 결함 및 BBox(0~1000 상대좌표)를 100% 정밀 검출하세요.

[BBox 검출 및 결함 판정 4대 원칙]
1. 표지 (Front/Back Cover):
   - 찌그러짐, 찢어짐, 심한 오염이 없는 깨끗한 표지는 결함 없음(Clean, []).
2. 속지 필기/낙서 (DMG_INT_DOODLE):
   - 본문 지문, 보기 번호(①~④)에 친 연필/볼펜 동그라미 표기 -> DMG_INT_DOODLE.
   - 지문/쿼리문 하단에 그은 연필/볼펜 밑줄(Underline) -> DMG_INT_DOODLE.
   - 문제 박스 안이나 여백에 적힌 손글씨 메모(SQL 쿼리, outer join, 10:10:00, 숫적 메모 등) -> DMG_INT_DOODLE.
3. 인쇄본 구별:
   - 교재 본문에 기본 인쇄된 텍스트, 표(Table), 인포그래픽 박스는 절대 결함으로 오탐하지 말 것.
4. 좌표계:
   - 각 결함(defects[i])의 bbox 필드에 xmin, ymin, xmax, ymax를 이미지 0~1000 픽셀 상대 좌표로 채워서 반환하세요. confidence 필드에는 본인의 판독 확신도(0.0~1.0)를 적으세요.
   - bbox는 **당신이 이미지에서 실제로 본 위치**여야 합니다. 결함은 보이는데 위치를 정확히 특정할 수 없으면 좌표를 지어내지 말고 bbox를 null로 두세요 (그 건은 관리자가 직접 위치를 그립니다). (50,50,150,150)처럼 규칙적인 예시형 좌표, 여러 결함에 같은 좌표 반복, 이미지 전체를 덮는 띠는 전부 "보지 않고 지어낸 좌표"로 간주되어 판독 전체가 반려됩니다.
   - 해당 결함이 도서 제목이나 본문 텍스트 영역을 가리거나 침범하면 text_overlap을 true로, 아니면 false로 설정하세요 (UBCI 1.5배 가중치 판정에 직접 사용됩니다).
   - image_index에는 결함이 발견된 이미지의 순서(0번째=정면, 1번째=후면, 2번째부터=내지/측면 등)를 정확히 기재하세요.
5. 정성적 관찰(special_notes):
   - UBCI 감점과 무관하지만 특기할 사항(도서관 장서 도장, 부록 CD/카드 누락, 저자 친필 서명 등)이 보이면 top-level special_notes 필드에 한 줄로 기록하세요. 없으면 null로 두세요.
6. 이미지 유효성(invalid_image_indexes) — invalid 판정은 최후 수단:
   - invalid로 지정할 수 있는 컷은 딱 두 가지뿐입니다:
     (a) 도서가 프레임에 전혀 존재하지 않는 컷 (작업자 얼굴/신체만 찍힘, 빈 배경/책상)
     (b) 초점 이탈·모션 블러가 심해 도서 표면의 상태를 물리적으로 읽을 수 없는 컷
   - 다음은 전부 유효한 컷입니다. invalid로 지정하지 말고 반드시 판독하세요:
     · 도서를 손에 들고 비스듬히 기울여 찍은 컷 (현장 웹캠 촬영의 기본 형태입니다)
     · 도서가 프레임 일부에만 걸쳐 있거나, 사람 얼굴·손·의자·배경과 함께 찍힌 컷
     · 표지가 아닌 책배(종이 단면)·책등·펼친 속지가 찍힌 컷
     · 표지 글자가 안 읽혀도 종이 상태(주름·오염·마모)는 판독 가능한 컷
   - 결함 판정(defects)은 유효한 컷에 대해서만 수행하고, 도서 미식별 컷에서는 결함을 보고하지 마세요. 모든 컷에 도서가 보이면 빈 배열([])로 두세요.
   - 전 컷 invalid 지정은 "검수 불가" 선언과 같으며 시스템이 그 건을 자동 확정하지 않고 관리자 수동 검수(HITL)로 이관합니다. 확신 없이 전 컷을 invalid로 만들지 말고, 조금이라도 판독 가능한 컷은 판독을 시도한 뒤 낮은 confidence로 보고하세요.

7. [image_index 규칙 — 반드시 지킬 것]
   image_index는 첨부된 이미지의 순서입니다. 각 이미지 바로 앞에 [이미지 index=N] 표시가 붙어 있으니 그 숫자를 그대로 사용하세요.
   - 사진에 무엇이 찍혔는지로 번호를 추측하지 마세요. 첨부 순서가 유일한 기준입니다.
   - 첨부된 이미지가 N장이면 사용 가능한 index는 0부터 N-1까지뿐입니다. 그 범위를 벗어난 숫자를 절대 쓰지 마세요.

8. [촬영 순서 관례] 통상 0=앞면, 1=뒷면, 2=책등, 3번 이후=책배(종이 단면) 또는 속지입니다.
   다만 얇은 책은 책등 촬영을 생략할 수 있어 이 관례가 항상 맞지는 않습니다.
   번호는 위 7번(첨부 순서)을 따르고, 각 사진의 실제 내용은 눈으로 보고 판단하세요.
   - 앞면·뒷면·책등은 별도 YOLO 앙상블이 이미 검사했습니다(아래 후보 목록 참조).
   - 책배·속지는 그 모델이 학습한 적 없는 각도이므로 당신이 직접 판독해야 합니다.
     종이 단면의 마모·오염·변색, 내지의 얼룩·물 젖음·찢어짐을 빠짐없이 보고하세요.

9. [속지 지면 영역 — 결함 유무와 무관하게 반드시 채울 것]
   펼쳐진 책의 내지(본문 페이지)가 보이는 컷이 하나라도 있으면, 그 컷마다
   inner_page_regions 항목을 하나씩 만드세요. 이 배열은 결함 보고와 별개입니다.
   결함이 없어도, 깨끗한 속지여도 지면이 보이면 반드시 채웁니다.
   - 손가락, 책상, 배경을 제외하고 종이 지면만 감싸도록 좌표를 잡습니다.
   - 한 컷에 양쪽 페이지가 보이면 전체를 하나의 영역으로 묶어도 됩니다.
   - image_index는 위 7번 규칙(첨부 순서)을 그대로 따릅니다.
   - 표지·책등만 찍힌 컷이거나 내지가 전혀 안 보이면 그 컷은 넣지 마세요.
     모든 컷에 내지가 없으면 빈 배열([])입니다.

10. [변색 강도] 황변/변색(DMG_INT_DISCOLOR)을 보고할 때는 level에 1~3을 넣으세요.
   황변은 지면 전체에 나타나 면적으로는 심각도를 구분할 수 없기 때문입니다.
   1=종이 끝만 살짝 바램(중고책의 자연스러운 노화) / 2=전반적으로 뚜렷 / 3=짙은 갈색·곰팡이성.
   오래된 중고책이 누렇게 뜬 것은 정상 범위이므로 함부로 2~3을 주지 마세요.

11. [물 젖음/습기 손상 — DMG_EXT_WET, 놓치기 쉬우니 특히 주의]
   물에 젖었다 마른 책은 찢김이나 얼룩 없이도 아래 형태로 드러납니다. 하나라도 보이면 DMG_EXT_WET으로 보고하세요 (색이 아니라 종이의 기하학적 변형을 보는 것이 핵심):
   - 책배(종이 단면)가 매끈한 직선이 아니라 물결처럼 쭈글쭈글하게 부풀어 있음
   - 책을 덮었는데 지면이 평평하지 않고 두께가 부위별로 다르게 부풀어(팽윤) 있음
   - 지면에 물결 주름(cockling)·굴곡이 잡혀 빛을 받는 면에 줄무늬 음영이 생김
   - 종이 가장자리를 따라 얼룩진 경계선(tide line)이 남아 있음
   - 표지가 물결지거나 뒤틀려 들뜸 / 코팅이 우글거림
   판단 기준: 새 책의 종이 단면은 자로 그은 듯 균일한 직선입니다. 그 직선이 무너져
   울퉁불퉁하면 물 손상을 의심하는 것이 정상입니다. 사용 중 자연스럽게 생기는 모서리 마모(DMG_EDGE_WEAR)와 구별하세요. — 마모는 모서리 국소, 물 손상은 지면 전체의 파형입니다.
   - 확신이 서지 않으면 보고하지 않고 넘기지 말고, confidence를 0.4~0.6으로 낮춰 보고하세요. 판독 누락(놓침)이 낮은 확신도 보고보다 훨씬 큰 손실입니다.

12. [담당 범위 — 표지는 종합 판단, 속지는 찢어짐 중심]
   표지·뒤표지·책등(0~2번 컷)은 당신이 종합적으로 판단합니다. 여러 컷을 함께 보고 같은 결함인지 다른 결함인지까지 정리해 주세요. 제보는 참고일 뿐 기각해도 됩니다.

   속지(3번째 이후 컷)에서는 모서리 마모(DMG_EDGE_WEAR)를 보고하지 마세요.
   마모는 책의 겉면에서 생기는 손상이고, 속지 컷에 측면이 걸려 보이더라도 그 부위는 표지·책등 컷에서 이미 판정됩니다. 여기서 또 세면 같은 손상을 두 번 세는 것입니다.
   속지에서 당신이 볼 것은 찢어짐(DMG_EXT_TEAR) 입니다.

   그 밖에 당신이 맡는 것은 탐지 모델이 할 수 없는 판단입니다:
   - 물 젖음/습기(DMG_EXT_WET) — 지면 전체의 기하학적 변형
   - 오염·얼룩(DMG_INT_STAIN), 황변·변색(DMG_INT_DISCOLOR) — 면적/강도 판단
   - 찢어짐(DMG_EXT_TEAR) — 특히 속지가 찢겼는지
   - 인쇄물과 손글씨의 구별, 도서 미식별 컷 판정

13. [판독 원칙 — 종합]
   - 이 검수 결과는 실제 매입 대금을 결정합니다. "결함 0건"은 "결함을 못 찾았다"가 아니라
     "정밀 판독 결과 무결점임을 보증한다"는 선언입니다. 확신이 없으면 0건으로 확정하지 말고
     낮은 confidence로라도 보고하거나, 판독 불가 컷은 invalid로 명시하세요.
   - 촬영 컷 전체가 물리적으로 판독 불가한 경우가 아니라면, 반드시 각 컷을 끝까지 살펴본 뒤
     결과를 내세요. 사진이 지저분하거나 각도가 나쁘다는 이유로 판독을 포기하지 마세요.
"""


def _flag_ungrounded_bboxes(defects: List[Dict[str, Any]]) -> int:
    """VLM이 지어낸(비접지) BBox를 결정론 규칙으로 표시한다. LLM 미사용.

    VLM은 정규화 좌표를 픽셀 위치로 환산하지 못하므로, 위치를 못 잡으면 그럴듯한
    좌표를 만들어 반환한다. 실측 두 패턴:
      R1  서로 다른 컷에 완전히 같은 좌표 반복 (LPN-260810-A030: 컷 0·1·2 전부 (0,0,1000,100))
      R2  같은 크기 상자가 일정 간격 등차로 나열 (LPN-260810-A012: (50,50,150,150) →
          (100,100,200,200) → (150,150,250,250) → (200,200,300,300), 전부 100x100)

    지어낸 좌표는 두 가지를 오염시킨다 - ① 크롭 검증이 엉뚱한 부위를 심사해 실제 결함의
    감점을 지운다(A012 재검수에서 발생) ② HITL 화면·학습 데이터에 가짜 위치가 실린다.

    결함을 지우지 않는다. `bbox_ungrounded=True` 표식만 남기고, 크롭 검증이 이를 건너뛰며
    Critic Stage A가 정합성 위반으로 승격시켜 HITL로 보낸다 - conf_copied_from_candidate와
    같은 처리 계보다. YOLO 좌표(conf_source="yolo"/자동 채택분)는 실측이므로 대상이 아니다.
    """
    vlm_owned = [
        d for d in defects
        if isinstance(d.get("bbox"), dict)
        and d.get("conf_source") != "yolo"
        and not d.get("adopted_from_candidate")
    ]
    if len(vlm_owned) < 2:
        return 0

    def key(d):
        b = d["bbox"]
        return (int(b.get("xmin", 0)), int(b.get("ymin", 0)),
                int(b.get("xmax", 0)), int(b.get("ymax", 0)))

    flagged: set = set()

    # R1: 같은 좌표 4값이 서로 다른 image_index에서 반복
    by_coord: Dict[tuple, List[Dict[str, Any]]] = {}
    for d in vlm_owned:
        by_coord.setdefault(key(d), []).append(d)
    for coord, group in by_coord.items():
        if len({int(g.get("image_index") or 0) for g in group}) >= 2:
            flagged.update(id(g) for g in group)

    # R2: 같은 크기 상자가 xmin·ymin 모두 동일 간격 등차 (3건 이상)
    by_size: Dict[tuple, List[Dict[str, Any]]] = {}
    for d in vlm_owned:
        x1, y1, x2, y2 = key(d)
        by_size.setdefault((x2 - x1, y2 - y1), []).append(d)
    for size, group in by_size.items():
        if len(group) < 3:
            continue
        pts = sorted({(key(g)[0], key(g)[1]) for g in group})
        if len(pts) < 3:
            continue
        dx = pts[1][0] - pts[0][0]
        dy = pts[1][1] - pts[0][1]
        if (dx, dy) != (0, 0) and all(
            (pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) == (dx, dy)
            for i in range(len(pts) - 1)
        ):
            flagged.update(id(g) for g in group)

    for d in vlm_owned:
        if id(d) in flagged:
            d["bbox_ungrounded"] = True
    return len(flagged)


def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Vision Agent: GPT-4o VLM 정밀검수 -> GPT-4o-mini 예비감점 검증 중...")
    defects = state.get("defects") or []
    image_paths = state.get("image_paths") or []
    # Detector Node가 앞서 채워둔 앙상블 후보 (VLM 실패 시 폴백 근거로도 쓰인다)
    yolo_candidates = state.get("yolo_candidates") or []

    if llm_vlm and not defects and image_paths:
        # --- GPT-4o VLM 정밀 검수 (WBF 후보를 컨텍스트로 제공, 최종 판단은 VLM이 직접) ---
        structured_vlm = llm_vlm.with_structured_output(VisionResult)
        # 후보를 JSON 그대로(=confidence 포함) 넣으면 모델이 그것을 답으로 베낀다.
        # 실측: 후보 5건과 확정 결함 5건의 confidence가 소수점 4자리까지 동일(0.7578 / 0.7947 / 0.5241 / 0.4370 / 0.5903, job b7b34ae1). 후보에 잡음이 많을 때는 말이 안 되는 것들을
        # 걸러내며 "판독하는 것처럼" 보였지만, 후보 품질이 올라가자 전건 복사가 드러났다. 즉 판독이 아니라 통과 도장이었다.
        #
        # 그래서 후보에서 confidence를 제거하고 위치·유형만 넘긴다. 모델이 베낄 확신도가 없으면 스스로 판단해 적을 수밖에 없다. 동시에 후보를 "정답 목록"이 아니라 "기각해야 할 수도 있는 제보"로 제시한다.
        prompt_vlm = VISION_PROMPT_BASE + build_yolo_hint(yolo_candidates)

        # 각 이미지 바로 앞에 인덱스 라벨을 끼워 넣는다.
        # 라벨 없이 이미지만 나열하면 VLM이 **첨부 순서가 아니라 사진 내용으로** 번호를 매긴다.
        # 실측: 속지 1장만 넣었는데 프롬프트의 "3번 이후=속지" 관례를 보고 image_index=3을 반환했고, 4장 입력에서는 존재하지 않는 index=4를 지목했다.
        # 범위 밖 인덱스는 Critic이 환각으로 판정해 HITL로 보내므로, 프롬프트 탓에 매번 HITL이 걸리는 상태가 된다.
        # 라벨로 앵커를 박아 순서를 강제한다.
        content_list = [{"type": "text", "text": prompt_vlm}]
        for i, path in enumerate(image_paths):
            b64 = _load_image_as_base64(path)
            if b64:
                content_list.append({"type": "text", "text": f"[이미지 index={i}]"})
                content_list.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

        special_notes = None
        vision_error = None
        invalid_image_indexes: list = []
        inner_page_regions: list = []
        try:
            res_vlm: VisionResult = structured_vlm.invoke([HumanMessage(content=content_list)])
            is_mint = res_vlm.is_mint
            defects = [d.model_dump() for d in res_vlm.defects]
            special_notes = res_vlm.special_notes
            # 범위를 벗어난 인덱스는 VLM 환각 신호이므로 버린다 (Critic의 image_index 검증과 동일 원칙)
            invalid_image_indexes = sorted({
                int(i) for i in (res_vlm.invalid_image_indexes or [])
                if isinstance(i, (int, float)) and 0 <= int(i) < len(image_paths)
            })
            # 속지 지면 영역도 같은 원칙으로 범위 검증한다. 도서 미식별 컷은 제외한다
            # (판독 불가로 분류된 컷에 낙서 탐지를 돌릴 이유가 없다).
            inner_page_regions = [
                r.model_dump() for r in (res_vlm.inner_page_regions or [])
                if 0 <= int(r.image_index) < len(image_paths)
                and int(r.image_index) not in invalid_image_indexes
            ]
        except Exception as e:
            # 예전에는 여기서 `is_mint = len(defects) == 0` 이었다.
            # VLM 호출이 실패하면 defects가 빈 채로 남으므로 "결함 0건 = MINT(무결점)"으로 해석되어, OpenAI 장애/키 만료 시 모든 반품 도서가 UBCI 100점 MINT로 자동 승인·매입되었다.
            # "검수하지 못했다"와 "검수했더니 흠이 없다"는 완전히 다른 사실인데 이를 동일하게 취급한 것.
            # 판독 실패는 MINT로 승격시키지 않고 reason_code=HUMAN_REQUIRED를 세워 Supervisor의 기존 HITL 이관 분기를 타게 한다.
            print(f"[Vision Agent] GPT-4o VLM 호출 실패 - MINT 자동승격 금지, HITL 이관: {e}")
            vision_error = f"{type(e).__name__}: {e}"

        if vision_error:
            # 판독 실패는 즉시 HITL로 보내지 않는다. Rate limit(429)/타임아웃 같은 일시적 장애가 대부분이므로,
            # 기존 재검수 루프(Critic의 revision_count, 최대 2회)에 태워 재시도하게 하고 그래도 안 되면 Supervisor가 HITL로 이관한다.
            # 핵심은 defects를 비운 채 ubci_score 산출을 막는 것이다. Critic의 `score is None and revision < 2` 분기가 재검수를, `revision >= 2` 분기가 HITL 이관을 이미 담당하고 있으므로 별도 분기를 새로 만들 필요가 없다.
            # YOLO 후보는 state에 그대로 보존한다. 다만 그것을 임의의 ratio/감점 값으로 변환해 등급을 매기지는 않는다 - 근거 없는 수치를 지어내는 것이기 때문이다.
            # HITL 관리자 화면에서 사람이 판단할 때 참고 증거로만 쓰인다.
            return {
                "is_mint": None,
                "defects": [],
                "vision_failed": True,
                "vision_text": (
                    f"GPT-4o VLM 판독 실패 - UBCI 점수 산출을 보류하고 재검수 루프로 회부합니다. "
                    f"(YOLO 사전탐지 후보 {len(yolo_candidates)}건 보존 / 원인: {vision_error[:160]})"
                ),
                "executed_agents": ["vision_agent"],
                "messages": [AIMessage(content="[Vision Agent] VLM 판독 실패 - 재검수 루프 회부")],
            }
    else:
        is_mint = len(defects) == 0
        special_notes = None
        invalid_image_indexes = []
        inner_page_regions = []

    # --- 예비 감점 산정 (결정론적) ---
    #
    # [프리즈 예외 — 2026-08-17, 조장 승인]
    # 종전에는 GPT-4o-mini가 이 값을 재계산했다(Stage 3). 그 호출을 제거한다.
    # 백업: archive/2026-08-17_freeze_exception_vision_stage3/
    #
    # 제거 근거 — preliminary_deduction을 최종 산정에 쓰는 곳이 없다.
    #   · policy.py는 이 값을 읽지 않는다(참조 0건). UBCI 매트릭스 SSOT
    #     (app/core/ubci_matrix.py)로 처음부터 다시 계산해 applied_deduction에 넣는다.
    #   · Policy는 모든 분기에서 applied_deduction을 세우므로, 소비처(critic·report·wrapper)의
    #     `d.get("applied_deduction", d.get("preliminary_deduction"))` 폴백은 정상 경로에서
    #     도달하지 않는다.
    #   · 그 폴백은 오히려 위험하다 — 그룹 산정(마모 부위 합산)·Cap·오탐 제외를 반영하지
    #     않아 보증서에 실제와 다른 감점이 찍힌 전례가 있다(report.py 주석 참조).
    #
    # 즉 아무도 쓰지 않는 값의 정확도를 높이려고 LLM을 호출하고 있었다.
    # 아래 규칙은 종전 폴백 로직 그대로이며, 판정 결과에는 영향이 없다.
    for d in defects:
        dtype = str(d.get("type", ""))
        ratio = d.get("ratio", 10)
        if "DOODLE" in dtype or "필기" in dtype or "낙서" in dtype:
            d["preliminary_deduction"] = min(15, max(5, ratio))
        elif "TEAR" in dtype or "찢어짐" in dtype:
            d["preliminary_deduction"] = 5 if ratio < 5 else (10 if ratio < 15 else 15)
        else:
            d["preliminary_deduction"] = max(5, ratio)

    # --- Track 2·3: 속지 지면 크롭에 doodle 단독 추론 ---
    # VLM이 지정한 지면 영역만 잘라 doodle 모델에 넣는다. 인쇄면 전체를 넣으면 활자를 손글씨로 오인하므로(실측: 깨끗한 속지 1장에 오탐 12건), 학습 도메인인 "손글씨 크롭 패치"에 가까운 입력을 만들어 준다. 크롭본과 탐지 결과는 로컬에 적재해 나중에 검증한다.
    doodle_added = 0
    for region in (inner_page_regions or []):
        try:
            idx = int(region.get("image_index", -1))
            if not (0 <= idx < len(image_paths)):
                continue
            local_path = _ensure_local_path(image_paths[idx])
            if not local_path:
                continue

            import cv2
            from app.ai.wbf_detector import wbf_detector, detect_page_region

            img = cv2.imread(local_path)
            if img is None:
                continue
            ih, iw = img.shape[:2]
            # 좌표는 YOLO-World(공간 판단)를 우선 쓰고, 실패 시 VLM 좌표로 폴백한다.
            # VLM은 "이 사진이 속지인가"(의미 판단)까지만 신뢰한다 - 좌표 정확도는 낮다.
            region_src = "yoloworld"
            wr = detect_page_region(img)
            if wr is not None:
                x1 = max(0, min(iw - 1, int(wr[0] * iw)))
                y1 = max(0, min(ih - 1, int(wr[1] * ih)))
                x2 = max(x1 + 1, min(iw, int(wr[2] * iw)))
                y2 = max(y1 + 1, min(ih, int(wr[3] * ih)))
            else:
                region_src = "vlm"
                x1 = max(0, min(iw - 1, int(region.get("xmin", 0) / 1000 * iw)))
                y1 = max(0, min(ih - 1, int(region.get("ymin", 0) / 1000 * ih)))
                x2 = max(x1 + 1, min(iw, int(region.get("xmax", 1000) / 1000 * iw)))
                y2 = max(y1 + 1, min(ih, int(region.get("ymax", 1000) / 1000 * ih)))
            crop = img[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            hits = wbf_detector.detect_doodle_only(
                crop,
                debug_tag=f"idx{idx}_inner",
                debug_meta={
                    "source_image": os.path.basename(local_path),
                    "image_index": idx,
                    "region_source": region_src,  # yoloworld | vlm(폴백)
                    "vlm_region": {k: region.get(k) for k in ("xmin", "ymin", "xmax", "ymax")},
                    "crop_px": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                },
            )
            # 크롭 기준 0~1000 좌표를 원본 이미지 기준으로 역변환한다.
            # 프론트는 S3 원본 위에 BBox를 그리므로 원본 좌표여야 한다.
            cw, ch = (x2 - x1), (y2 - y1)
            for hd in hits:
                b = hd["bbox"]
                defects.append({
                    "type": YOLO_TO_UBCI_TYPE.get("doodle_scribble", "DMG_INT_DOODLE"),
                    "ratio": max(1, int((b["xmax"] - b["xmin"]) * (b["ymax"] - b["ymin"]) / 10000)),
                    "confidence": hd.get("confidence"),
                    "image_index": idx,
                    "bbox": {
                        "xmin": int((x1 + b["xmin"] / 1000 * cw) / iw * 1000),
                        "ymin": int((y1 + b["ymin"] / 1000 * ch) / ih * 1000),
                        "xmax": int((x1 + b["xmax"] / 1000 * cw) / iw * 1000),
                        "ymax": int((y1 + b["ymax"] / 1000 * ch) / ih * 1000),
                    },
                })
                doodle_added += 1
        except Exception as e:
            # 부가 탐지이므로 실패가 판독 결과를 폐기시키지 않는다.
            print(f"[Vision Agent] 속지 크롭 doodle 추론 실패({type(e).__name__}) - 건너뜀: {e}")

    if doodle_added:
        is_mint = False
        print(f"[Vision Agent] 속지 크롭 doodle 낙서 {doodle_added}건 추가")

    # 프론트에서 문자열을 지어내지 않도록 판독 근거(스캔 장수, 앙상블 후보 수, 결함 유형)를 여기서 확정해 State에 싣는다.
    scanned_cnt = len(image_paths)

    # --- 마모 후보 자동 채택 [WEAR_AUTO_ADOPT, 기본 꺼짐] ---
    # 켜면 YOLO 마모 후보를 VLM 채택 여부와 무관하게 전건 결함으로 등록하고, 판정은 확대 크롭을 보는 증거 대조 검증에 맡긴다(마모 한정, 다른 유형은 VLM 판독 유지).
    auto_adopted = 0
    for cand in (yolo_candidates or []) if WEAR_AUTO_ADOPT else []:
        if str(cand.get("type") or "") != "DMG_EDGE_WEAR":
            continue
        cb, ci = cand.get("bbox"), int(cand.get("image_index") or 0)
        if not isinstance(cb, dict):
            continue
        # VLM이 이미 같은 상자를 보고했으면 중복 등록하지 않는다
        if any(
            int(d.get("image_index") or 0) == ci and _bbox_iou(d.get("bbox"), cb) >= 0.5
            for d in defects
        ):
            continue
        defects.append({
            "type": "DMG_EDGE_WEAR",
            "ratio": 0,                      # _effective_ratio가 BBox 면적에서 유도한다
            "bbox": dict(cb),
            "image_index": ci,
            "confidence": cand.get("confidence"),
            "conf_source": "yolo",
            "adopted_from_candidate": True,  # 감사 추적: VLM 판독이 아니라 자동 채택분
        })
        auto_adopted += 1
    if auto_adopted:
        is_mint = False
        print(f"[Vision Agent] 마모 후보 {auto_adopted}건 자동 채택 (판정은 크롭 검증이 담당)")

    # --- 속지 마모 제외 ---
    # 모서리 마모는 책의 겉면에서 발생한다. 속지 컷에 측면 마모가 걸려 보이더라도 그 부위는 표지·책등 컷에서 이미 검출되므로, 속지에서 또 세면 같은 손상을 이중으로 계산하게 된다.
    # 판정 대상에서 제외한다.
    # 자동 채택분도 이 필터를 통과해야 하므로 채택 뒤에 둔다.
    before = len(defects)
    defects = [
        d for d in defects
        if not (
            _is_inner_page(d.get("image_index"))
            and str(d.get("type", "")) in INNER_PAGE_EXCLUDED_TYPES
        )
    ]
    inner_wear_dropped = before - len(defects)
    if inner_wear_dropped:
        print(f"[Vision Agent] 속지 마모 {inner_wear_dropped}건 제외 (겉면에서 중복 검출되는 부위)")

    # --- 확신도 출처 확정 + YOLO 제보 복사 탐지 (결정론적) ---
    # 출처(conf_source): BBox가 제보와 일치하면 확신도를 제보 실측값으로 교체하고 VLM 자기 신고는 conf_vlm_selfreported 에 보존한다. 제보에 없으면 "vlm"(추정치).
    # 위반(conf_copied_from_candidate): VLM이 이미지를 보지 않은 신호. 확신도가 제보와 소수점 4자리까지 같거나, 확정 결함 2건 이상에 동일한 확신도를 부여한 경우.
    copied_conf = 0
    if defects and yolo_candidates:
        cand_confs = {
            round(float(c["confidence"]), 4)
            for c in yolo_candidates
            if c.get("confidence") is not None
        }
        for d in defects:
            # 자동 채택분은 제보 확신도를 그대로 쓰는 것이 설계다. 이걸 복사 위반으로 잡으면 Critic Stage A가 전건을 HITL로 올려 자동화가 성립하지 않는다. 
            # 복사 탐지의 대상은 "VLM이 판독했다고 주장하면서 실제로는 제보를 되돌려준 것" 뿐이다.
            if d.get("adopted_from_candidate"):
                continue
            try:
                if round(float(d.get("confidence")), 4) in cand_confs:
                    d["conf_copied_from_candidate"] = True
                    copied_conf += 1
            except (TypeError, ValueError):
                continue

        # 1) BBox 대조로 확신도 출처를 확정한다 (IoU >= 0.9 = 사실상 같은 상자)
        for d in defects:
            best, best_iou = None, 0.0
            for c in yolo_candidates:
                if int(c.get("image_index") or 0) != int(d.get("image_index") or 0):
                    continue
                iou = _bbox_iou(d.get("bbox"), c.get("bbox"))
                if iou > best_iou:
                    best, best_iou = c, iou
            if best is not None and best_iou >= 0.9 and best.get("confidence") is not None:
                d["conf_source"] = "yolo"
                d["conf_vlm_selfreported"] = d.get("confidence")
                d["confidence"] = round(float(best["confidence"]), 4)
                d["conf_bbox_iou"] = round(best_iou, 3)
            else:
                d["conf_source"] = "vlm"

    # 2) 평평한 자기 신고 탐지 - VLM 단독 판독분의 확신도가 전부 같은 값이면 판단이 아니다.
    vlm_confs = [
        round(float(d.get("confidence")), 4)
        for d in defects
        if d.get("conf_source") != "yolo"
        and not d.get("adopted_from_candidate")
        and d.get("confidence") is not None
    ]
    if len(vlm_confs) >= 2 and len(set(vlm_confs)) == 1:
        for d in defects:
            if d.get("conf_source") != "yolo":
                d["conf_flat_selfreported"] = True
                d["conf_copied_from_candidate"] = True
        copied_conf = max(copied_conf, len(vlm_confs))

    # --- 비접지 BBox 탐지 (결정론적) ---
    # 크롭 검증보다 먼저 돈다 - 지어낸 좌표를 크롭 심사하면 엉뚱한 부위를 보고
    # "손상 없음"이 나와 실제 결함의 감점이 지워진다 (실측: LPN-260810-A012 재검수).
    ungrounded_cnt = _flag_ungrounded_bboxes(defects)
    if ungrounded_cnt:
        print(f"[Vision Agent] ⚠ 비접지 BBox {ungrounded_cnt}건 - VLM이 좌표를 지어낸 패턴 (HITL 승격 대상)")

    # --- 증거 대조 검증 (GPT-4o, BBox 크롭 건별 심사) ---
    # [호출 위치] 속지 마모 제외와 확신도 출처 확정이 끝난 뒤에 돈다.
    #   - 앞에서 돌면 곧 폐기될 속지 결함까지 심사해 비용만 쓴다.
    #   - 고확신 면제(VERIFY_EXEMPT_CONF)는 conf_source/confidence가 확정돼야 판단할 수 있다.
    #     VLM이 써낸 자기 신고(전건 0.8)로 면제를 판단하면 전건이 면제돼 버린다.
    # 오탐으로 지목된 항목은 제거하지 않고 표식만 남긴다 - 여기서 지우면 Critic/Supervisor가 볼 근거가 사라지고, 판정 책임이 이 함수로 넘어와 버린다.
    verify_verdict = verify_defects_with_images(
        defects,
        image_paths,
        book_title=str(state.get("book_title") or state.get("title") or ""),
        special_notes=special_notes,
    )
    verify_note = None
    if verify_verdict is not None:
        suspects = set(verify_verdict.suspect_indices or [])
        for i, d in enumerate(defects):
            if i in suspects:
                d["evidence_suspect"] = True
        if verify_verdict.decision == "REJECTED":
            verify_note = (
                f"증거 대조 검증 반려 - {verify_verdict.reason}"
                + (f" (오탐 의심 인덱스: {sorted(suspects)})" if suspects else "")
            )
        else:
            verify_note = f"증거 대조 검증 통과 - {verify_verdict.reason}"
        print(f"[Vision Verify] {verify_note}")

    if is_mint:
        vision_text = (
            f"{scanned_cnt}장 다각도 스캔 완료 - WBF 3-YOLO 앙상블 및 GPT-4o VLM 판독 결과 "
            f"결함 0건, MINT(무결점) 판정"
        )
    else:
        type_summary = ", ".join(sorted({str(d.get("type")) for d in defects if d.get("type")})) or "미분류"
        copy_warn = (
            f" / ⚠ 판독 신뢰 불가: 확정 결함 {copied_conf}건의 확신도가 YOLO 제보 값과 동일합니다"
            f"(이미지를 직접 판단한 것이 아니라 제보를 되돌려준 것으로 보임) - 관리자 확인 필요"
            if copied_conf else ""
        )
        vision_text = (
            f"{scanned_cnt}장 다각도 스캔 완료 - 결함 {len(defects)}건 검출 ({type_summary}), "
            f"BBox 좌표 및 image_index 바인딩 완료{copy_warn}"
        )
    if special_notes:
        vision_text += f" / 특이사항: {special_notes}"
    if invalid_image_indexes:
        vision_text += f" / 도서 미식별 컷 {len(invalid_image_indexes)}장 제외 (인덱스: {invalid_image_indexes})"
    if verify_note:
        vision_text += f" / {verify_note}"

    result = {
        "is_mint": is_mint,
        "defects": defects,
        "vision_text": vision_text,
        "invalid_image_indexes": invalid_image_indexes,
        "executed_agents": ["vision_agent"],
        "messages": [AIMessage(content=f"[Vision Agent] WBF+GPT-4o VLM 검수 & GPT-4o 검증 완료 (is_mint: {is_mint}, 결함 {len(defects)}건)")]
    }
    if special_notes:
        result["special_notes"] = special_notes
    return result

# ==========================================
# 1-b. 증거 대조 검증 (Vision 종합 검증) - GPT-4o
# ==========================================
# 도입 배경 - 현행 Critic은 이미지를 보지 못한다:
# critic_agent의 Stage B 프롬프트에는 image_url 파트가 없다. 전달되는 것은 도서명, "이미지 장수(숫자)", 결함 목록 JSON, UBCI 점수뿐이다. 즉 "환각 방어" 담당이 실제 증거(픽셀)를 한 번도 보지 않고 "BBox가 면적 50% 이상이면 인쇄물 오탐 의심", "다른 이미지에 동일 좌표 중복" 같은 메타 규칙만으로 오탐을 추정해 왔다.
# vision_agent 안에는 이미 이미지가 컨텍스트에 올라와 있으므로, 여기서 한 번 더 심사하면 재업로드 없이 실제 증거를 보고 판정할 수 있다.
# Critic Stage B는 제거하지 않고 그대로 둔다 (이중 검증):
#   - 본 함수      : 이미지를 본다. 판독이 증거와 맞는가 (증거 타당성)
#   - Critic Stage B: 점수를 본다. 판독과 UBCI가 정합한가 · 경계선인가 (정합성 · 라우팅)
#   두 검증은 보는 대상이 달라 실패 양상이 독립적이고, 서로의 약점을 덮는다.
#   (본 함수는 판독 맥락 안에 있어 동조 편향 위험이 있고, Critic은 독립적이나 눈이 멀었다.)
# 프리즈 규정("각 단계는 별도 노드/함수로 유지, 단일 프롬프트로 병합 금지") 준수를 위해 vision 판독 프롬프트에 합치지 않고 독립 함수 + 독립 프롬프트로 분리한다.
# 동조 편향을 줄이기 위해 앞선 판독의 추론 과정은 넘기지 않고, 이미지와 확정된 결함 목록만 새로 구성해 전달한다.
# 비용: 결함이 0건이면 호출하지 않는다(MINT 물량에서 추가 비용 0).
# 실패 시 fail-open - 부가 검증이므로 LLM 장애가 판독 결과를 폐기시키지 않는다.

# 증거 대조 검증 면제 확신도 하한. 기본값 1.01 = 면제 없음(전건 심사).
# 확신도는 진위와 무관한 것이 실측으로 확인되어 판단 근거로 쓰지 않는다.
VERIFY_EXEMPT_CONF = float(os.getenv("WMS_VERIFY_EXEMPT_CONF", "1.01"))

# ── 마모 판정 실험 플래그 [미사용/확장예정 — 기본 꺼짐] ────────────────────────
#
# 존치 사유: 마모 탐지기를 재학습해 정밀도를 올린 뒤 곧바로 재실험하기 위한 완성 코드다.
# 지우면 측정 설계를 처음부터 다시 짜야 한다. 켜기 전에 아래 전제를 반드시 재측정할 것.
#
# 두 플래그는 짝으로만 의미가 있다. 하나만 켜면 어느 쪽도 옳지 않다.
#   - 채택만 켜면  : 오탐 후보가 그대로 감점된다
#   - 제외만 켜면  : VLM이 채택한 결함에서 감점만 빠져 점수가 오른다
# 끈 이유 (2026-08-09 실측, 정답 확인 도서 3권):
#   마모 후보 23건 중 실제 결함 2~3건 — 탐지기 정밀도 10~13%.
#   중재 계층으로 해결되는 문제가 아니며, 켜면 깨끗한 책이 감점된다
#   (LPN-260810-A005: 후보 12건 전부 오탐인데 크롭 검증이 8건을 YES로 확정 → MINT 95 → 78).
#   상세: `33_코드_변경이력_설계배경_아카이브` 2026-08-08 추가분.

# YOLO 마모 후보를 VLM 채택 여부와 무관하게 전건 결함으로 등록한다.
WEAR_AUTO_ADOPT = os.getenv("WMS_WEAR_AUTO_ADOPT", "0") == "1"

# 증거 대조 검증의 UNCLEAR(판단 어려움)를 감점 제외로 처리한다.
# 실측에서 UNCLEAR 12건은 12건 모두 오탐이었다 - 판정 자체의 근거는 가장 좋다.
UNCLEAR_EXCLUDES_DEDUCTION = os.getenv("WMS_UNCLEAR_EXCLUDES_DEDUCTION", "0") == "1"

# 크롭 기하. 결함만 딱 자르면 주변 면과의 대비가 사라져 판단할 수 없으므로 여백을 준다.
VERIFY_CROP_EXPAND = 3.0
VERIFY_CROP_SIZE = 512
VERIFY_CROP_MAX_ASPECT = 3.0      # 띠 형태 결함의 짧은 축에 맥락을 확보
VERIFY_CROP_MIN_SHORT = 224       # 배율 기준축 (긴 변 기준이면 띠에서 확대가 안 된다.)
VERIFY_CROP_MAX_LONG = 896
VERIFY_CROP_MAX_UPSCALE = 4.0     # 원본에 없는 정보는 확대해도 생기지 않는다.


def _crop_around_bbox(
    path_or_url: str,
    bbox: Dict[str, Any],
    expand: float = VERIFY_CROP_EXPAND,
    out_size: int = VERIFY_CROP_SIZE,
) -> Optional[str]:
    """BBox 주변을 잘라 확대한 이미지를 base64로 돌려준다.

    VLM은 정규화 좌표를 픽셀 위치로 환산하지 못하므로, 볼 곳을 미리 잘라서 넘긴다.
    (설계 배경: `33_코드_변경이력_설계배경_아카이브` 2026-08-08 추가분 §4)
    """
    try:
        from PIL import Image
        import base64
        import io

        local = _ensure_local_path(path_or_url)
        if not local:
            return None
        img = Image.open(local).convert("RGB")
        iw, ih = img.size

        x1 = int(bbox["xmin"]) / 1000.0 * iw
        y1 = int(bbox["ymin"]) / 1000.0 * ih
        x2 = int(bbox["xmax"]) / 1000.0 * iw
        y2 = int(bbox["ymax"]) / 1000.0 * ih
        if x2 <= x1 or y2 <= y1:
            return None

        bw, bh = x2 - x1, y2 - y1

        # 여백은 짧은 변 기준. 긴 변 기준 정사각으로 만들면 띠 형태 결함에서 크롭이 표지 전체로 부풀어 확대 목적이 무산된다.
        pad_x = pad_y = max(min(bw, bh) * (expand - 1) / 2, 24.0)

        # 종횡비가 상한을 넘으면 짧은 축에만 여백을 더해 주변 맥락을 확보한다.
        if bw > bh:
            pad_y = max(pad_y, (bw / VERIFY_CROP_MAX_ASPECT - bh) / 2)
        else:
            pad_x = max(pad_x, (bh / VERIFY_CROP_MAX_ASPECT - bw) / 2)

        left = max(0, int(x1 - pad_x))
        top = max(0, int(y1 - pad_y))
        right = min(iw, int(x2 + pad_x))
        bottom = min(ih, int(y2 + pad_y))
        crop = img.crop((left, top, right, bottom))
        if crop.width < 8 or crop.height < 8:
            return None

        # 배율은 짧은 변 기준. 긴 변에 맞추면 띠 형태에서 확대가 거의 일어나지 않는다.
        short, long_ = min(crop.width, crop.height), max(crop.width, crop.height)
        scale = max(VERIFY_CROP_MIN_SHORT / short, out_size / long_)
        scale = min(scale, VERIFY_CROP_MAX_LONG / long_, VERIFY_CROP_MAX_UPSCALE)
        if scale > 1:
            crop = crop.resize(
                (max(8, round(crop.width * scale)), max(8, round(crop.height * scale))),
                Image.LANCZOS,
            )

        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"[Vision Verify] 크롭 실패({type(e).__name__}) - 해당 건 심사 생략: {e}")
        return None




def verify_defects_with_images(
    defects: List[Dict[str, Any]],
    image_paths: List[str],
    book_title: str = "",
    special_notes: Optional[str] = None,
) -> Optional[CriticVerdict]:
    """확정된 결함을 BBox 크롭 단위로 하나씩 원본 이미지와 대조해 심사한다.
    감점 제외는 기본적으로 NO(명백히 손상 없음)만이다. UNCLEAR 까지 뺄지는 UNCLEAR_EXCLUDES_DEDUCTION 이 정한다(기본 꺼짐). 어느 경우든 결함 자체는 지우지 않고 표식만 남긴다 - 후속 노드가 볼 근거를 보존하기 위함이다.
    Returns:
        CriticVerdict (decision / reason / suspect_indices) 또는 심사 불가 시 None.
    """
    if not llm_verify or not defects or not image_paths:
        return None

    structured = llm_verify.with_structured_output(DefectEvidenceVerdict)
    suspects: List[int] = []
    exempt = 0
    judged = 0
    skipped = 0
    notes: List[str] = []

    for i, d in enumerate(defects):
        conf = d.get("confidence")
        if (
            d.get("conf_source") == "yolo"
            and isinstance(conf, (int, float))
            and float(conf) >= VERIFY_EXEMPT_CONF
        ):
            d["verify_status"] = "exempt_high_conf"
            exempt += 1
            continue

        idx = int(d.get("image_index") or 0)
        bbox = d.get("bbox")
        if not isinstance(bbox, dict) or not (0 <= idx < len(image_paths)):
            d["verify_status"] = "skipped_no_bbox"
            skipped += 1
            continue

        # 비접지 좌표는 심사하지 않는다 - 지어낸 위치를 크롭하면 반드시 "손상 없음"이
        # 나와 실제 결함의 감점이 지워진다. 이 건은 Critic Stage A가 HITL로 보낸다.
        if d.get("bbox_ungrounded"):
            d["verify_status"] = "skipped_ungrounded_bbox"
            skipped += 1
            continue

        b64 = _crop_around_bbox(image_paths[idx], bbox)
        if not b64:
            d["verify_status"] = "skipped_crop_failed"
            skipped += 1
            continue

        dtype = str(d.get("type") or "")
        label = DEFECT_TRANSLATION_MAP.get(dtype, dtype or "상태 결함")
        prompt = f"""아래는 중고도서 검수 사진에서 결함으로 지목된 부위를 확대한 이미지입니다. 
이미지 중앙부에 "{label}"({dtype})이 실제로 보이는지만 판단하세요.
- 도서명: {book_title or "미상"}
- 판독 특이사항: {special_notes or "없음"}
[판단 기준]
- 이미지는 지목 부위를 중심으로 주변 여유를 포함해 잘라 확대한 것입니다. 가장자리가 아니라 중앙부를 보세요.
- 인쇄된 활자·삽화·표는 손상이 아닙니다. 인쇄물은 규칙적인 행·열을 이루고 색이 균일하지만, 손글씨/낙서는 필압과 기울기가 불규칙합니다.
- 손·책상·배경 등 도서가 아닌 물체 위의 표시는 손상이 아닙니다.
- 조명 반사나 그림자는 손상이 아닙니다.
- 확대 과정에서 화질이 거칠어졌더라도, 손상의 형태가 보이면 YES입니다.
- 확신이 서지 않으면 NO가 아니라 UNCLEAR를 고르세요. NO는 "명백히 손상이 없다"는 뜻이며, 이 판단은 실제 손상을 감점에서 지우는 데 쓰입니다.
"""
        try:
            res: DefectEvidenceVerdict = structured.invoke([
                HumanMessage(content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                ])
            ])
        except Exception as e:
            # 건별 fail-open: 한 건이 실패해도 나머지 심사는 계속한다.
            print(f"[Vision Verify] 결함 #{i} 심사 실패({type(e).__name__}) - 건너뜀: {e}")
            d["verify_status"] = "skipped_llm_error"
            skipped += 1
            continue

        judged += 1
        d["verify_visible"] = res.visible
        d["verify_reason"] = res.reason
        # 감점 제외 대상. 기본은 NO(명백히 손상 없음)만이며, UNCLEAR(판단 어려움)까지 뺄지는 UNCLEAR_EXCLUDES_DEDUCTION 이 정한다(선언부 주석 참조).
        d["verify_status"] = {"YES": "confirmed", "NO": "rejected"}.get(res.visible, "unclear")
        excluded = res.visible == "NO" or (
            res.visible == "UNCLEAR" and UNCLEAR_EXCLUDES_DEDUCTION
        )
        if excluded:
            suspects.append(i)
            notes.append(f"#{i} {label}: {res.reason}")

    if judged == 0 and not suspects:
        # 전건 면제/생략이면 심사를 한 적이 없다. 통과로 위장하지 않는다.
        print(f"[Vision Verify] 심사 대상 없음 (면제 {exempt}건 / 생략 {skipped}건)")
        return None

    summary = (
        f"크롭 대조 심사 {judged}건 (고확신 면제 {exempt}건"
        + (f" / 생략 {skipped}건" if skipped else "")
        + f") - 오탐 지목 {len(suspects)}건"
    )
    if notes:
        summary += " : " + " ; ".join(notes[:3])

    return CriticVerdict(
        decision="REJECTED" if suspects else "APPROVED",
        reason=summary,
        suspect_indices=suspects,
    )


def _bbox_iou(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> float:
    """두 BBox({xmin,ymin,xmax,ymax}, 0~1000 정규화)의 IoU. 좌표가 없으면 0.0.

    확정 결함이 YOLO 제보와 같은 상자인지 판정하는 데 쓴다. 값 비교(confidence)가 아니라 좌표 비교여야 하는 이유는 vision_agent 쪽 주석 참조 - VLM은 좌표를 그대로 베끼면서 확신도만 임의 값으로 덮어쓴다.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return 0.0
    try:
        ax1, ay1, ax2, ay2 = (int(a["xmin"]), int(a["ymin"]), int(a["xmax"]), int(a["ymax"]))
        bx1, by1, bx2, by2 = (int(b["xmin"]), int(b["ymin"]), int(b["xmax"]), int(b["ymax"]))
    except (KeyError, TypeError, ValueError):
        return 0.0

    iw = max(0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return (inter / union) if union > 0 else 0.0


