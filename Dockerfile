# syntax=docker/dockerfile:1-labs
# ↑ COPY --exclude 플래그(모델 가중치/코드 레이어 분리용)는 labs 채널에서만 지원된다
# (1.7 stable에는 아직 없음 — 로컬 빌드로 실측 확인).

# Stage 1: Builder
FROM python:3.11-slim AS builder

# uv 패키지 매니저 바이너리 복사
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 의존성 파일 복사 및 설치 (프로젝트 코드 제외하여 레이어 캐시 극대화)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# AI 모델 가중치(.pt, 총 ~256MB)는 소스코드보다 훨씬 드물게 바뀌므로 별도 레이어로
# 분리한다. 코드와 한 레이어로 묶으면 코드 한 줄만 바뀌어도 이 레이어가 무효화되어,
# 안 바뀐 250MB짜리 가중치를 매 배포마다 다시 복사하고 ECR에도 재업로드하게 된다.
COPY app/ai/*.pt ./app/ai/

# 나머지 전체 프로젝트 복사 후 설치 (가중치 레이어는 위에서 이미 캐시됨)
COPY --exclude=app/ai/*.pt . /app
RUN uv sync --frozen --no-dev

# Stage 2: Runtime
FROM python:3.11-slim AS runtime

# 보안 강화를 위한 Non-root 시스템 유저 생성
RUN groupadd -r wms-user && useradd -r -g wms-user wms-user

# ultralytics(YOLO)가 의존하는 opencv-python(cv2)은 python:3.11-slim 베이스에 없는
# X11/GL 공유 라이브러리를 필요로 한다. WBF 앙상블 3-YOLO 추론을 위해 설치.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libxcb1 \
    libxext6 \
    libxrender1 \
    libsm6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Builder 스테이지에서 생성된 패키지 환경과 소스 코드 복사 (권한 부여)
COPY --from=builder --chown=wms-user:wms-user /app/.venv /app/.venv
# 가중치(.pt)와 코드를 별도 레이어로 유지 — 코드만 바뀐 배포에서는 이 레이어가
# 그대로 재사용되어 로컬 캐시 히트는 물론, ECR push 시에도 다이제스트가 동일하면
# 레지스트리가 250MB 업로드 자체를 건너뛴다.
COPY --from=builder --chown=wms-user:wms-user /app/app/ai/*.pt /app/app/ai/
# exclude 패턴은 src(/app/app) 기준 상대경로 — 이 레이어의 소스 루트 안에서 ai/*.pt를 뺀다.
COPY --from=builder --chown=wms-user:wms-user --exclude=ai/*.pt /app/app /app/app
COPY --from=builder --chown=wms-user:wms-user /app/alembic /app/alembic
COPY --from=builder --chown=wms-user:wms-user /app/alembic.ini /app/alembic.ini

# 가상 환경을 PATH 최상단에 등록하여 venv 내 패키지 사용
ENV PATH="/app/.venv/bin:$PATH"

# Non-root 유저로 전환
USER wms-user

# 포트 개방
EXPOSE 8000

# 컨테이너 실행 시 기본 엔트리포인트 (Celery 구동 시 command override 권장)
# 워커 2개: 단일 프로세스는 무거운 직렬화 요청 하나가 파드 전체를 마비시킨다
# (동시 측정 실증 - completed 24s 처리 중 같은 파드의 /health가 20s 동반 정지)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
