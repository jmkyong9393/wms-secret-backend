"""
저재고 자동 발주 제안 스캔 배치 엔트리포인트.

k8s CronJob이 `python -m app.batch.restock_scan`으로 주기 실행한다.
배치 파드는 Celery 워커 풀에 스캔 태스크를 큐잉만 하고 즉시 종료하므로
LLM 비용이 큰 실제 스캔은 acks_late 보장이 있는 상시 워커에서 수행된다.

브로커(Redis) 장애 시에는 배치 파드 안에서 스캔을 직접 실행(fail-open)해
"그날의 스캔이 통째로 증발"하는 상황을 막는다.
"""
import logging

from app.core.celery_app import celery_app
from app.worker.tasks import scan_safety_stock_proposals

logger = logging.getLogger(__name__)

SCAN_TASK_NAME = "app.worker.tasks.scan_safety_stock_proposals"


def run_restock_scan_batch() -> None:
    logger.info("=== 저재고 Restock 제안 스캔 배치 시작 ===")

    try:
        task = celery_app.send_task(SCAN_TASK_NAME)
        logger.info(f"저재고 스캔 태스크 큐잉 완료. task_id={task.id}")
    except Exception as e:
        logger.warning(f"Celery 큐잉 실패, 배치 파드에서 직접 실행으로 폴백: {e}")
        result = scan_safety_stock_proposals()
        logger.info(f"인프로세스 스캔 완료: {result}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_restock_scan_batch()
