from celery import Celery
from app.core.config import settings

# Celery 앱 인스턴스 생성
# Redis를 브로커(Broker)와 백엔드(Backend)로 모두 사용
celery_app = Celery(
    "wms_worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Seoul",
    enable_utc=False,
    # 🛡️ 대규모 트래픽 3대 방어 논리 2번: KEDA 스케일링을 위한 프리패치(Prefetch) 최적화
    # 워커가 무리해서 태스크를 당겨오지 않고 한 번에 하나씩만 처리하도록 하여 KEDA 스케일아웃 효율 극대화
    worker_prefetch_multiplier=1
)
