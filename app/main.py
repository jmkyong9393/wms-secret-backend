"""
FastAPI 애플리케이션의 진입점(Entrypoint) 파일입니다.
앱 초기화, 데이터베이스 테이블 생성 트리거, 그리고 도메인별 API 라우터를 마운트하는 역할을 합니다.
"""
from fastapi import FastAPI
from app.api.v1.routes import inventory, returns, orders, dashboard, po, inbound
from app.core.database import create_db_and_tables
from app.core.config import settings

# FastAPI 앱 객체 생성 및 메타데이터(Swagger 문서 등) 설정
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="다중 에이전트 기반 B2B 도서 물류 자동화 플랫폼 Secret Backend",
    version="1.6.0.0"
)

@app.on_event("startup")
def on_startup():
    """
    서버 시작 시 실행되는 이벤트 핸들러입니다.
    현재는 앱 시작 시 SQLModel 기반 데이터베이스 테이블을 자동 생성(초기화)합니다.
    """
    create_db_and_tables()

# ==========================================
# 라우터 등록 (도메인별 API 분리)
# ==========================================
# settings.API_V1_STR (예: "/api/v1") Prefix를 달고 각 도메인의 라우터를 포함시킵니다.
app.include_router(dashboard.router, prefix=settings.API_V1_STR)
app.include_router(inventory.router, prefix=settings.API_V1_STR)
app.include_router(po.router, prefix=settings.API_V1_STR)
app.include_router(inbound.router, prefix=settings.API_V1_STR)
app.include_router(returns.router, prefix=settings.API_V1_STR)
app.include_router(orders.router, prefix=settings.API_V1_STR)

@app.get("/health")
def health_check():
    """
    로드밸런서(K8s Ingress 등) 또는 KEDA 스케일링을 위한 서버 헬스 체크 엔드포인트입니다.
    """
    return {"status": "ok", "version": "1.6.0.0"}
