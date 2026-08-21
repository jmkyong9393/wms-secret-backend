# 통합 스모크 — 전 기능 1회 주파 (로컬 전용)

"모든 기능을 한 번씩 실제로 돌려볼 수 있는" 단일 진입점.
쓰기 포함 전 도메인을 한 파일로 관통하고, 생성한 데이터는 마지막 테스트가 전부 회수한다.

## 실행 (빈 환경에서 3개 명령으로 끝 - 클린룸 검증 완료)

```bash
# ① 평가용 빈 PostgreSQL (저장소 compose와 무관, 시드·스키마 파일 불필요)
docker compose -f tests/integration/docker-compose.eval.yml up -d

# ② 의존성 (Python 3.11+ · uv 사용. requirements.txt는 없고 pyproject.toml + uv.lock이 정본)
pip install uv && uv sync

# ③ 실행
uv run pytest tests/integration -q
```

기대 결과: **19 passed, 4~5초.** 반복 실행 가능(매 실행 고유 `ITEST<epoch>` 태그로 생성→전량 삭제).

## 자급자족 부트스트랩 - 시드가 전혀 필요 없다

빈 DB면 테스트 세션이 스스로 준비한다 (전부 멱등 - 이미 있으면 건드리지 않음):

| 대상 | 방법 |
|---|---|
| 스키마 23테이블 | `SQLModel.metadata.create_all` (없는 테이블만 생성) |
| MASTER 계정 | 앱 스타트업의 빈 DB 자동 시드 + `init-master` 정식 경로 (WM2608001/1234) |
| WORKER 계정 | 직원 발급 서비스 정식 경로로 1명 생성 |
| 로케이션 | A-01-01 1행 삽입 |

`.env` 없이도 기본값(localhost:5432, admin/password)으로 동작한다. 별도 설정이 필요하면
아래 "필요한 .env 키" 참조. Redis·Celery 워커·라벨 프린터·인터넷 연결은 **필요 없다**.

## 필요한 .env 키

```dotenv
# 이 스위트가 실제로 읽는 키. 나머지 키는 없어도 기본값으로 동작한다.
DATABASE_URL=postgresql://admin:password@localhost:5432/wms_db   # 반드시 로컬 호스트
SECRET_KEY=<JWT 서명 키 - 서버와 동일해야 토큰 발급이 유효>
ALGORITHM=HS256
```

- 테스트는 비밀번호 로그인에 의존하지 않는다 — DB 실존 계정의 사번으로 JWT를 직접 서명한다
  (`SECRET_KEY`가 서버 설정과 같아야 하는 이유).
- `LABEL_PRINTER_*`, `OPENAI_API_KEY`, `AWS_*` 는 **불필요** (해당 경계를 끊고 검증).

## 로컬 전용 가드 (중요)

`conftest.py`의 `pytest_sessionstart`가 `DATABASE_URL` 호스트를 검사해
로컬(localhost / 127.0.0.1 / docker 서비스명)이 아니면 **즉시 종료**한다.
이 스위트는 쓰기를 포함하므로 운영·시연 DB를 향해 도는 것을 코드로 차단한 것이다.
배포 환경 검증은 이 테스트의 역할이 아니다 — 실물 등록 테스트와 읽기 전용 점검이 담당한다.

## 커버 범위 (실행 순서)

1. 인증 — `/auth/me` + 무인증 쓰기 차단(401/403)
2. 신품 Fast-Track 입고 — Zone A 편입, 수량 검증
3. 재고 원장 조회 — 입고분 반영 확인
4. 주문 생성 — 가격 산정 + AI 피킹지시서 자동 발행
5. 피킹 수락·상세 조회
6. 주문 조회 계열 (available-books · outbound-summary)
7. 반품 검수 접수 — 202 + **워커 큐 경계 도달 검증** (여기서부터 AI 파이프라인 소관)
8. 라벨 출력 — ZPL 생성·검증·이력 기록 (실기기 전송만 끔)
9. 게시판 CRUD — 글 작성·수정·댓글·삭제 전 과정
10. 조회 9종 — 알림 · 대시보드 4종 · FDS 2종 · 설정 · 입고이력
11. **cleanup** — 생성 데이터 전량 삭제 + 잔재 0건 검증 (테스트로 포함)

## 경계 처리 — 끊은 곳과 이유

| 경계 | 처리 | 이유 |
|---|---|---|
| 알라딘 ISBN 조회 | 스텁 | 가짜 ISBN 사용, 외부 네트워크 비의존 |
| AI 검수 (LangGraph/LLM) | `process_inspection.delay` 호출까지만 검증 | LLM 비용 0, 워커 비의존. 파이프라인 내부는 `tests/test_ubci_matrix_equivalence.py`(181건)와 단위 테스트가 담당 |
| 라벨 프린터 | `LABEL_PRINTER_ENABLED=false` (기존 개발 스위치) | LAN 실기기 비의존 |

## 기존 테스트와의 역할 분담

| 스위트 | 성격 | 쓰기 |
|---|---|---|
| `tests/unit/` | 단위 (파일보안 34종 · 인가 17종 등) | 없음 |
| `tests/api/v1/` | API 계약 (읽기 전용 GET + 4xx 유도) | 없음 |
| `tests/test_ubci_matrix_equivalence.py` | 결정론 동치 181건 | 없음 |
| **`tests/integration/`** | **전 기능 1회 주파 (본 스위트)** | **있음 (전량 회수)** |
