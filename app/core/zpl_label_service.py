"""
50mm × 31mm LPN/UBCI 라벨 ZPL 생성 서비스 (다이컷 라벨 RS5031 규격).

QR에는 LPN 원문 대신 공개 품질보증서 /certificate/{lpn} URL을 넣어
작업자 스캔과 소비자 보증서 조회를 하나의 QR로 통합한다.

레이아웃은 제조사 인쇄 여백(왼쪽 1.5mm / 위쪽 1.44mm)에 미관용 여유 인셋을 더해
^LH로 원점을 밀어낸 뒤, 그 안에 액자형 테두리 + QR/텍스트 2단 구성을 그린다.
좌표는 전부 이 원점 기준 상대값이다.

[2026-08-08] 실측 인쇄(LPN-260803-B007)에서 테두리가 물리적 왼쪽 가장자리에
붙어 보인다는 피드백으로 _VISUAL_INSET_DOTS를 추가했다. 제조사 여백은 "인쇄
가능한 최소 여백"이지 "보기 좋은 여백"이 아니므로, 최소 여백 위에 순수 미관용
여유를 더 얹는다. 같은 실측에서 12dot(≈1.5mm) 폰트로 찍은 하단 문구
("SCAN FOR ITEM OR CERTIFICATE")의 F/R 획이 열전사 헤드 해상도에서 뭉개져
읽기 어려웠던 것도 확인해, 하단 문구 폰트를 15dot로 키웠다.
"""
from decimal import Decimal

from app.core.config import settings


def build_certificate_qr_url(lpn_barcode: str) -> str:
    """라벨 QR에 넣을 공개 품질보증서 URL을 생성한다."""
    base = settings.PUBLIC_WEB_BASE_URL.rstrip("/")
    return f"{base}/certificate/{lpn_barcode}"


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

# 프레임 내부 2단 레이아웃 고정 좌표 (원점 기준). 실측 인쇄(LPN-260803-B007)
# 피드백을 반영해 프레임을 원점에서 한 번 더 안쪽으로 넣고, 하단 문구 폰트를
# 15dot로 키웠다.
#
# [2026-08-08] 실측 인쇄(LPN-260808-TEST)에서 QR이 구분선(당시 138)과 텍스트
# 컬럼을 침범했다. /certificate/{lpn} 풀 URL을 인코딩하는 QR은 예상(mag5≈128dot)
# 보다 커서(mag5 실측 ≈144dot) BQN 배율을 5→4로 낮추고, 그만큼 좁아진 QR 폭에
# 맞춰 구분선·텍스트 컬럼 좌표를 다시 뒤로 뺐다.
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
    """50mm × 31mm 라벨에 공통으로 적용할 ZPL 헤더다. 인쇄 여백 + 미관용 인셋만큼 원점을 민다."""
    label_width = _mm_to_dots(settings.LABEL_PRINTER_LABEL_WIDTH_MM)
    label_height = _mm_to_dots(settings.LABEL_PRINTER_LABEL_HEIGHT_MM)
    margin_left = _mm_to_dots(settings.LABEL_PRINTER_MARGIN_LEFT_MM) + _VISUAL_INSET_DOTS
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


def _build_scan_qr_zpl(lpn_barcode: str) -> str:
    """
    작업자·소비자가 공통으로 스캔할 QR ZPL을 생성한다.

    QR에는 LPN 원문이 아니라 프론트의 /certificate/{lpn} URL을 넣는다.
    """
    scan_url = build_certificate_qr_url(lpn_barcode)
    return f"^FO10,{_DIVIDER_TOP}^BQN,2,{_QR_MAGNIFICATION}^FDLA,{scan_url}^FS"


def build_lpn_label_zpl(*, lpn_barcode: str) -> str:
    """
    입고 접수 직후 책에 부착할 초기 LPN QR 라벨 ZPL을 생성한다.

    검수 전 라벨이므로 등급·UBCI 점수는 출력하지 않는다.
    동적 한글 도서명은 실제 프린터 폰트 매핑이 확정된 뒤 추가한다.
    """
    safe_lpn = _sanitize_zpl_text(lpn_barcode)

    commands = [
        *_build_label_header(),
        *_build_frame_zpl(title="NEXUS LPN LABEL"),
        _build_scan_qr_zpl(lpn_barcode),
        f"^FO{_TEXT_COL_X},46^A0N,20,20^FD{safe_lpn}^FS",
        f"^FO{_TEXT_COL_X},84^A0N,15,15^FDPENDING INSPECTION^FS",
        f"^FO{_TEXT_COL_X},117^A0N,13,13^FDSCAN WITH WMS APP^FS",
        f"^FO10,{_FOOTER_TEXT_Y}^A0N,{_FOOTER_FONT_SIZE},{_FOOTER_FONT_SIZE}^FDSCAN FOR ITEM OR CERTIFICATE^FS",
        "^XZ",
    ]

    return "\n".join(commands)


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

    return "\n".join(commands)
