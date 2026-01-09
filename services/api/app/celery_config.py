"""Celery configuration for API service.

This provides a minimal Celery app for dispatching tasks to the worker.
The API doesn't process tasks - it just sends them.
"""

import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "netagent",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
)


def send_task(task_name: str, args: list = None, kwargs: dict = None):
    """Send a task to the Celery worker.

    Args:
        task_name: Full task name (e.g., 'tasks.job_executor.execute_job')
        args: Positional arguments for the task
        kwargs: Keyword arguments for the task

    Returns:
        AsyncResult from Celery
    """
    return celery_app.send_task(task_name, args=args, kwargs=kwargs)
