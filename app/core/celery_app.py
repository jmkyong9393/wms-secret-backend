import os
from celery import Celery
from celery.schedules import crontab

# celery 설정 담당
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("wms_worker", broker=redis_url, backend=redis_url)

celery_app.conf.update(
    # 메시지 직렬화 형식
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # 시간대 설정 (Flower에서 시간 볼 때를 위함)
    timezone="Asia/Seoul",
    enable_utc=False,
    # Flower/모니터링에서 task 상태를 더 잘 추적하기 위한 설정
    task_track_started=True,
    # Flower가 Celery task 이벤트를 받을 수 있게 해주는 설정
    worker_send_task_events=True,
    task_send_sent_event=True,
    # Worker가 한 번에 너무 많은 task를 미리 가져가지 않도록 제한
    worker_prefetch_multiplier=1,
    # task 성공 후 ACK 처리, worker 장애 시 task 유실 방지
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Redis 브로커의 unacked 태스크 복원 대기 시간.
    # 검수 태스크 최대 실행 시간(task_time_limit 360초)보다 길게 잡되 분 단위 복구가 되도록 480초로 설정.
    # 주의: 실행 중 태스크가 이 시간을 넘기면 중복 전달될 수 있으나, Redlock 분산 락이 중복 처리를 차단한다.
    broker_transport_options={"visibility_timeout": 480},
    # 장시간 실행 task 보호를 위한 실행 시간 제한
    task_soft_time_limit=300,  # 5분 넘으면 Celery가 부드럽게 중단 신호 줌
    task_time_limit=360,  # 6분이 넘으면 강제 종료
    # 원장 기반 미아 작업 스위퍼 주기 실행.
    # worker_ready 시그널(기동 시 1회)에만 걸려 있었는데, 스위퍼의 대상 조건이 "2분 이상 방치된 PENDING"이라 워커 재기동 직후에는 방금 유실된 작업이 아직 2분이 안 돼 걸리지 않았다.
    # 결과적으로 두 조건이 서로를 무력화해, 브로커에서 소실된 작업이 다음 재기동 때까지 무기한 방치됐다. 주기 실행으로 바꿔 재기동 없이도 복구되게 한다.
    beat_schedule={
        "requeue-stale-pending-inspections": {
            "task": "app.worker.tasks.sweep_stale_pending_inspections",
            "schedule": 60.0,  # 초. 스위퍼 자체가 멱등(터미널 검사+Redlock)이라 짧아도 안전
        },
        # 주간 인사이트 정규 생성. timezone="Asia/Seoul" + enable_utc=False라 crontab도 KST다.
        # 00:00이 아니라 00:05인 이유: 자정 정각은 다른 일배치와 겹치기 쉽고, 주 경계 직후 몇 분은 직전 주 마지막 트랜잭션이 아직 커밋 중일 수 있어 여유를 둔다.
        # 태스크가 멱등(직전 주는 없을 때만 생성, 현재 주는 force 재집계)이라 중복 실행도 안전하다.
        "generate-weekly-insight": {
            "task": "app.worker.tasks.generate_weekly_insight",
            "schedule": crontab(hour=0, minute=5),
        },
    },
)
