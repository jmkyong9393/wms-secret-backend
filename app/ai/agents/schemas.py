"""
LangGraph 노드의 구조화 출력 스키마.

[설계 원칙] 모든 LLM 응답은 with_structured_output으로 이 스키마에 강제된다.
응답 텍스트를 직접 파싱하면 모델이 코드블록이나 서론 한 줄만 덧붙여도 노드가 죽는다.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class InnerPageRegion(BaseModel):
    """속지(펼친 내지) 사진에서 낙서 탐지를 돌릴 영역.

    [Track 2·3] doodle 모델은 AIHub 손글씨 **크롭 패치** 1만 장으로 학습돼 있어, 인쇄면 전체를 그대로 넣으면 활자를 손글씨로 오인한다(실측: 깨끗한 속지 1장에 오탐 12건, 전부 인쇄 본문). VLM이 지면 영역을 먼저 좁혀 주면 학습 도메인에 가까운 입력이 되고, 손이나 배경도 함께 제외된다.
    """

    # 좌표를 Dict가 아니라 평탄한 int 필드로 둔다. OpenAI structured output(strict)은 Dict[str, int]를 필수 필드로 쓰면 "Extra required key" 오류로 요청 자체를 거부한다.
    image_index: int = Field(description="이 영역이 속한 원본 이미지의 인덱스 (0부터)")
    xmin: int = Field(description="지면 영역 좌측 x (0~1000 상대좌표)")
    ymin: int = Field(description="지면 영역 상단 y (0~1000 상대좌표)")
    xmax: int = Field(description="지면 영역 우측 x (0~1000 상대좌표)")
    ymax: int = Field(description="지면 영역 하단 y (0~1000 상대좌표)")


class DefectDetail(BaseModel):
    type: str = Field(
        description="결함의 종류 (예: DMG_INT_DOODLE, DMG_INT_STAIN, DMG_EXT_CRUSH, DMG_EXT_WET 등)"
    )
    ratio: int = Field(description="전체 면적 대비 결함의 상대적 비율 (%)")
    level: Optional[int] = Field(
        default=None,
        description="변색/황변(DMG_INT_DISCOLOR) 전용 강도 1~3. 황변은 지면 전체에 나타나 면적(ratio)이 항상 100%가 되므로 면적 대신 강도로 판정한다. 1=종이 끝만 살짝 바램(자연 노화) / 2=전반적으로 뚜렷한 황변 / 3=짙은 갈색·곰팡이성 얼룩 동반. 변색이 아닌 결함에는 넣지 않는다",
    )
    preliminary_deduction: int = Field(
        description="4o-mini가 1차 계산한 예비 감점 수치", default=10
    )
    bbox: Optional[Dict[str, int]] = Field(
        default=None,
        description="결함의 2D Bounding Box, {xmin,ymin,xmax,ymax} 0~1000 상대좌표 (wbf_detector.py의 bbox 출력과 동일 키)",
    )
    confidence: Optional[float] = Field(
        default=None,
        description="결함 판독 신뢰도 (0.0~1.0) - WBF 후보와 일치하면 해당 confidence, VLM 단독 판독이면 VLM 자체 추정치",
    )
    text_overlap: bool = Field(
        default=False,
        description="결함이 도서의 제목/본문 텍스트 영역을 침범했는지 여부 - UBCI 1.5배 가중치 판단에 사용 (Policy Agent가 이 필드를 읽음)",
    )
    image_index: Optional[int] = Field(
        default=None,
        description="결함이 발견된 원본 이미지의 인덱스 (0=정면, 1=후면, 2번째~=내지/측면 등) - 프론트 이미지별 BBox 오버레이 1:1 매핑에 사용",
    )


class VisionResult(BaseModel):
    is_mint: bool = Field(description="결함이 전혀 없는 완전한 새 책(Mint)인지 여부")
    defects: List[DefectDetail] = Field(
        description="결함 리스트. Mint인 경우 빈 리스트 반환", default_factory=list
    )
    special_notes: Optional[str] = Field(
        default=None,
        description="UBCI 감점과 무관한 정성적 관찰 (도서관 장서 도장, 부록 CD 누락, 저자 친필 서명 등)",
    )
    # 현장 촬영 컷 중 도서가 식별되지 않는 이미지(작업자 얼굴만 찍힘, 빈 배경, 심한 초점 이탈 등)의 인덱스 목록. HITL/상세 화면이 해당 컷을 "결함 미검출(정상처럼 보임)"이 아니라 "도서 미식별 컷"으로 구분·필터링하는 데 쓴다.
    invalid_image_indexes: List[int] = Field(
        default_factory=list,
        description="도서가 식별되지 않는 이미지의 인덱스 목록 (0=정면 촬영 순서 기준). 모든 컷에 도서가 보이면 빈 배열",
    )
    # 속지 컷의 지면 영역. doodle 모델을 이 영역에만 돌린다.
    inner_page_regions: List[InnerPageRegion] = Field(
        default_factory=list,
        description="펼친 속지(내지)가 찍힌 컷의 지면 영역 목록. 속지 컷이 없으면 빈 배열",
    )


class PolicyResult(BaseModel):
    ubci_score: int = Field(description="계산된 최종 UBCI 점수 (0~100)")
    ubci_grade: str = Field(description="최종 등급 (S, A, B, REJECT)")
    decision: str = Field(description="입고 처분 결정 (APPROVE, DOWNGRADE, REJECT)")


class CriticResult(BaseModel):
    reason_code: Literal[
        "OK", "REJECT", "MAX_RETRIES_AMBIGUOUS_HITL", "BOUNDARY_AMBIGUOUS_HITL"
    ] = Field(description="프로세스 검증 통과 여부 및 HITL 이관 코드")
    repair_directive: Optional[str] = Field(
        description="REJECT 또는 HITL 이관 시 수정 지시 가이드라인"
    )


class CriticVerdict(BaseModel):
    """
    Critic Stage B(LLM 판독 타당성 심사)의 구조화 출력.

    [설계 노트] 이 스키마는 with_structured_output으로 강제된다. 응답 텍스트를 ast.literal_eval / json.loads로 파싱하는 방식은 모델이 코드블록(```)이나 서론을 한 줄만 덧붙여도 예외가 나면서 노드 전체가 죽는다 - 프롬프트로 "코드 블록 제거"를 부탁하는 대신 스키마로 보장받는다. decision도 자유 문자열이 아닌 Literal이라 "Approved", "APPROVE" 같은 변형이 원천 차단된다.
    """

    decision: Literal["APPROVED", "REJECTED"] = Field(
        description="판독 타당성 승인 여부"
    )
    reason: str = Field(description="판정 근거 한 문장 (한국어)")
    suspect_indices: List[int] = Field(
        default_factory=list,
        description="오탐(False Positive)이 의심되는 결함의 defects 배열 인덱스 목록. 없으면 빈 배열",
    )


class PolicyClauseCitation(BaseModel):
    """Stage B 판단의 근거가 된 규정 조항 1건."""

    chunk_id: str = Field(
        default="", description="지식베이스 청크 ID (내부 감사용, 고객 노출 금지)"
    )
    doc_title: str = Field(default="", description="규정 문서 공식 명칭")
    clause_ref: str = Field(default="", description="조항 번호. 없으면 빈 문자열")
    authority_rank: Optional[int] = Field(
        default=None,
        description="1=법령 2=약관 3=운영정책 4=가이드 5=내부기준. 낮을수록 강제성이 높다",
    )


class ReturnPolicyVerdict(BaseModel):
    """
    Policy Agent (거래 처분 판단)의 구조화 출력.

    [Stage A와의 경계 - CRITICAL]
    이 스키마에는 점수·등급 필드가 의도적으로 없다. UBCI 점수는 Stage A의 결정론적 매트릭스가 확정하며, Stage B는 그 값을 바꿀 수단 자체를 갖지 않는다. 매입가를 결정하는 값에 LLM이 개입하면 같은 도서가 실행할 때마다 다른 등급을 받아 감사 추적성이 깨지기 때문이다.

    [무엇을 판단하는가]
    감점 매트릭스로 환원되지 않는 것들 - 반품을 받아줄 것인가, 배송비는 누가 무는가, 귀책은 어디인가. 조건이 여럿이고 플랫폼마다 달라 규정 해석이 필요한 영역이다.
    """

    return_accepted: Optional[bool] = Field(
        default=None,
        description="반품/매입 수용 여부. 규정으로 판단할 수 없으면 null (임의 추정 금지)",
    )
    shipping_fee_bearer: Optional[Literal["CUSTOMER", "SELLER", "CARRIER"]] = Field(
        default=None,
        description="반품 배송비 부담 주체. 근거가 없으면 null",
    )
    liability: Literal["CUSTOMER", "CARRIER", "WAREHOUSE", "UNDETERMINED"] = Field(
        default="UNDETERMINED",
        description="귀책 주체 **후보**. 표준 운영 정책서 제0장 ④에 따라 AI가 단독 확정하지 않는다. 증빙 2개 이상이 일치하지 않으면 반드시 UNDETERMINED",
    )
    refund_ratio: Optional[float] = Field(
        default=None,
        description="환불 비율 0.0~1.0. 해외주문 수수료 공제처럼 규정에 명시된 공제가 있을 때만 채운다. 근거 없이 추정하지 않는다",
    )
    cited_clauses: List[PolicyClauseCitation] = Field(
        default_factory=list,
        description="위 판단의 근거 조항. 비어 있으면 판단은 무효다",
    )
    requires_human: bool = Field(
        default=True,
        description="관리자 결재가 필요한가. 규정 근거가 불충분하거나 귀책이 UNDETERMINED면 반드시 true",
    )
    rationale: str = Field(
        default="",
        description="판단 근거 2~4문장 한국어. 인용한 조항명을 문장 안에 밝힐 것",
    )


class QualityCertificateResult(BaseModel):
    cert_id: str = Field(description="발급된 디지털 WMS 검수 보증서 고유 번호")
    certificate_text: str = Field(description="디지털 검수 보증서 전문")


class DefectFinding(BaseModel):
    """고객용 보증서에 노출되는 결함 항목 1건의 서술.

    [주의] 탐지 건수와 1:1이 아니다. 같은 유형이 같은 부위에서 여러 번 잡히거나(모서리 마모는 한 구석이 여러 컷에 걸쳐 검출된다.) 묶음으로 감점되는 유형은 고객에게 하나의 항목으로 보여야 한다. 종전에는 findings 길이를 결함 배열 길이와 강제로 맞춰서, 마모 3건이 잡히면 "또 발견되었습니다"가 3줄 나왔다.
    """

    image_index: int = Field(
        default=0, description="이 항목을 대표하는 원본 검수 이미지 인덱스"
    )
    location: str = Field(
        default="",
        description="고객이 읽는 위치 표현 (예: 앞표지 아래쪽 모서리, 책등 하단). 좌표·인덱스 같은 내부 표기를 쓰지 않는다",
    )
    label: str = Field(
        description="결함 이름 (예: 내지 손글씨/낙서). 코드가 아닌 사람이 읽는 한국어"
    )
    deduction: int = Field(
        default=0,
        description="이 항목으로 차감된 UBCI 점수 (묶음이면 묶음 합계를 한 번만)",
    )
    reason: str = Field(
        description="상세 사유 한 문장. 고객이 읽는 문장이므로 결함을 숨기지 않되 위트 있고 정중하게 포장"
    )


class CertificateDocument(BaseModel):
    """
    Report Agent가 생성하는 고객 공개용 보증서 본문.
    프론트(/certificate/[lpn])는 이 필드들을 그대로 렌더하기만 하며, 어떤 문장도 프론트에서 조립하지 않는다.
    """

    headline: str = Field(
        description="보증서 상단 한 줄 총평. 위트 있게, 단 과장 광고는 금지 (예: '표지부터 마지막 장까지, 흠잡을 데가 없었습니다')"
    )
    summary: str = Field(
        description="종합 소견 2~3문장. 검수 방식과 판정 결과를 고객 눈높이로 설명"
    )
    condition_detail: str = Field(
        description="상세 사유 본문. 결함이 하나도 없으면 '결함 없음'을 위트 있게 풀어 쓰고, 있으면 어떤 결함이 어떻게 반영됐는지 정직하게 서술"
    )
    findings: List[DefectFinding] = Field(
        default_factory=list, description="결함별 상세 내역. 결함이 없으면 빈 리스트"
    )
    care_tip: Optional[str] = Field(
        default=None, description="이 도서 상태에 맞는 짧은 보관/사용 팁 한 줄"
    )
    policy_notice: Optional[str] = Field(
        default=None,
        description="반품 처분 안내 1~2문장. Policy Stage B가 규정 근거로 판단한 반품 수용 여부와 배송비 부담을 고객 눈높이로 설명한다. 판단이 없거나 관리자 확인중이면 null. 귀책(누구 잘못인지)은 절대 쓰지 않는다",
    )
    policy_basis: List[str] = Field(
        default_factory=list,
        description="위 안내의 근거 조항 표기 목록 (예: '표준 운영 정책서 제16조의3'). 내부 chunk_id는 절대 넣지 않는다",
    )


class DefectEvidenceVerdict(BaseModel):
    """크롭 1장에 대한 단일 결함 심사 결과."""

    visible: Literal["YES", "NO", "UNCLEAR"] = Field(
        description="확대된 이미지 중앙부에 보고된 유형의 손상이 실제로 보이는가. 보이면 YES, 명백히 보이지 않으면(깨끗한 면·인쇄물·배경 등) NO, 판단이 어려우면 UNCLEAR."
    )
    reason: str = Field(description="판단 근거 한 문장 (한국어)")
