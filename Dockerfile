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

# 전체 프로젝트 복사 후 설치
COPY . /app
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
COPY --from=builder --chown=wms-user:wms-user /app/app /app/app
COPY --from=builder --chown=wms-user:wms-user /app/alembic /app/alembic
COPY --from=builder --chown=wms-user:wms-user /app/alembic.ini /app/alembic.ini

# 가상 환경을 PATH 최상단에 등록하여 venv 내 패키지 사용
ENV PATH="/app/.venv/bin:$PATH"

# Non-root 유저로 전환
USER wms-user

# 포트 개방
EXPOSE 8000

# 컨테이너 실행 시 기본 엔트리포인트 (Celery 구동 시 command override 권장)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
