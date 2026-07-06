import os
from celery import Celery

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "wms_worker",
    broker=redis_url,
    backend=redis_url
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Seoul",
    enable_utc=False,
    task_track_started=True,
    task_time_limit=3600,
)

@celery_app.task(bind=True, name="app.core.celery_app.process_inspection")
def process_inspection(self, order_id: str, image_url: str):
    """
    LangGraph 기반 AI 비전 검수 에이전트를 구동하는 Celery 데몬
    """
    # TODO: LangGraph Agent 연동 로직
    print(f"[Celery] AI Inspection Task Started -> Order: {order_id}")
    return {"status": "SUCCESS", "order_id": order_id}
