from fastapi import FastAPI
from app.api.routes import inventory, returns, orders
from app.core.database import init_db

app = FastAPI(
    title="WMS Core API",
    description="다중 에이전트 기반 B2B 도서 물류 자동화 플랫폼 Core Backend",
    version="1.5.0.0"
)

@app.on_event("startup")
def on_startup():
    init_db()

# 라우터 등록
app.include_router(inventory.router, prefix="/api/v1")
app.include_router(returns.router, prefix="/api/v1")
app.include_router(orders.router, prefix="/api/v1")

@app.get("/health")
def health_check():
    return {"status": "ok", "version": "1.5.0.0"}
