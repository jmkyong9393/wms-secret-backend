# -*- coding: utf-8 -*-
"""첨부 검사 엣지케이스 — 레드팀 탐침에서 실제로 뚫렸던 경로를 고정한다.

`test_file_security.py`가 기본 위협 9종을 다룬다면, 이 파일은 **그 검사를 우회하려는
변종**을 다룬다. 각 케이스는 탐침 결과 "통과"로 나왔던 것들이며, 방어를 넣은 뒤
차단으로 바뀐 것을 여기서 고정한다.

정상 파일이 함께 막히지 않는지도 같은 파일에서 확인한다 — 오탐 0을 유지하지 못하면
방어를 강화한 의미가 없다(실측에서 삼성 캡처 트레일러 46장이 이 함정에 걸렸다).
"""
import io
import zipfile

import pytest

from app.core.file_security import (
    FileSecurityError,
    normalize_filename,
    scan_attachment,
    verify_no_trailing_payload,
    verify_pdf_active_content,
    verify_zip_bomb,
)

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
       b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
       b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 200 + b"\xff\xd9"
# 실측에서 나온 삼성 캡처 트레일러 (스크린샷 46장에 붙어 있었다)
SAMSUNG_TRAILER = b"\x00\x00Q\x0c\x14\x00\x00\x00Samsung_Capture_InfoScreenshot" + b"\x00" * 40


def zip_of(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in entries:
            z.writestr(n, d)
    return buf.getvalue()


def code_of(fn, *args):
    with pytest.raises(FileSecurityError) as e:
        fn(*args)
    return e.value.code


# ────────────────────────────────────────────────────────────
# 1. PDF 내부 능동 콘텐츠 — PDF는 문서이자 실행 환경이다
# ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body,label", [
    (b"1 0 obj<</Type/Catalog/OpenAction<</S/JavaScript/JS(app.alert('x'))>>>>endobj", "JavaScript 자동실행"),
    (b"1 0 obj<</Type/Action/S/Launch/F(cmd.exe)>>endobj", "외부 프로그램 실행"),
    (b"1 0 obj<</Type/Filespec/EF<</F 2 0 R>>>>endobj", "내장 파일"),
    (b"1 0 obj<</Subtype/RichMedia>>endobj", "내장 미디어"),
])
def test_능동_요소가_있는_PDF는_차단된다(body, label):
    data = b"%PDF-1.4\n" + body + b"\n%%EOF"
    assert code_of(verify_pdf_active_content, data, ".pdf") == "PDF_ACTIVE_CONTENT", label


def test_평범한_PDF는_통과한다():
    data = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n%%EOF"
    scan_attachment(data, ".pdf")


def test_짧은_키워드로는_판정하지_않는다():
    """`/JS`·`/AA`는 3글자라 압축 스트림에 우연히 걸린다.

    실측: 정상 설계 문서 PDF가 `/AA` 한 번으로 차단됐다. `/JavaScript`가 항상 동반되므로
    짧은 키를 빼도 탐지력 손실이 없다.
    """
    data = b"%PDF-1.4\n" + b"\x9a\xAAstream\x00/AA\x00binary" + b"\n%%EOF"
    scan_attachment(data, ".pdf")


# ────────────────────────────────────────────────────────────
# 2. 압축 컨테이너 — 바깥 압축비만 보면 우회된다
# ────────────────────────────────────────────────────────────

def test_중첩_ZIP_폭탄은_한_겹_더_들어가_차단된다():
    inner = zip_of([("big.txt", b"A" * 20_000_000)])
    assert code_of(verify_zip_bomb, zip_of([("inner.zip", inner)]), ".xlsx").startswith("ZIP_BOMB")


@pytest.mark.parametrize("entry", ["../../evil.txt", "/etc/passwd", "..\\..\\evil.txt"])
def test_경로_이탈_엔트리는_차단된다(entry):
    assert code_of(verify_zip_bomb, zip_of([(entry, b"x")]), ".xlsx") == "ZIP_PATH_TRAVERSAL"


@pytest.mark.parametrize("entry", ["setup.exe", "run.bat", "payload.js", "tool.jar", "mal.hta"])
def test_압축_내부_실행파일은_차단된다(entry):
    assert code_of(verify_zip_bomb, zip_of([(entry, b"x")]), ".xlsx") == "ZIP_EXECUTABLE_ENTRY"


def test_정상_OOXML_구성물은_통과한다():
    """실제 xlsx는 xml·rels·이미지로 이루어진다 — 이게 막히면 기능이 죽는다."""
    verify_zip_bomb(zip_of([
        ("[Content_Types].xml", b"<Types/>"),
        ("_rels/.rels", b"<Relationships/>"),
        ("xl/workbook.xml", b"<workbook/>"),
        ("xl/media/image1.png", PNG),
    ]), ".xlsx")


# ────────────────────────────────────────────────────────────
# 3. 이미지 폴리글롯 — 형식마다 기준이 다르다
# ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload,label", [
    (b"<?php system($_GET['c']); ?>", "PHP"),
    (b"<html><script>alert(1)</script></html>", "HTML/스크립트"),
    (b"PK\x03\x04" + b"\x00" * 40, "ZIP(phar 계열)"),
    (b"MZ\x90\x00" + b"\x00" * 40, "PE 실행파일"),
])
def test_PNG_후미_페이로드는_차단된다(payload, label):
    assert code_of(verify_no_trailing_payload, PNG + payload, ".png") == "TRAILING_PAYLOAD", label


@pytest.mark.parametrize("payload,label", [
    (b"MZ\x90\x00" + b"\x00" * 40, "PE 실행파일"),
    (b"PK\x03\x04" + b"\x00" * 40, "ZIP"),
    (b"<?php echo 1; ?>", "PHP"),
])
def test_JPEG_후미의_위험_시그니처는_차단된다(payload, label):
    assert code_of(verify_no_trailing_payload, JPG + payload, ".jpg") == "TRAILING_PAYLOAD", label


def test_JPEG_제조사_트레일러는_통과한다():
    """삼성 캡처 메타데이터는 정상 구조다.

    존재만으로 막으면 휴대폰 스크린샷이 전부 거부된다 — 실측 405장 중 46장이 여기 걸렸다.
    """
    verify_no_trailing_payload(JPG + SAMSUNG_TRAILER, ".jpg")


def test_PNG는_후미_데이터_자체를_허용하지_않는다():
    """PNG의 IEND는 파일의 끝을 뜻한다. 실측 PNG 전량이 후미 0바이트였다."""
    verify_no_trailing_payload(PNG, ".png")                       # 정상
    assert code_of(verify_no_trailing_payload, PNG + b"A" * 40, ".png") == "TRAILING_PAYLOAD"


# ────────────────────────────────────────────────────────────
# 4. 파일명 — 보이지 않는 문자로 확장자를 바꾼다
# ────────────────────────────────────────────────────────────

def test_널바이트는_제거하지_않고_거부한다():
    """조용히 지우면 `photo.png\\x00.exe`가 `photo.png.exe`가 되어 검사 대상 확장자가 바뀐다."""
    assert code_of(normalize_filename, "photo.png\x00.exe") == "FILENAME_CONTROL"


@pytest.mark.parametrize("name", ["photo​.exe", "photo﻿.png", "photo⁠.png"])
def test_폭_없는_문자는_거부된다(name):
    assert code_of(normalize_filename, name) == "FILENAME_ZERO_WIDTH"


@pytest.mark.parametrize("name,expected", [
    ("photo.png.", "photo.png"),      # 윈도우는 후행 점을 무시한다
    ("photo.png ", "photo.png"),      # 후행 공백도 마찬가지
])
def test_후행_점과_공백은_정리된다(name, expected):
    assert normalize_filename(name) == expected


@pytest.mark.parametrize("name", ["...", "   ", ". . ."])
def test_이름이_남지_않는_파일명은_거부된다(name):
    assert code_of(normalize_filename, name) == "EMPTY_FILENAME"


@pytest.mark.parametrize("name", ["보고서_최종.pdf", "2026-08-18 회의록.docx", "photo (1).jpg"])
def test_평범한_파일명은_통과한다(name):
    assert normalize_filename(name)


# ────────────────────────────────────────────────────────────
# 5. 의도적으로 통과시키는 것 — 근거를 함께 고정한다
# ────────────────────────────────────────────────────────────

def test_txt_안의_HTML은_통과한다():
    """`.txt`는 텍스트다. 저장 시 `Content-Disposition: attachment`가 박히므로 브라우저가
    렌더링하지 않고 내려받는다. 내용으로 텍스트를 검열하지 않는다."""
    scan_attachment(b"<html><script>alert(1)</script></html>", ".txt")


def test_EICAR은_시그니처_검사로_잡히지_않는다():
    """우리는 안티바이러스가 아니다.

    이 통과는 결함이 아니라 **범위**다 — 알려진 악성코드 탐지는 GuardDuty Malware
    Protection의 몫이고, 이 계층은 형식·구조 위장을 막는다. 문서에 한계로 명시한다.
    """
    eicar = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    scan_attachment(eicar, ".txt")


def test_확장자_화이트리스트가_최종_방어선이다():
    """`invoice.pdf.exe`는 normalize를 통과하지만 확장자가 `.exe`라 상위 계층에서 막힌다.

    계층마다 책임이 다르다 — normalize는 *표시 위장*을, 화이트리스트는 *형식*을 본다.
    """
    assert normalize_filename("invoice.pdf.exe") == "invoice.pdf.exe"
    assert code_of(scan_attachment, b"MZ\x90\x00", ".exe") == "EXECUTABLE_CONTENT"
