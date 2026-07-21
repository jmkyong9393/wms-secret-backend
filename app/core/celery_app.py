import os
from celery import Celery

# celery 설정 담당
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "wms_worker",
    broker=redis_url,
    backend=redis_url
)

celery_app.conf.update(
    # 메시지 직렬화 형식
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # 시간대 설정 (Flower에서 시간 볼 때를 위함)
    timezone = "Asia/Seoul",
    enable_utc = False,

    # Flower/모니터링에서 task 상태를 더 잘 추적하기 위한 설정
    task_track_started = True,

    # Flower가 Celery task 이벤트를 받을 수 있게 해주는 설정
    worker_send_task_events = True,
    task_send_sent_event = True,

    # Worker가 한 번에 너무 많은 task를 미리 가져가지 않도록 제한
    worker_prefetch_multiplier = 1,

    # task 성공 후 ACK 처리, worker 장애 시 task 유실 방지
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # 장시간 실행 task 보호를 위한 실행 시간 제한
    task_soft_time_limit = 300, # 5분 넘으면 Celery가 부드럽게 중단 신호 줌
    task_time_limit = 360 # 6분이 넘으면 강제 종료
    

)

