# -*- coding: utf-8 -*-
"""업로드 방어 로직 검증 — 실제 공격 페이로드를 만들어 차단되는지 확인한다.

정상 파일이 통과하는지(오탐 없음)와 악성 파일이 막히는지(미탐 없음)를 함께 본다.
어느 한쪽만 검증하면 "전부 막는 필터"나 "전부 통과시키는 필터"도 통과해 버린다.
"""
import io
import zipfile

import pytest

from app.core.file_security import (
    FileSecurityError,
    detect_file_type,
    normalize_filename,
    scan_attachment,
)

# ── 정상 파일 샘플 (최소 유효 바이트) ──────────────────────────
PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
    b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPG_MIN = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 64 + b"\xff\xd9"
PDF_MIN = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"


def _make_docx(with_macro: bool = False, bomb: bool = False) -> bytes:
    """OOXML 최소 구조. 옵션으로 매크로/압축폭탄을 심는다."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")
        if with_macro:
            zf.writestr("word/vbaProject.bin", b"\x00" * 32)
        if bomb:
            zf.writestr("word/huge.bin", b"\x00" * (300 * 1024 * 1024))  # 압축은 잘 되지만 해제 시 300MB
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════
# 1. 정상 파일은 통과해야 한다 (오탐 검증)
# ══════════════════════════════════════════════════════════════
@pytest.mark.parametrize("data,ext,expected_type", [
    (PNG_1x1, ".png", "png"),
    (JPG_MIN, ".jpg", "jpg"),
    (JPG_MIN, ".jpeg", "jpg"),
    (PDF_MIN, ".pdf", "pdf"),
    (_make_docx(), ".docx", "zip"),
    ("한글 텍스트 내용입니다.".encode("utf-8"), ".txt", None),
])
def test_정상_파일은_통과한다(data, ext, expected_type):
    report = scan_attachment(data, ext)
    assert report["verdict"] == "CLEAN"
    assert report["actual_type"] == expected_type


# ══════════════════════════════════════════════════════════════
# 2. 확장자 위조 — 실행파일을 이미지로 위장 (①)
# ══════════════════════════════════════════════════════════════
@pytest.mark.parametrize("payload,label", [
    (b"MZ\x90\x00\x03" + b"\x00" * 100, "PE(Windows exe)"),
    (b"\x7fELF\x02\x01\x01" + b"\x00" * 100, "ELF(Linux)"),
    (b"\xca\xfe\xba\xbe\x00\x00\x004" + b"\x00" * 50, "Java class"),
    (b"#!/bin/sh\nrm -rf /\n", "셸 스크립트"),
])
def test_실행파일은_확장자를_위장해도_차단된다(payload, label):
    with pytest.raises(FileSecurityError) as e:
        scan_attachment(payload, ".png")
    assert e.value.code == "EXECUTABLE_CONTENT", label


def test_확장자와_실제형식_불일치_차단():
    # 내용은 PNG인데 .pdf로 올린 경우
    with pytest.raises(FileSecurityError) as e:
        scan_attachment(PNG_1x1, ".pdf")
    assert e.value.code == "EXT_CONTENT_MISMATCH"


def test_시그니처_인식불가_차단():
    with pytest.raises(FileSecurityError) as e:
        scan_attachment(b"random bytes without any signature", ".png")
    assert e.value.code == "UNKNOWN_SIGNATURE"


# ══════════════════════════════════════════════════════════════
# 3. Polyglot — 유효한 JPEG 뒤에 ZIP을 이어붙임 (②)
# ══════════════════════════════════════════════════════════════
def test_polyglot_jpeg_zip은_docx로_위장해도_차단된다():
    zip_part = _make_docx()
    polyglot = JPG_MIN + zip_part          # 앞은 JPEG, 뒤는 ZIP
    # .docx로 올리면 시그니처가 jpg로 판정되어 불일치로 걸린다
    with pytest.raises(FileSecurityError) as e:
        scan_attachment(polyglot, ".docx")
    assert e.value.code == "EXT_CONTENT_MISMATCH"


# ══════════════════════════════════════════════════════════════
# 4. Office 매크로 (③)
# ══════════════════════════════════════════════════════════════
def test_매크로_포함_문서_차단():
    with pytest.raises(FileSecurityError) as e:
        scan_attachment(_make_docx(with_macro=True), ".docx")
    assert e.value.code == "MACRO_DETECTED"


def test_손상된_OOXML_차단():
    with pytest.raises(FileSecurityError) as e:
        scan_attachment(b"PK\x03\x04" + b"\xff" * 200, ".docx")
    assert e.value.code == "OOXML_BROKEN"


# ══════════════════════════════════════════════════════════════
# 5. 압축·이미지 폭탄 (④)
# ══════════════════════════════════════════════════════════════
def test_압축폭탄_차단():
    with pytest.raises(FileSecurityError) as e:
        scan_attachment(_make_docx(bomb=True), ".docx")
    assert e.value.code in {"ZIP_BOMB_SIZE", "ZIP_BOMB_RATIO"}


def test_이미지_디컴프레션_폭탄_차단():
    pytest.importorskip("PIL")
    from PIL import Image
    # 파일 크기는 작지만 픽셀 수가 상한을 넘는 PNG (단색이라 압축률이 극단적)
    buf = io.BytesIO()
    Image.new("L", (12000, 12000)).save(buf, format="PNG")
    data = buf.getvalue()
    assert len(data) < 5 * 1024 * 1024, "폭탄 샘플이 5MB 제한 안에 들어와야 의미가 있다"
    with pytest.raises(FileSecurityError) as e:
        scan_attachment(data, ".png")
    assert e.value.code == "IMAGE_BOMB"


# ══════════════════════════════════════════════════════════════
# 6. 파일명 트릭 (⑤)
# ══════════════════════════════════════════════════════════════
def test_RTL_override_파일명_차단():
    # 화면에는 "photoexe.png"처럼 보이지만 실제로는 .exe
    with pytest.raises(FileSecurityError) as e:
        normalize_filename("photo‮gnp.exe")
    assert e.value.code == "FILENAME_BIDI"


def test_이중확장자_차단():
    with pytest.raises(FileSecurityError) as e:
        normalize_filename("report.exe.png")
    assert e.value.code == "FILENAME_DOUBLE_EXT"


def test_정상_파일명은_통과한다():
    assert normalize_filename("검수 보고서_2026.docx") == "검수 보고서_2026.docx"


# ══════════════════════════════════════════════════════════════
# 7. 텍스트 위장 · 크기 불일치
# ══════════════════════════════════════════════════════════════
def test_txt에_바이너리_위장_차단():
    with pytest.raises(FileSecurityError) as e:
        scan_attachment(b"text\x00\x01\x02binary", ".txt")
    assert e.value.code == "TXT_BINARY"


def test_선언크기와_실제크기_불일치_차단():
    with pytest.raises(FileSecurityError) as e:
        scan_attachment(PNG_1x1, ".png", declared_size=999999)
    assert e.value.code == "SIZE_MISMATCH"


def test_허용되지_않은_확장자_차단():
    with pytest.raises(FileSecurityError) as e:
        scan_attachment(b"<svg onload=alert(1)>", ".svg")
    assert e.value.code == "EXT_NOT_ALLOWED"
