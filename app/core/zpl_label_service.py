"""
50mm × 31mm LPN/UBCI 라벨 ZPL 생성 서비스 (다이컷 라벨 RS5031 규격).

QR에는 LPN 원문 대신 /lpn/{lpn} URL을 넣어 작업자 스캔과 소비자 보증서 조회를
하나의 QR로 통합한다. /lpn/[lpn] 페이지 자체가 로그인 역할(WORKER/ADMIN/MASTER)이면
내부 상세를, 아니면 /certificate/{lpn} 공개 보증서로 자동 전환해 보여준다 — 이 함수는
그 진입점만 만든다.

레이아웃은 제조사 인쇄 여백(왼쪽 1.5mm / 위쪽 1.44mm)에 미관용 여유 인셋을 더해
^LH로 원점을 밀어낸 뒤, 그 안에 액자형 테두리 + QR/텍스트 2단 구성을 그린다.
좌표는 전부 이 원점 기준 상대값이다.

실측 인쇄에서 테두리가 물리적 왼쪽 가장자리에 붙어 보인다는 피드백으로 _VISUAL_INSET_DOTS를 추가했다. 제조사 여백은 "인쇄 가능한 최소 여백"이지 "보기 좋은 여백"이 아니므로, 최소 여백 위에 순수 미관용 여유를 더 얹는다. 같은 실측에서 12dot(≈1.5mm) 폰트로 찍은 하단 문구 ("SCAN FOR ITEM OR CERTIFICATE")의 F/R 획이 열전사 헤드 해상도에서 뭉개져 읽기 어려웠던 것도 확인해, 하단 문구 폰트를 15dot로 키웠다.
"""

from decimal import Decimal
from pathlib import Path

from app.core.config import settings

# 한글 라벨 텍스트 렌더용 폰트 (나눔고딕 Bold, OFL 라이선스).
# 프린터 내장 폰트(A0)는 한글 글리프가 없어 한글 줄이 통째로 누락되거나 깨진다
# 비ASCII 텍스트는 서버에서 비트맵(^GFA)으로 렌더한다.
# Bold를 쓰는 이유: 203dpi 열전사에서 Regular의 가는 획이 끊겨 보인다 (실측 피드백).
_KOREAN_FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NanumGothic-Bold.ttf"
)


def build_certificate_qr_url(lpn_barcode: str) -> str:
    """라벨 QR에 넣을 URL을 생성한다. /lpn/[lpn]이 로그인 역할에 따라 내부 상세/공개 보증서로 스스로 갈라진다."""
    base = settings.PUBLIC_WEB_BASE_URL.rstrip("/")
    return f"{base}/lpn/{lpn_barcode}"


def _mm_to_dots(length_mm: float) -> int:
    """
    mm 단위를 프린터 해상도 기준 dot 단위로 변환한다.

    203 DPI 기준으로 50mm × 31mm는 약 400 × 248 dots다.
    """
    return round(length_mm * settings.LABEL_PRINTER_DPI / 25.4)


def _sanitize_zpl_text(value: str) -> str:
    """DB 텍스트가 ZPL 제어문자로 해석되지 않도록 정리한다."""
    return (
        value.replace("^", " ")
        .replace("~", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


# 미관용 여유 인셋. 제조사 최소 인쇄 여백(1.5mm/1.44mm)과 별개로, 테두리가
# 물리적 가장자리에 붙어 보이지 않도록 추가로 밀어내는 값이다.
_VISUAL_INSET_DOTS = 14

# 프레임 내부 2단 레이아웃 고정 좌표 (원점 기준). 실측 인쇄 피드백을 반영해 프레임을 원점에서 한 번 더 안쪽으로 넣고, 하단 문구 폰트를 15dot로 키웠다.
# 실측 인쇄(LPN-260808-TEST)에서 QR이 구분선(당시 138)과 텍스트 컬럼을 침범했다. /certificate/{lpn} 풀 URL을 인코딩하는 QR은 예상(mag5≈128dot) 보다 커서(mag5 실측 ≈144dot) BQN 배율을 5→4로 낮추고, 그만큼 좁아진 QR 폭에 맞춰 구분선·텍스트 컬럼 좌표를 다시 뒤로 뺐다.
_FRAME_WIDTH = 360
_FRAME_HEIGHT = 212
_QR_MAGNIFICATION = 4
_DIVIDER_X = 134
_DIVIDER_TOP = 40
_DIVIDER_LENGTH = 130
_TEXT_COL_X = 148
_FOOTER_RULE_Y = 185
_FOOTER_TEXT_Y = 190
_FOOTER_FONT_SIZE = 15


def _build_label_header() -> list[str]:
    """50mm * 31mm 라벨에 공통으로 적용할 ZPL 헤더다. 인쇄 여백 + 미관용 인셋만큼 원점을 민다."""
    label_width = _mm_to_dots(settings.LABEL_PRINTER_LABEL_WIDTH_MM)
    label_height = _mm_to_dots(settings.LABEL_PRINTER_LABEL_HEIGHT_MM)
    margin_left = (
        _mm_to_dots(settings.LABEL_PRINTER_MARGIN_LEFT_MM) + _VISUAL_INSET_DOTS
    )
    margin_top = _mm_to_dots(settings.LABEL_PRINTER_MARGIN_TOP_MM) + _VISUAL_INSET_DOTS

    return [
        "^XA",
        # 실제 프린터의 한글 설정에 따라 추후 조정할 수 있다.
        "^CI28",
        f"^PW{label_width}",
        f"^LL{label_height}",
        f"^LH{margin_left},{margin_top}",
    ]


def _build_frame_zpl(*, title: str, title_font_size: int = 22) -> list[str]:
    """
    액자형 테두리 + 제목 밑줄 + QR/텍스트 구분선을 그린다.

    테두리 안쪽에 10dot 안팎의 여백을 둬 인쇄 원점(^LH)과 별개로 내용이
    테두리 선에 바로 붙지 않게 한다.
    """
    return [
        f"^FO10,8^A0N,{title_font_size},{title_font_size}^FD{title}^FS",
        f"^FO0,0^GB{_FRAME_WIDTH},{_FRAME_HEIGHT},2^FS",
        "^FO10,32^GB340,1,1^FS",
        f"^FO{_DIVIDER_X},{_DIVIDER_TOP}^GB1,{_DIVIDER_LENGTH},1^FS",
        f"^FO10,{_FOOTER_RULE_Y}^GB340,1,1^FS",
    ]


def _text_field_zpl(x: int, y: int, text: str, height: int, max_width: int) -> str:
    """텍스트 한 줄의 ZPL 필드를 만든다.

    ASCII 전용이면 프린터 내장 폰트(A0, 선명함)를 쓰고, 한글 등 비ASCII가 섞이면
    나눔고딕으로 서버에서 1bpp 비트맵을 그려 ^GFA로 보낸다. 폭 초과분은 잘라낸다.
    """
    if not text:
        return ""
    if text.isascii():
        return f"^FO{x},{y}^A0N,{height},{height}^FD{text}^FS"

    from PIL import Image, ImageDraw, ImageFont

    # 2배 슈퍼샘플링: 목표 크기의 2배로 그레이스케일 렌더 후 축소·이진화한다.
    # 목표 크기에 직접 1bpp로 그리면 외곽선이 계단식으로 끊긴다 (실측 피드백).
    SS = 2
    font = ImageFont.truetype(str(_KOREAN_FONT_PATH), height * SS)
    t = text
    while t and font.getbbox(t)[2] > max_width * SS:
        t = t[:-1]
    if not t:
        return ""
    bbox = font.getbbox(t)
    w_ss = min(max_width * SS, bbox[2])
    h_ss = height * SS + max(0, bbox[3] - height * SS)  # 아래로 삐치는 글리프 여유
    big = Image.new("L", (w_ss, h_ss), 255)
    ImageDraw.Draw(big).text((0, 0), t, font=font, fill=0)
    w, h = w_ss // SS, h_ss // SS
    # 행 끝 패딩 비트가 검은 점으로 찍히지 않도록 폭을 8의 배수로 맞춘다
    w8 = (w + 7) // 8 * 8
    small = big.resize((w, h), Image.LANCZOS)
    canvas = Image.new("L", (w8, h), 255)
    canvas.paste(small, (0, 0))
    # 임계값 170: 축소로 생긴 회색 경계 픽셀을 검정에 넉넉히 편입해 획을 살짝 두껍게 유지
    img = canvas.point(lambda p: 0 if p < 170 else 255, mode="1")
    row_bytes = w8 // 8
    total = row_bytes * h
    # PIL mode '1' tobytes: 흰색=1비트. ZPL은 1비트=검정이므로 반전한다.
    hex_data = bytes((~b) & 0xFF for b in img.tobytes()).hex().upper()
    return f"^FO{x},{y}^GFA,{total},{total},{row_bytes},{hex_data}^FS"


def _build_scan_qr_zpl(lpn_barcode: str) -> str:
    """
    작업자·소비자가 공통으로 스캔할 QR ZPL을 생성한다.

    QR에는 LPN 원문이 아니라 프론트의 /certificate/{lpn} URL을 넣는다.
    """
    scan_url = build_certificate_qr_url(lpn_barcode)
    return f"^FO10,{_DIVIDER_TOP}^BQN,2,{_QR_MAGNIFICATION}^FDLA,{scan_url}^FS"


def build_lpn_label_zpl(
    *,
    lpn_barcode: str,
    book_title: str = "",
    isbn: str = "",
    worker_id: str = "",
) -> str:
    """
    입고 접수 직후 책에 부착할 초기 LPN QR 라벨 ZPL을 생성한다.

    선부착 시점(ISBN 스캔 → 알라딘 조회 → LPN 발급 → 촬영/검수 순)에 이미 확정된
    도서 식별 정보(제목/ISBN)와 접수 작업자만 싣는다. 등급·UBCI 점수는 이 시점에
    아직 존재하지 않으므로(검수는 다음 단계) 출력하지 않는다 - 등급이 바뀔 때마다
    실물 라벨을 재부착해야 하는 문제도 함께 피한다. 최신 등급/가격은 QR이 가리키는
    /certificate/{lpn} 페이지에서 항상 동적으로 조회된다.
    """
    safe_lpn = _sanitize_zpl_text(lpn_barcode)
    safe_title = _sanitize_zpl_text(book_title) or "-"
    safe_isbn = _sanitize_zpl_text(isbn) or "-"
    safe_worker = _sanitize_zpl_text(worker_id) or "-"
    text_max_w = _FRAME_WIDTH - _TEXT_COL_X - 10

    commands = [
        *_build_label_header(),
        *_build_frame_zpl(title="NEXUS LPN LABEL"),
        _build_scan_qr_zpl(lpn_barcode),
        f"^FO{_TEXT_COL_X},46^A0N,20,20^FD{safe_lpn}^FS",
        _text_field_zpl(_TEXT_COL_X, 74, safe_title, 24, text_max_w),
        f"^FO{_TEXT_COL_X},106^A0N,18,18^FDISBN: {safe_isbn}^FS",
        _text_field_zpl(_TEXT_COL_X, 132, safe_worker, 18, text_max_w),
        f"^FO10,{_FOOTER_TEXT_Y}^A0N,{_FOOTER_FONT_SIZE},{_FOOTER_FONT_SIZE}^FDSCAN FOR ITEM OR CERTIFICATE^FS",
        "^XZ",
    ]

    return "\n".join(c for c in commands if c)


def build_ubci_label_zpl(
    *,
    lpn_barcode: str,
    condition_grade: str,
    ubci_score: Decimal | float | None,
) -> str:
    """
    검수 확정 후 사용할 UBCI QR 라벨 ZPL을 생성한다.

    초기 LPN 라벨과 같은 QR을 사용하고, 확정 등급·UBCI 점수를 출력한다.
    """
    safe_lpn = _sanitize_zpl_text(lpn_barcode)
    safe_grade = _sanitize_zpl_text(condition_grade)
    score_text = f"{ubci_score:.2f}" if ubci_score is not None else "-"

    commands = [
        *_build_label_header(),
        *_build_frame_zpl(title="NEXUS UBCI CERTIFICATE", title_font_size=18),
        _build_scan_qr_zpl(lpn_barcode),
        f"^FO{_TEXT_COL_X},46^A0N,20,20^FD{safe_lpn}^FS",
        f"^FO{_TEXT_COL_X},80^A0N,15,15^FDGRADE: {safe_grade}^FS",
        f"^FO{_TEXT_COL_X},111^A0N,15,15^FDUBCI: {score_text}^FS",
        f"^FO{_TEXT_COL_X},140^A0N,13,13^FDSCAN FOR CERTIFICATE^FS",
        f"^FO10,{_FOOTER_TEXT_Y}^A0N,{_FOOTER_FONT_SIZE},{_FOOTER_FONT_SIZE}^FDSCAN FOR ITEM OR CERTIFICATE^FS",
        "^XZ",
    ]

    return "\n".join(c for c in commands if c)
