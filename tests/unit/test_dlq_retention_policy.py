"""DLQ 보관정책(상한·TTL) 단위 테스트."""
import json

from app.core.config import settings
from app.worker import tasks


class FakePipeline:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def rpush(self, *args):
        self.calls.append(("rpush", args))
        return self

    def ltrim(self, *args):
        self.calls.append(("ltrim", args))
        return self

    def expire(self, *args):
        self.calls.append(("expire", args))
        return self

    def execute(self):
        self.calls.append(("execute", ()))


class FakeRedis:
    def __init__(self):
        self.pipeline_instance = FakePipeline()

    def pipeline(self, transaction):
        assert transaction is True
        return self.pipeline_instance


def test_push_to_dlq_applies_retention_policy(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(tasks, "redis_client", fake_redis)
    # 알림 발행은 외부 의존이므로 차단
    monkeypatch.setattr(tasks, "_notify_agent_error", lambda *a, **k: None)

    tasks.push_to_dlq(
        task_id="celery-task-1",
        return_job_id="job-1",
        error_msg="boom",
        retries=3,
    )

    calls = fake_redis.pipeline_instance.calls
    ops = [name for name, _ in calls]

    # rpush -> ltrim -> expire -> execute 순서로 원자 실행되어야 한다
    assert ops == ["rpush", "ltrim", "expire", "execute"]

    rpush_args = calls[0][1]
    assert rpush_args[0] == tasks.DLQ_KEY
    payload = json.loads(rpush_args[1])
    assert payload["task_id"] == "celery-task-1"
    assert payload["return_job_id"] == "job-1"
    assert payload["retries"] == 3

    ltrim_args = calls[1][1]
    assert ltrim_args == (
        tasks.DLQ_KEY,
        -settings.INSPECTION_DLQ_MAX_ENTRIES,
        -1,
    )

    expire_args = calls[2][1]
    assert expire_args == (
        tasks.DLQ_KEY,
        settings.INSPECTION_DLQ_TTL_SECONDS,
    )


def test_push_to_dlq_survives_redis_failure(monkeypatch):
    class BrokenRedis:
        def pipeline(self, transaction):
            raise ConnectionError("redis down")

    monkeypatch.setattr(tasks, "redis_client", BrokenRedis())
    monkeypatch.setattr(tasks, "_notify_agent_error", lambda *a, **k: None)

    # Redis 장애 시에도 예외를 전파하지 않고 로그만 남겨야 한다
    tasks.push_to_dlq(
        task_id="celery-task-2",
        return_job_id="job-2",
        error_msg="boom",
        retries=1,
    )
