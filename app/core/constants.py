from enum import Enum


class BoxStandardEnum(str, Enum):
    BOX_1 = "우체국_1호"
    BOX_2 = "우체국_2호"
    BOX_3 = "우체국_3호"
    BOX_4 = "우체국_4호"
    BOX_5 = "우체국_5호"
    BOX_6 = "우체국_6호"


# [Legacy] 우체국 규격 상수 — 신규 로직은 아래 BOX_CATALOG(SSOT)를 사용한다.
# (DB/과거 API 응답 호환을 위해 Enum과 함께 보존)
BOX_STANDARDS = {
    BoxStandardEnum.BOX_1: {"width": 220, "length": 190, "height": 90, "max_weight": 2},
    BoxStandardEnum.BOX_2: {
        "width": 270,
        "length": 180,
        "height": 150,
        "max_weight": 3,
    },
    BoxStandardEnum.BOX_3: {
        "width": 340,
        "length": 250,
        "height": 210,
        "max_weight": 5,
    },
    BoxStandardEnum.BOX_4: {
        "width": 410,
        "length": 310,
        "height": 280,
        "max_weight": 10,
    },
    BoxStandardEnum.BOX_5: {
        "width": 480,
        "length": 380,
        "height": 340,
        "max_weight": 20,
    },
    BoxStandardEnum.BOX_6: {
        "width": 520,
        "length": 480,
        "height": 400,
        "max_weight": 30,
    },
}

# ============================================================
# 3D Bin Packing SSOT (Single Source of Truth) 상수
# - 프론트엔드(admin/outbound 16종 카탈로그)와 1:1 동일 규격
# ============================================================

# 도서 판형 규격 (mm)
BOOK_FORMATS = {
    "신국판": {"width": 152, "length": 225},
    "46판": {"width": 128, "length": 188},
    "4x6배판": {"width": 188, "length": 257},
}

# 판형 누락 시 카테고리 기반 폴백
CATEGORY_FALLBACK = {
    "IT": "4x6배판",
    "Textbook": "4x6배판",
    "Novel": "신국판",
    "Essay": "신국판",
    "Comic": "46판",
}

# 두께 추정 상수 (mm) — 흑백 0.05mm/p, 컬러 0.08mm/p, 양장 커버 +6.0mm
PAGE_THICKNESS_MM = {"mono": 0.05, "color": 0.08}
HARDCOVER_EXTRA_MM = 6.0
MIN_BOOK_THICKNESS_MM = 3.0  # 페이지 수 미상/극소 페이지 방어 하한

# 중량 추정 상수 — 1장(leaf)=2페이지, 평량(g/m²) 기준
PAPER_GRAMMAGE_GSM = {"mono": 80.0, "color": 120.0}
COVER_WEIGHT_G = {"hard": 150.0, "soft": 50.0}

# 완충재 체적 마진 (박스 추천 시 총 체적에 곱하는 계수)
CUSHION_MARGIN_RATIO = 1.15

# 통합 박스 카탈로그 16종 (mm / kg) — 프론트 BOOK_SLIM_BOX_OPTIONS + STD_BOX_OPTIONS와 동일
# length = 긴 변, width = 짧은 변, height = 수직 높이
BOX_CATALOG = [
    {
        "id": "BOOK-S1",
        "category": "BOOK_SLIM",
        "name": "도서슬림 소형 1호",
        "length": 250,
        "width": 150,
        "height": 50,
        "max_weight_kg": 2.0,
    },
    {
        "id": "BOOK-S2",
        "category": "BOOK_SLIM",
        "name": "도서슬림 소형 2호",
        "length": 250,
        "width": 150,
        "height": 60,
        "max_weight_kg": 3.0,
    },
    {
        "id": "BOOK-M1",
        "category": "BOOK_SLIM",
        "name": "도서슬림 중형 1호",
        "length": 300,
        "width": 200,
        "height": 70,
        "max_weight_kg": 4.0,
    },
    {
        "id": "BOOK-M2",
        "category": "BOOK_SLIM",
        "name": "도서슬림 중형 2호",
        "length": 300,
        "width": 200,
        "height": 90,
        "max_weight_kg": 5.0,
    },
    {
        "id": "BOOK-L1",
        "category": "BOOK_SLIM",
        "name": "도서슬림 대형 1호",
        "length": 350,
        "width": 250,
        "height": 100,
        "max_weight_kg": 7.0,
    },
    {
        "id": "BOOK-L2",
        "category": "BOOK_SLIM",
        "name": "도서슬림 대형 2호",
        "length": 350,
        "width": 250,
        "height": 140,
        "max_weight_kg": 8.5,
    },
    {
        "id": "BOOK-XL1",
        "category": "BOOK_SLIM",
        "name": "도서슬림 특대형 1호",
        "length": 400,
        "width": 300,
        "height": 160,
        "max_weight_kg": 10.0,
    },
    {
        "id": "BOOK-XL2",
        "category": "BOOK_SLIM",
        "name": "도서슬림 특대형 2호",
        "length": 400,
        "width": 300,
        "height": 200,
        "max_weight_kg": 12.0,
    },
    {
        "id": "STD-01",
        "category": "STANDARD",
        "name": "일반택배 1호 (소형)",
        "length": 220,
        "width": 190,
        "height": 90,
        "max_weight_kg": 5.0,
    },
    {
        "id": "STD-02",
        "category": "STANDARD",
        "name": "일반택배 2호 (중소형)",
        "length": 270,
        "width": 180,
        "height": 150,
        "max_weight_kg": 7.0,
    },
    {
        "id": "STD-03",
        "category": "STANDARD",
        "name": "일반택배 3호 (중형)",
        "length": 340,
        "width": 250,
        "height": 210,
        "max_weight_kg": 10.0,
    },
    {
        "id": "STD-04",
        "category": "STANDARD",
        "name": "일반택배 4호 (대형)",
        "length": 410,
        "width": 310,
        "height": 280,
        "max_weight_kg": 15.0,
    },
    {
        "id": "STD-05",
        "category": "STANDARD",
        "name": "일반택배 5호 (특대형 1호)",
        "length": 480,
        "width": 380,
        "height": 340,
        "max_weight_kg": 20.0,
    },
    {
        "id": "STD-06",
        "category": "STANDARD",
        "name": "일반택배 6호 (특대형 2호)",
        "length": 530,
        "width": 410,
        "height": 400,
        "max_weight_kg": 25.0,
    },
    {
        "id": "STD-07",
        "category": "STANDARD",
        "name": "일반택배 7호 (초대형 점포용)",
        "length": 600,
        "width": 450,
        "height": 450,
        "max_weight_kg": 30.0,
    },
    {
        "id": "STD-08",
        "category": "STANDARD",
        "name": "일반택배 8호 (마스터 카톤)",
        "length": 650,
        "width": 500,
        "height": 500,
        "max_weight_kg": 35.0,
    },
]


# 작업자/검수자 표기 정본: `WM2608001(장문경)` — 사번과 이름 사이에 공백을 넣지 않는다.
# 같은 사람이 화면마다 다른 형태로 보이면 동일인 판별이 어려워지므로 생성부를 한 곳으로 모은다.
import re as _re

_WORKER_LABEL_RE = _re.compile(r"^\s*([A-Za-z]{2}\d{6,})\s*\(\s*(.+?)\s*\)\s*$")


def format_worker_label(employee_id: str = None, name: str = None) -> str:
    """사번+이름을 정본 형식으로 만든다. 이름을 모르면 사번만, 사번도 없으면 미기록.

    employee_id에 이미 `WM2608001 (장문경)` 같은 표시용 문자열이 들어와도 정본으로 되돌린다
    (과거 발주 승인 경로가 표시용 라벨을 사번 컬럼에 저장한 이력이 있다).
    """
    emp = (employee_id or "").strip()
    nm = (name or "").strip()

    m = _WORKER_LABEL_RE.match(emp)
    if m:
        emp, nm = m.group(1), (nm or m.group(2))

    if not emp:
        return nm or "작업자 미기록"
    return f"{emp}({nm})" if nm else emp
