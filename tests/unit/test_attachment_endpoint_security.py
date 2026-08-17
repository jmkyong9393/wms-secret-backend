# -*- coding: utf-8 -*-
"""게시판 첨부 엔드포인트 인가·입력 검증 테스트.

`test_file_security.py`가 **파일 내용**을 다룬다면 이 파일은 **엔드포인트 자체**를 다룬다.
내용 검사가 아무리 촘촘해도 인가가 비어 있으면 남의 파일을 승격시키거나 열어 볼 수 있다.

S3는 스텁으로 대체한다 — 검증 대상은 우리 라우터의 판단이지 AWS의 동작이 아니다.
(S3가 정책을 실제로 강제하는지는 `s3_presigned_e2e_test.py`가 라이브 버킷으로 확인한다.)
"""
import uuid
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import get_current_user
from app.domains.uploads import router as uploads_router
from app.models.wms import User, UserRoleEnum, UserStatusEnum

USER_A_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER_B_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
       b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
       b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def _user(uid: uuid.UUID, employee_id: str) -> User:
    return User(
        id=uid, employee_id=employee_id, name="테스트", password_hash="x",
        role=UserRoleEnum.WORKER, status=UserStatusEnum.ACTIVE,
    )


class FakeS3:
    """라우터가 부르는 S3 호출만 흉내 낸다. 실제로 무엇을 복사·삭제했는지 기록한다."""

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}
        self.copied: List[Dict[str, str]] = []
        self.deleted: List[str] = []

    def generate_presigned_post(self, Bucket, Key, Fields, Conditions, ExpiresIn):
        self.last_conditions = Conditions
        return {"url": f"https://{Bucket}.s3.test/", "fields": {**Fields, "key": Key}}

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://{Params['Bucket']}.s3.test/{Params['Key']}?sig=stub&exp={ExpiresIn}"

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key]), "ContentType": "image/png"}

    def get_object(self, Bucket, Key):
        import io as _io
        return {"Body": _io.BytesIO(self.objects[Key])}

    def copy_object(self, Bucket, Key, CopySource, **kw):
        self.objects[Key] = self.objects[CopySource["Key"]]
        self.copied.append({"from": CopySource["Key"], "to": Key})

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
        self.deleted.append(Key)


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(uploads_router, "_attachment_s3_client", lambda: fake)
    return fake


class Session:
    """로그인 주체를 바꿔 가며 호출하는 클라이언트.

    사용자별로 픽스처를 나누면 둘 다 같은 `dependency_overrides` 키를 덮어써서
    **나중에 만들어진 쪽으로 두 요청이 모두 나간다**(실제로 이 함정에 빠져 IDOR 테스트가
    거짓 통과했다). 호출 직전에 주체를 바꾸는 방식으로 그 가능성을 없앤다.
    """

    def __init__(self, client: TestClient) -> None:
        self._c = client

    def _as(self, uid, emp):
        app.dependency_overrides[get_current_user] = lambda: _user(uid, emp)

    def a(self):
        self._as(USER_A_ID, "WM2608001")
        return self._c

    def b(self):
        self._as(USER_B_ID, "WM2608002")
        return self._c


@pytest.fixture
def sess():
    with TestClient(app) as c:
        yield Session(c)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def as_a(sess):
    return sess.a()


@pytest.fixture
def anon():
    """인증 의존성을 걷어내지 않은 상태 = 쿠키 없는 익명 호출."""
    app.dependency_overrides.pop(get_current_user, None)
    with TestClient(app) as c:
        yield c


# ────────────────────────────────────────────────────────────
# 1. 인증 — 로그인 없이는 어느 단계도 진입할 수 없어야 한다
# ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path,kwargs", [
    ("post", "/api/v1/uploads/attachment/presign", {"params": {"file_name": "a.png"}}),
    ("post", "/api/v1/uploads/attachment/verify", {"params": {"object_key": "quarantine/x/a.png"}}),
    ("post", "/api/v1/uploads/attachment/download-urls", {"json": {"object_keys": ["attachments/x/a.png"]}}),
])
def test_익명_호출은_전부_차단된다(anon, method, path, kwargs):
    r = getattr(anon, method)(path, **kwargs)
    assert r.status_code in (401, 403), f"{path} → {r.status_code}"


# ────────────────────────────────────────────────────────────
# 2. 소유권 — 남의 격리본을 승격시킬 수 없어야 한다
# ────────────────────────────────────────────────────────────

def test_presign_키에_소유자가_박힌다(as_a, s3):
    r = as_a.post("/api/v1/uploads/attachment/presign", params={"file_name": "photo.png", "file_type": "image/png"})
    assert r.status_code == 200
    key = r.json()["object_key"]
    assert key.startswith(f"quarantine/{USER_A_ID}/")
    # 서명된 정책도 자기 구역으로 제한되어야 한다 (남의 구역에 심어 두기 차단)
    assert ["starts-with", "$key", f"quarantine/{USER_A_ID}/"] in s3.last_conditions


def test_남의_격리본은_verify로_승격되지_않는다(sess, s3):
    r = sess.a().post("/api/v1/uploads/attachment/presign",
                      params={"file_name": "photo.png", "file_type": "image/png"})
    victim_key = r.json()["object_key"]
    assert victim_key.startswith(f"quarantine/{USER_A_ID}/"), "A의 키가 아니다 - 주체 전환 실패"
    s3.objects[victim_key] = PNG                      # A가 업로드를 마친 상태

    r2 = sess.b().post("/api/v1/uploads/attachment/verify", params={"object_key": victim_key})
    assert r2.status_code == 403
    assert s3.copied == [], "남의 파일이 정상 구역으로 옮겨졌다"
    assert victim_key in s3.objects, "남의 격리본이 삭제됐다"


def test_본인_격리본은_정상_승격된다(as_a, s3):
    r = as_a.post("/api/v1/uploads/attachment/presign", params={"file_name": "photo.png", "file_type": "image/png"})
    key = r.json()["object_key"]
    s3.objects[key] = PNG

    r2 = as_a.post("/api/v1/uploads/attachment/verify", params={"object_key": key})
    assert r2.status_code == 200, r2.text
    clean = r2.json()["object_key"]
    assert clean == key.replace("quarantine/", "attachments/", 1)
    assert s3.copied[0]["to"] == clean
    assert key in s3.deleted, "격리본이 남아 있다"


# ────────────────────────────────────────────────────────────
# 3. 키 조작 — 경로 이탈·구역 이탈
# ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_key", [
    "attachments/직행.png",                              # 격리를 건너뛴 정상 구역 직행
    "quarantine/../attachments/bypass.png",              # 상위 이동
    "/quarantine/절대경로.png",                           # 절대경로
    "quarantine//이중슬래시.png",                          # 빈 세그먼트
    "quarantine/\x00널바이트.png",                         # 제어문자
    "quarantine/" + "A" * 600 + ".png",                  # 과도한 길이
    "",                                                  # 빈 키
])
def test_조작된_키는_verify에서_거부된다(as_a, s3, bad_key):
    r = as_a.post("/api/v1/uploads/attachment/verify", params={"object_key": bad_key})
    assert r.status_code in (400, 403, 422), f"{bad_key!r} → {r.status_code}"
    assert s3.copied == []


# ────────────────────────────────────────────────────────────
# 4. presign 입력 검증 — 화이트리스트와 파일명 위장
# ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("file_name", [
    "payload.html",      # 저장형 XSS 벡터
    "payload.svg",       # SVG 내부 스크립트
    "payload.js",
    "payload.exe",
    "payload.sh",
    "payload.php",
    ".htaccess",         # 숨김 파일
    "noext",             # 확장자 없음
])
def test_허용목록_밖_확장자는_자격을_받지_못한다(as_a, s3, file_name):
    r = as_a.post("/api/v1/uploads/attachment/presign", params={"file_name": file_name})
    assert r.status_code == 400, f"{file_name} → {r.status_code}"


def test_경로조작_파일명은_경로성분이_제거된다(as_a, s3):
    r = as_a.post("/api/v1/uploads/attachment/presign",
                  params={"file_name": "../../../etc/passwd.png", "file_type": "image/png"})
    assert r.status_code == 200
    key = r.json()["object_key"]
    assert ".." not in key and "/etc/" not in key
    assert key.count("/") == 2, f"디렉터리 성분이 남았다: {key}"


def test_RTL_override_파일명은_거부되거나_정규화된다(as_a, s3):
    # "photo‮gnp.exe" — 화면에는 photo.exe가 photo.png처럼 뒤집혀 보인다
    r = as_a.post("/api/v1/uploads/attachment/presign",
                  params={"file_name": "photo‮gnp.exe", "file_type": "image/png"})
    assert r.status_code == 400, "BiDi 위장 파일명이 통과했다"


def test_이중확장자_위장은_거부된다(as_a, s3):
    r = as_a.post("/api/v1/uploads/attachment/presign",
                  params={"file_name": "invoice.pdf.exe", "file_type": "application/pdf"})
    assert r.status_code == 400


def test_허용목록_밖_ContentType은_거부된다(as_a, s3):
    r = as_a.post("/api/v1/uploads/attachment/presign",
                  params={"file_name": "photo.png", "file_type": "text/html"})
    assert r.status_code == 400


# ────────────────────────────────────────────────────────────
# 5. 열람 URL 발급
# ────────────────────────────────────────────────────────────

def test_격리구역_키로는_열람URL이_발급되지_않는다(as_a, s3):
    r = as_a.post("/api/v1/uploads/attachment/download-urls",
                  json={"object_keys": [f"quarantine/{USER_A_ID}/미검증.png"]})
    assert r.status_code == 200
    assert r.json()["urls"] == {}, "미검증 파일의 열람 경로가 열렸다"


def test_경로이탈_키는_조용히_제외된다(as_a, s3):
    keys = ["attachments/../quarantine/x.png", "/attachments/y.png", "attachments/정상.png"]
    r = as_a.post("/api/v1/uploads/attachment/download-urls", json={"object_keys": keys})
    urls = r.json()["urls"]
    assert list(urls) == ["attachments/정상.png"]


def test_발급_개수_상한이_강제된다(as_a, s3):
    keys = [f"attachments/{i}.png" for i in range(uploads_router.ATTACHMENT_DOWNLOAD_MAX_KEYS + 1)]
    r = as_a.post("/api/v1/uploads/attachment/download-urls", json={"object_keys": keys})
    assert r.status_code == 400


def test_빈_목록은_빈_결과를_돌려준다(as_a, s3):
    r = as_a.post("/api/v1/uploads/attachment/download-urls", json={"object_keys": []})
    assert r.status_code == 200 and r.json()["urls"] == {}


# ────────────────────────────────────────────────────────────
# 6. 검사 실패분의 처리 — 격리 구역에 남으면 안 된다
# ────────────────────────────────────────────────────────────

def test_악성파일은_거부되고_격리본이_삭제된다(as_a, s3):
    r = as_a.post("/api/v1/uploads/attachment/presign", params={"file_name": "innocent.png", "file_type": "image/png"})
    key = r.json()["object_key"]
    s3.objects[key] = b"MZ\x90\x00\x03" + b"\x00" * 200      # png로 위장한 실행파일

    r2 = as_a.post("/api/v1/uploads/attachment/verify", params={"object_key": key})
    assert r2.status_code == 400
    assert r2.headers.get("X-Scan-Code") == "EXECUTABLE_CONTENT"
    assert key in s3.deleted, "검사 실패분이 격리 구역에 남았다"
    assert s3.copied == [], "검사 실패분이 정상 구역으로 옮겨졌다"


def test_업로드되지_않은_키는_404다(as_a, s3):
    r = as_a.post("/api/v1/uploads/attachment/presign", params={"file_name": "a.png", "file_type": "image/png"})
    key = r.json()["object_key"]
    r2 = as_a.post("/api/v1/uploads/attachment/verify", params={"object_key": key})
    assert r2.status_code == 404


def test_크기초과_객체는_서버에서도_거부된다(as_a, s3):
    """S3가 1차로 막지만, 정책 없이 적재된 객체가 있어도 서버가 다시 막는지 확인한다."""
    r = as_a.post("/api/v1/uploads/attachment/presign", params={"file_name": "big.png", "file_type": "image/png"})
    key = r.json()["object_key"]
    s3.objects[key] = b"\x00" * (uploads_router.ATTACHMENT_MAX_BYTES + 1)

    r2 = as_a.post("/api/v1/uploads/attachment/verify", params={"object_key": key})
    assert r2.status_code == 400
    assert key in s3.deleted
