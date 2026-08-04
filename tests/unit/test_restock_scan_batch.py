"""저재고 스캔 배치 디스패치 로직 단위 테스트."""
from app.batch import restock_scan


class FakeTask:
    id = "fake-task-id"


def test_batch_enqueues_scan_task(monkeypatch):
    sent = []

    def fake_send_task(name):
        sent.append(name)
        return FakeTask()

    monkeypatch.setattr(restock_scan.celery_app, "send_task", fake_send_task)

    restock_scan.run_restock_scan_batch()

    assert sent == [restock_scan.SCAN_TASK_NAME]


def test_batch_falls_back_to_inprocess_on_broker_failure(monkeypatch):
    def broken_send_task(name):
        raise ConnectionError("redis down")

    inprocess_calls = []
    monkeypatch.setattr(restock_scan.celery_app, "send_task", broken_send_task)
    monkeypatch.setattr(
        restock_scan,
        "scan_safety_stock_proposals",
        lambda: inprocess_calls.append(True) or {"status": "success"},
    )

    # 브로커 장애 시에도 예외 전파 없이 인프로세스 스캔으로 폴백해야 한다
    restock_scan.run_restock_scan_batch()

    assert inprocess_calls == [True]
