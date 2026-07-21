import sys
import time

def simulate_redlock_chaos():
    print("[2026-07-21 09:42:01,123: INFO/MainProcess] Celery worker started.")
    print("[2026-07-21 09:42:05,456: WARNING/ForkPoolWorker-1] Task skipped. Job 8f3a9b12-1d2c-4e8f-b9e4-c7a8b9c0d1e2 is already locked by another worker.")
    print("[2026-07-21 09:42:05,488: INFO/ForkPoolWorker-2] process_inspection started. task_id=abc12345-6789-def0 return_job_id=8f3a9b12-1d2c-4e8f-b9e4-c7a8b9c0d1e2")
    print("[2026-07-21 09:42:05,510: WARNING/ForkPoolWorker-3] Task skipped. Job 8f3a9b12-1d2c-4e8f-b9e4-c7a8b9c0d1e2 is already locked by another worker.")
    print("[2026-07-21 09:42:15,110: INFO/ForkPoolWorker-2] process_inspection completed gracefully. task_id=abc12345-6789-def0 return_job_id=8f3a9b12-1d2c-4e8f-b9e4-c7a8b9c0d1e2 status=APPROVED")

def simulate_dlq_chaos():
    print("[2026-07-21 09:45:10,001: INFO/ForkPoolWorker-1] process_inspection started. task_id=def98765-4321-abc0 return_job_id=9a4b8c23-5d6e-7f8a-b9c0-d1e2f3a4b5c6")
    print("[2026-07-21 09:45:11,050: WARNING/ForkPoolWorker-1] [Rate Limit / HTTP Error] Retrying task def98765-4321-abc0 in 2s... (1/3) | Err: 429 Too Many Requests")
    print("[2026-07-21 09:45:13,055: WARNING/ForkPoolWorker-1] [Rate Limit / HTTP Error] Retrying task def98765-4321-abc0 in 4s... (2/3) | Err: 429 Too Many Requests")
    print("[2026-07-21 09:45:17,060: WARNING/ForkPoolWorker-1] [Rate Limit / HTTP Error] Retrying task def98765-4321-abc0 in 8s... (3/3) | Err: 429 Too Many Requests")
    print("[2026-07-21 09:45:25,065: ERROR/ForkPoolWorker-1] HTTP retries exhausted for 9a4b8c23-5d6e-7f8a-b9c0-d1e2f3a4b5c6. Sending to DLQ.")
    print("[2026-07-21 09:45:25,070: ERROR/ForkPoolWorker-1] [DLQ] Task def98765-4321-abc0 for job 9a4b8c23-5d6e-7f8a-b9c0-d1e2f3a4b5c6 safely pushed to DLQ.")

if __name__ == "__main__":
    simulate_redlock_chaos()
    print("\n")
    simulate_dlq_chaos()
