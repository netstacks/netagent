"""Celery application configuration."""

import os
from celery import Celery

# Add shared package to path
import sys
sys.path.insert(0, '/app/shared')

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "netagent",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "tasks.agent_executor",
        "tasks.knowledge_indexer",
        "tasks.scheduled_runs",
        "tasks.notifications",
        "tasks.job_executor",
        "tasks.alert_triage",
    ],
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=28800 + 600,  # 8 hours + 10 min buffer (for approval holds)
    task_soft_time_limit=28800,  # 8 hours soft limit
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)

# Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    "check-scheduled-tasks": {
        "task": "tasks.scheduled_runs.check_scheduled_tasks",
        "schedule": 60.0,  # Every minute
    },
    "cleanup-expired-approvals": {
        "task": "tasks.scheduled_runs.cleanup_expired_approvals",
        "schedule": 300.0,  # Every 5 minutes
    },
    "sync-knowledge-bases": {
        "task": "tasks.knowledge_indexer.sync_pending_knowledge_bases",
        "schedule": 300.0,  # Check every 5 minutes, actual sync interval controlled by setting
    },
}

if __name__ == "__main__":
    celery_app.start()
