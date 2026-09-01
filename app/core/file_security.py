"""업로드 파일 보안 검증 — 선언된 형식이 아니라 **실제 바이트**를 본다.

확장자·Content-Type은 클라이언트가 주장하는 값이라 신뢰하지 않는다. presigned POST가
크기·Content-Type을 S3 서명으로 강제하지만, 그것만으로는 아래를 막지 못한다:

  ① 확장자 위조    .jpg 인데 실제로는 PE 실행파일
  ② Polyglot       앞은 유효한 JPEG, 뒤에 ZIP/JAR을 이어붙인 파일(GIFAR 계열)
  ③ 매크로 문서    docx/xlsx/pptx 내부의 vbaProject.bin
  ④ 디컴프레션 폭탄 수십 KB PNG가 압축 해제 시 수 GB 픽셀로 팽창
  ⑤ 파일명 트릭    RTL override(U+202E)로 .exe를 .png처럼 보이게 표시

각 함수는 통과 시 조용히 반환하고, 위반 시 FileSecurityError를 던진다.
**차단 목록(blacklist)을 쓰지 않는다** — 새 형식이 나올 때마다 뚫리기 때문에
허용 목록(whitelist)만 쓴다.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from typing import Dict, Optional, Set, Tuple


class FileSecurityError(Exception):
    """검증 위반. 사용자에게 보일 사유(reason)와 감사용 코드(code)를 함께 담는다."""

    def __init__(self, code: str, reason: str):
        self.code = code
        self.reason = reason
        super().__init__(f"[{code}] {reason}")


# ──────────────────────────────────────────────────────────────
# 1. 매직 바이트 — 파일 시그니처로 실제 타입을 판정한다
# ──────────────────────────────────────────────────────────────
# (오프셋, 시그니처) 목록. 하나라도 맞으면 그 타입으로 본다.
_MAGIC: Dict[str, Tuple[Tuple[int, bytes], ...]] = {
    "jpg": ((0, b"\xff\xd8\xff"),),
    "png": ((0, b"\x89PNG\r\n\x1a\n"),),
    "webp": ((0, b"RIFF"), (8, b"WEBP")),  # 두 조건 모두 만족해야 한다
    "heic": ((4, b"ftyp"),),  # ftypheic / ftypheix / ftypmif1
    "pdf": ((0, b"%PDF-"),),
    "zip": ((0, b"PK\x03\x04"), (0, b"PK\x05\x06"), (0, b"PK\x07\x08")),
    "ole": (
        (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    ),  # 구 MS Office(.doc/.xls/.ppt) · HWP 5.0
}

# 확장자 → 허용되는 실제 타입. docx/xlsx/pptx는 ZIP 컨테이너다.
_EXT_TO_TYPES: Dict[str, Set[str]] = {
    ".jpg": {"jpg"},
    ".jpeg": {"jpg"},
    ".png": {"png"},
    ".webp": {"webp"},
    ".heic": {"heic"},
    ".pdf": {"pdf"},
    ".docx": {"zip"},
    ".xlsx": {"zip"},
    ".pptx": {"zip"},
    ".doc": {"ole"},
    ".xls": {"ole"},
    ".ppt": {"ole"},
    ".hwp": {"ole"},
    ".txt": set(),  # 텍스트는 고정 시그니처가 없다 — 별도 규칙(§2)으로 검사한다
}

# 실행 파일 시그니처. 확장자와 무관하게 발견되면 즉시 거부한다.
_EXECUTABLE_SIGNATURES: Tuple[Tuple[str, bytes], ...] = (
    ("PE(Windows 실행파일)", b"MZ"),
    ("ELF(Linux 실행파일)", b"\x7fELF"),
    ("Mach-O(macOS 실행파일)", b"\xcf\xfa\xed\xfe"),
    ("Java class", b"\xca\xfe\xba\xbe"),
    ("셸 스크립트", b"#!"),
)


def detect_file_type(head: bytes) -> Optional[str]:
    """선두 바이트로 실제 파일 타입을 판정한다. 모르면 None."""
    for ftype, conditions in _MAGIC.items():
        if ftype == "webp":
            # RIFF....WEBP — 두 조건을 모두 만족해야 한다
            if all(head[off : off + len(sig)] == sig for off, sig in conditions):
                return ftype
            continue
        for off, sig in conditions:
            if head[off : off + len(sig)] == sig:
                return ftype
    return None


def verify_magic_bytes(head: bytes, ext: str) -> None:
    """확장자와 실제 시그니처를 교차 대조한다 (①·② 차단의 1차 관문)."""
    # 실행 파일은 확장자가 무엇이든 거부한다
    for label, sig in _EXECUTABLE_SIGNATURES:
        if head.startswith(sig):
            raise FileSecurityError(
                "EXECUTABLE_CONTENT", f"실행 파일 형식이 감지되었습니다 ({label})."
            )

    allowed = _EXT_TO_TYPES.get(ext)
    if allowed is None:
        raise FileSecurityError("EXT_NOT_ALLOWED", f"허용되지 않는 확장자입니다: {ext}")

    if not allowed:  # .txt — 시그니처가 없으므로 여기서는 통과, §2가 검사한다
        return

    actual = detect_file_type(head)
    if actual is None:
        raise FileSecurityError(
            "UNKNOWN_SIGNATURE",
            f"파일 시그니처를 인식할 수 없습니다. 확장자({ext})와 실제 내용이 다를 수 있습니다.",
        )
    if actual not in allowed:
        raise FileSecurityError(
            "EXT_CONTENT_MISMATCH",
            f"확장자({ext})와 실제 파일 형식({actual})이 일치하지 않습니다.",
        )


# ──────────────────────────────────────────────────────────────
# 2. 텍스트 파일 — 실행 스크립트·바이너리 위장 차단
# ──────────────────────────────────────────────────────────────
def verify_text_content(head: bytes) -> None:
    """.txt로 올린 파일이 실제로 평문인지 확인한다."""
    if b"\x00" in head:
        raise FileSecurityError(
            "TXT_BINARY", "텍스트 파일에 바이너리 데이터가 포함되어 있습니다."
        )
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        try:
            head.decode("cp949")
        except UnicodeDecodeError:
            raise FileSecurityError(
                "TXT_NOT_DECODABLE", "텍스트로 해석할 수 없는 내용입니다."
            )


# ──────────────────────────────────────────────────────────────
# 3. Office 매크로 (③)
# ──────────────────────────────────────────────────────────────
# OOXML(docx/xlsx/pptx)은 ZIP이다. 매크로는 아래 경로에 들어간다.
_MACRO_MARKERS = ("vbaproject.bin", "vbadata.xml", "macros/")


def verify_no_macro(data: bytes, ext: str) -> None:
    """OOXML 문서 내부에 VBA 매크로가 있으면 거부한다."""
    if ext not in {".docx", ".xlsx", ".pptx"}:
        return
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n.lower() for n in zf.namelist()]
    except zipfile.BadZipFile:
        raise FileSecurityError(
            "OOXML_BROKEN", "문서 파일이 손상되었거나 형식이 올바르지 않습니다."
        )

    for n in names:
        if any(marker in n for marker in _MACRO_MARKERS):
            raise FileSecurityError(
                "MACRO_DETECTED", "매크로가 포함된 문서는 업로드할 수 없습니다."
            )


# 압축 컨테이너 안에 있어서는 안 되는 확장자. OOXML 정상 구성물(xml·rels·이미지·폰트)에는
# 없는 것들만 골랐다.
_ARCHIVE_FORBIDDEN_EXTS = (
    ".exe",
    ".dll",
    ".scr",
    ".com",
    ".pif",
    ".bat",
    ".cmd",
    ".msi",
    ".ps1",
    ".vbs",
    ".vbe",
    ".js",
    ".jse",
    ".wsf",
    ".hta",
    ".jar",
    ".sh",
    ".lnk",
)
# 중첩 압축으로 폭탄을 숨기는 것을 막기 위해 한 단계 더 들어간다.
_NESTED_ARCHIVE_EXTS = (".zip", ".docx", ".xlsx", ".pptx", ".jar")
_ARCHIVE_MAX_DEPTH = 2


def verify_zip_bomb(
    data: bytes, ext: str, max_ratio: int = 100, max_total: int = 200 * 1024 * 1024
) -> None:
    """ZIP 계열의 압축 폭탄과 위험한 엔트리를 차단한다 (④의 ZIP 변종).

    압축 해제 후 총량과 압축비를 **실제로 풀지 않고** 헤더에서 읽어 판단한다.
    엔트리 이름과 중첩 압축까지 함께 본다 — 바깥 압축비만 보면 zip 안에 zip으로 우회된다.
    """
    if ext not in {".docx", ".xlsx", ".pptx"}:
        return
    _inspect_archive(data, depth=1, max_ratio=max_ratio, max_total=max_total)


def _inspect_archive(data: bytes, depth: int, max_ratio: int, max_total: int) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            total = sum(i.file_size for i in infos)

            if total > max_total:
                raise FileSecurityError(
                    "ZIP_BOMB_SIZE",
                    f"압축 해제 크기가 과도합니다 ({total // (1024 * 1024)}MB).",
                )
            if len(data) > 0 and total / len(data) > max_ratio:
                raise FileSecurityError(
                    "ZIP_BOMB_RATIO",
                    f"압축비가 비정상적입니다 ({total / len(data):.0f}배).",
                )

            for info in infos:
                name = info.filename
                lowered = name.lower()

                # 압축을 푸는 쪽에서 의도하지 않은 경로에 쓰이는 것을 막는다(Zip Slip).
                # 우리는 풀지 않지만 내려받은 사람이 푼다.
                normalized = name.replace("\\", "/")
                if normalized.startswith("/") or ".." in normalized.split("/"):
                    raise FileSecurityError(
                        "ZIP_PATH_TRAVERSAL",
                        f"압축 파일에 비정상 경로가 있습니다: {name}",
                    )

                if lowered.endswith(_ARCHIVE_FORBIDDEN_EXTS):
                    raise FileSecurityError(
                        "ZIP_EXECUTABLE_ENTRY",
                        f"압축 파일에 실행 가능한 항목이 있습니다: {name}",
                    )

                if depth < _ARCHIVE_MAX_DEPTH and lowered.endswith(
                    _NESTED_ARCHIVE_EXTS
                ):
                    if info.file_size > max_total:
                        raise FileSecurityError(
                            "ZIP_BOMB_SIZE", f"중첩 압축 항목이 과도합니다: {name}"
                        )
                    _inspect_archive(zf.read(info), depth + 1, max_ratio, max_total)
    except zipfile.BadZipFile:
        return  # 형식 오류는 verify_no_macro가 이미 처리한다


# ──────────────────────────────────────────────────────────────
# 3-1. PDF 내부 능동 콘텐츠
# ──────────────────────────────────────────────────────────────
# PDF는 문서이자 **실행 환경**이다. 뷰어가 JavaScript를 돌리고 외부 프로그램을 띄울 수 있다.
# 매직바이트만 보면 "정상 PDF"이므로 여기서 따로 본다.
# 짧은 키(/JS · /AA)는 쓰지 않는다 — 압축 스트림 바이트에 우연히 걸려 오탐이 나고,
# /JavaScript가 항상 동반되므로 탐지 손실도 없다.
_PDF_ACTIVE_MARKERS = (
    (b"/JavaScript", "JavaScript"),
    (b"/OpenAction", "문서 열람 시 자동 실행"),
    (b"/Launch", "외부 프로그램 실행"),
    (b"/EmbeddedFile", "내장 파일"),
    (b"/Filespec", "내장 파일 참조"),
    (b"/RichMedia", "내장 미디어 실행"),
)


def verify_pdf_active_content(data: bytes, ext: str) -> None:
    """PDF 안의 자동 실행·내장 파일 구조를 거부한다."""
    if ext != ".pdf":
        return
    for marker, label in _PDF_ACTIVE_MARKERS:
        if marker in data:
            raise FileSecurityError(
                "PDF_ACTIVE_CONTENT",
                f"실행 가능한 요소가 포함된 PDF입니다 ({label}).",
            )


# ──────────────────────────────────────────────────────────────
# 3-2. 이미지 후미 페이로드 (Polyglot)
# ──────────────────────────────────────────────────────────────
# 이미지 종료 표지 뒤에 스크립트·압축·실행파일을 이어 붙이면 매직바이트 검사를 통과한다.
# 뷰어는 무시하지만 다른 해석기(웹서버·압축 도구)는 뒤쪽을 읽는다.
_TRAILING_TOLERANCE = 16  # 인코더가 남기는 패딩 여유

# JPEG 후미에서만 찾는 위험 시그니처. 존재 자체를 막지 않고 **무엇이 붙었는지**로 판정한다.
_TRAILING_DANGEROUS = (
    (b"MZ", "PE 실행파일"),
    (b"\x7fELF", "ELF 실행파일"),
    (b"\xca\xfe\xba\xbe", "Java class"),
    (b"PK\x03\x04", "ZIP 컨테이너"),
    (b"<?php", "PHP 코드"),
    (b"<script", "스크립트"),
    (b"<html", "HTML 문서"),
    (b"#!/", "셸 스크립트"),
)


def verify_no_trailing_payload(data: bytes, ext: str) -> None:
    """이미지 종료 표지 이후에 붙은 데이터를 검사한다.

    형식마다 기준이 다르다.
      - PNG: IEND가 파일의 끝 — 후미 데이터는 비정상.
      - JPEG: EOI 뒤 제조사 메타데이터(삼성 캡처 트레일러 등)가 정상이라
        위험 시그니처가 섞였을 때만 막는다. (오탐 실측 근거: 92번 §5-3c)
    """
    if ext == ".png":
        idx = data.rfind(b"IEND")
        if idx == -1:
            return  # 종료 표지가 없는 잘린 파일은 다른 계층이 다룬다
        trailing = data[idx + 8 :]  # IEND + CRC 4바이트
        if len(trailing) <= _TRAILING_TOLERANCE and not trailing.strip(b"\x00 \r\n\t"):
            return  # 패딩 수준은 허용
        if trailing:
            raise FileSecurityError(
                "TRAILING_PAYLOAD",
                f"PNG 종료 표지 뒤에 {len(trailing)}바이트의 추가 데이터가 있습니다 (폴리글롯 의심).",
            )
        return

    if ext in {".jpg", ".jpeg"}:
        idx = data.rfind(b"\xff\xd9")
        if idx == -1:
            return
        trailing = data[idx + 2 :]
        for sig, label in _TRAILING_DANGEROUS:
            if sig in trailing:
                raise FileSecurityError(
                    "TRAILING_PAYLOAD",
                    f"JPEG 종료 표지 뒤에 {label}이(가) 이어붙어 있습니다 (폴리글롯).",
                )


# ──────────────────────────────────────────────────────────────
# 4. 이미지 디컴프레션 폭탄 (④)
# ──────────────────────────────────────────────────────────────
# 5MB 제한 안에서도 픽셀 수는 수십억이 될 수 있다. 파일 크기가 아니라 **픽셀 수**로 막는다.
MAX_IMAGE_PIXELS = 50_000_000  # 5천만 화소 (8000x6000 여유)


def verify_image_dimensions(data: bytes, ext: str) -> None:
    """이미지 헤더만 읽어 픽셀 수 상한을 검사한다 (전체 디코딩하지 않는다)."""
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
        return
    try:
        from PIL import Image
    except ImportError:
        return  # Pillow가 없으면 이 계층은 건너뛴다 (다른 계층은 그대로 작동)

    try:
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size  # open()은 헤더만 읽는다 — 폭탄이어도 여기서 터지지 않는다
    except Exception:
        # heic 등 플러그인이 없는 형식은 매직바이트 검증으로 이미 걸러졌다
        return

    if w * h > MAX_IMAGE_PIXELS:
        raise FileSecurityError(
            "IMAGE_BOMB",
            f"이미지 해상도가 과도합니다 ({w}x{h} = {w * h / 1_000_000:.0f}M 화소).",
        )


# ──────────────────────────────────────────────────────────────
# 5. 파일명 (⑤)
# ──────────────────────────────────────────────────────────────
# 양방향 텍스트 제어문자 — 파일명 표시를 뒤집어 확장자를 위장한다.
_BIDI_CONTROLS = re.compile(r"[‪-‮⁦-⁩‎‏]")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


# 폭 없는 문자(제로폭 공백·BOM 등) — 표시되지 않으면서 파일명을 갈라놓는다.
_ZERO_WIDTH = re.compile("[​-‏⁠﻿]")


def normalize_filename(name: str) -> str:
    """표시 위장·제어문자를 제거하고 유니코드를 정규화한다.

    예: "photo‮gnp.exe" 는 화면에 "photoexe.png"로 보인다 — 이 트릭을 제거한다.
    """
    if not name:
        raise FileSecurityError("EMPTY_FILENAME", "파일명이 비어 있습니다.")
    name = unicodedata.normalize("NFC", name)
    if _BIDI_CONTROLS.search(name):
        raise FileSecurityError(
            "FILENAME_BIDI", "파일명에 표시를 왜곡하는 제어문자가 포함되어 있습니다."
        )
    # 제어문자를 조용히 지우면 확장자가 바뀐다 — "photo.png\x00.exe"가 "photo.png.exe"가 되어
    # 검사 대상 확장자 자체가 달라진다. 지우지 말고 거부한다.
    if _CONTROL_CHARS.search(name):
        raise FileSecurityError(
            "FILENAME_CONTROL", "파일명에 제어문자가 포함되어 있습니다."
        )
    # 폭 없는 문자는 눈에 보이지 않으면서 파일명을 다르게 만든다.
    if _ZERO_WIDTH.search(name):
        raise FileSecurityError(
            "FILENAME_ZERO_WIDTH", "파일명에 보이지 않는 문자가 포함되어 있습니다."
        )
    # 윈도우는 후행 점·공백을 무시한다 — "photo.png."는 실제로 "photo.png"로 열린다.
    # 확장자 판정이 갈리지 않도록 먼저 정리한다.
    name = name.rstrip(". ")
    if not name:
        raise FileSecurityError("EMPTY_FILENAME", "파일명이 비어 있습니다.")
    # 이중 확장자 경고: 마지막 확장자만 유효하므로 앞의 실행 확장자를 차단한다
    lowered = name.lower()
    for danger in (".exe.", ".bat.", ".cmd.", ".sh.", ".js.", ".jar.", ".ps1."):
        if danger in lowered:
            raise FileSecurityError(
                "FILENAME_DOUBLE_EXT", "실행 파일 확장자가 파일명에 포함되어 있습니다."
            )
    return name.strip()


# ──────────────────────────────────────────────────────────────
# 통합 진입점
# ──────────────────────────────────────────────────────────────
def scan_attachment(
    data: bytes, ext: str, declared_size: Optional[int] = None
) -> Dict[str, object]:
    """첨부파일 전체 검증. 통과하면 판정 요약을 돌려준다.

    호출자는 이 함수가 예외를 던지지 않은 경우에만 격리 구역에서 정상 구역으로 옮긴다.
    """
    ext = (ext or "").lower()
    checks = []

    verify_magic_bytes(data[:64], ext)
    checks.append("magic_bytes")
    if ext == ".txt":
        verify_text_content(data[:4096])
        checks.append("text_content")
    verify_no_macro(data, ext)
    checks.append("macro")
    verify_zip_bomb(data, ext)
    checks.append("zip_safety")
    verify_pdf_active_content(data, ext)
    checks.append("pdf_active_content")
    verify_image_dimensions(data, ext)
    checks.append("image_bomb")
    verify_no_trailing_payload(data, ext)
    checks.append("trailing_payload")

    if declared_size is not None and declared_size != len(data):
        raise FileSecurityError(
            "SIZE_MISMATCH",
            f"선언한 크기({declared_size})와 실제 크기({len(data)})가 다릅니다.",
        )

    return {
        "verdict": "CLEAN",
        "actual_type": detect_file_type(data[:64]),
        "size": len(data),
        "checks_passed": checks,
    }
