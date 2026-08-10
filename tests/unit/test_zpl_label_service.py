"""ZPL 라벨 생성 서비스 단위 테스트."""
from decimal import Decimal

from app.core.config import settings
from app.core.zpl_label_service import (
    build_certificate_qr_url,
    build_lpn_label_zpl,
    build_ubci_label_zpl,
)


def test_certificate_qr_url_points_to_lpn_route():
    url = build_certificate_qr_url("LPN-20260805-0001")
    assert url == (
        f"{settings.PUBLIC_WEB_BASE_URL.rstrip('/')}"
        "/lpn/LPN-20260805-0001"
    )


def test_lpn_label_contains_qr_and_barcode_text():
    zpl = build_lpn_label_zpl(lpn_barcode="LPN-20260805-0001")

    assert zpl.startswith("^XA")
    assert zpl.endswith("^XZ")
    # QR 목적지는 /lpn/{lpn} — 로그인 역할에 따라 내부 상세/공개 보증서로 자동 분기된다
    assert "/lpn/LPN-20260805-0001" in zpl
    assert "LPN-20260805-0001" in zpl
    # 검수 전 라벨에는 등급이 출력되면 안 된다
    assert "GRADE" not in zpl


def test_ubci_label_contains_grade_and_score():
    zpl = build_ubci_label_zpl(
        lpn_barcode="LPN-20260805-0002",
        condition_grade="GOOD",
        ubci_score=Decimal("87.50"),
    )

    assert "GRADE: GOOD" in zpl
    assert "UBCI: 87.50" in zpl


def test_ubci_label_without_score_prints_dash():
    zpl = build_ubci_label_zpl(
        lpn_barcode="LPN-20260805-0003",
        condition_grade="MINT",
        ubci_score=None,
    )

    assert "UBCI: -" in zpl


def test_zpl_control_characters_are_sanitized():
    zpl = build_ubci_label_zpl(
        lpn_barcode="LPN^~EVIL",
        condition_grade="GO^OD",
        ubci_score=None,
    )

    # 본문 텍스트 필드에서 ^, ~가 제거되어 ZPL 명령으로 오해되지 않아야 한다
    assert "LPN  EVIL" in zpl
    assert "GRADE: GO OD" in zpl


def test_label_dimensions_match_203dpi_50x31mm():
    zpl = build_lpn_label_zpl(lpn_barcode="LPN-DIM-TEST")

    # 203 DPI 기준 50mm=400dots, 31mm=248dots (RS5031 다이컷 라벨 실측 규격)
    assert "^PW400" in zpl
    assert "^LL248" in zpl
