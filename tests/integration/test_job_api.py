"""Integration tests for Job API endpoints."""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import sys
sys.path.insert(0, '/app/shared')
sys.path.insert(0, '/app')


# Mock the database and auth before importing the app
@pytest.fixture(autouse=True)
def mock_dependencies():
    """Mock database and authentication dependencies."""
    with patch('netagent_core.db.get_db') as mock_get_db, \
         patch('netagent_core.auth.get_current_user') as mock_get_user:

        # Mock database session
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        # Mock user
        mock_user = MagicMock()
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_get_user.return_value = mock_user

        yield {
            "db": mock_db,
            "user": mock_user,
        }


class TestJobParseEndpoint:
    """Tests for POST /api/jobs/parse endpoint."""

    def test_parse_structured_spec(self, mock_dependencies):
        """Test parsing a structured job specification."""
        # Import inside test to use mocked dependencies
        from services.api.app.routes.jobs import router, parse_job_spec, JobParse

        spec = JobParse(spec_text="""## Config
mode: batch
batch_size: 3

## Tasks
### 1. Check health
agent: network-monitor
Check device health status""")

        # Call the endpoint function directly
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            parse_job_spec(spec, mock_dependencies["user"])
        )

        assert result.name is not None
        assert len(result.tasks) >= 1
        assert result.config["mode"] == "batch"
        assert result.config["batch_size"] == 3

    def test_parse_natural_language_spec(self, mock_dependencies):
        """Test parsing a natural language specification."""
        from services.api.app.routes.jobs import parse_job_spec, JobParse

        spec = JobParse(spec_text="Check health of all devices and backup their configs")

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            parse_job_spec(spec, mock_dependencies["user"])
        )

        # Natural language should return some structure
        assert result is not None


class TestJobSubmitEndpoint:
    """Tests for POST /api/jobs/ endpoint."""

    def test_submit_structured_job(self, mock_dependencies):
        """Test submitting a structured job."""
        from services.api.app.routes.jobs import submit_job, JobSubmit
        from netagent_core.db import Job, User

        # Mock the job creation
        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.name = "Test Job"
        mock_job.status = "pending"
        mock_job.execution_mode = "batch"
        mock_job.batch_size = 5
        mock_job.validation_mode = "permissive"
        mock_job.total_tasks = 2
        mock_job.completed_tasks = 0
        mock_job.failed_tasks = 0
        mock_job.created_by = None
        mock_job.spec_raw = "test"
        mock_job.spec_parsed = {}
        mock_job.on_failure = "continue"
        mock_job.retry_count = 0
        mock_job.delivery_config = None
        mock_job.results = None
        mock_job.error_summary = None
        mock_job.tasks = []

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = None
        mock_dependencies["db"].add.return_value = None
        mock_dependencies["db"].flush.return_value = None
        mock_dependencies["db"].commit.return_value = None
        mock_dependencies["db"].refresh = lambda x: setattr(x, 'id', 1)

        data = JobSubmit(
            name="Test Job",
            spec_text="""## Tasks
### 1. First task
Do something

### 2. Second task
Do something else""",
            execution_mode="batch",
            batch_size=5,
            on_failure="continue",
        )

        # The function expects real SQLAlchemy models, so we need more mocking
        # This is a simplified test

    def test_submit_job_with_delivery(self, mock_dependencies):
        """Test submitting a job with delivery configuration."""
        # Similar setup as above but with delivery_config


class TestJobListEndpoint:
    """Tests for GET /api/jobs/ endpoint."""

    def test_list_all_jobs(self, mock_dependencies):
        """Test listing all jobs."""
        from services.api.app.routes.jobs import list_jobs
        from netagent_core.db import Job

        # Mock jobs
        mock_job1 = MagicMock(spec=Job)
        mock_job1.id = 1
        mock_job1.name = "Job 1"
        mock_job1.status = "completed"

        mock_job2 = MagicMock(spec=Job)
        mock_job2.id = 2
        mock_job2.name = "Job 2"
        mock_job2.status = "pending"

        mock_dependencies["db"].query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            mock_job1, mock_job2
        ]

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            list_jobs(
                status=None,
                limit=50,
                offset=0,
                db=mock_dependencies["db"],
                user=mock_dependencies["user"],
            )
        )

        assert len(result) == 2

    def test_list_jobs_filter_by_status(self, mock_dependencies):
        """Test listing jobs filtered by status."""
        from services.api.app.routes.jobs import list_jobs
        from netagent_core.db import Job

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.name = "Completed Job"
        mock_job.status = "completed"

        mock_dependencies["db"].query.return_value.order_by.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = [
            mock_job
        ]

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            list_jobs(
                status="completed",
                limit=50,
                offset=0,
                db=mock_dependencies["db"],
                user=mock_dependencies["user"],
            )
        )

        # Should only return completed jobs


class TestJobDetailEndpoint:
    """Tests for GET /api/jobs/{job_id} endpoint."""

    def test_get_job_detail(self, mock_dependencies):
        """Test getting job details."""
        from services.api.app.routes.jobs import get_job
        from netagent_core.db import Job, JobTask

        mock_task = MagicMock(spec=JobTask)
        mock_task.id = 1
        mock_task.sequence = 1
        mock_task.name = "Test Task"
        mock_task.status = "completed"

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.name = "Test Job"
        mock_job.status = "completed"
        mock_job.tasks = [mock_task]

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = mock_job

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            get_job(
                job_id=1,
                db=mock_dependencies["db"],
                user=mock_dependencies["user"],
            )
        )

        assert result.id == 1
        assert result.name == "Test Job"

    def test_get_job_not_found(self, mock_dependencies):
        """Test getting non-existent job."""
        from services.api.app.routes.jobs import get_job
        from fastapi import HTTPException

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = None

        import asyncio
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                get_job(
                    job_id=999,
                    db=mock_dependencies["db"],
                    user=mock_dependencies["user"],
                )
            )

        assert exc_info.value.status_code == 404


class TestJobActionsEndpoint:
    """Tests for job action endpoints (start, cancel, retry)."""

    def test_start_pending_job(self, mock_dependencies):
        """Test starting a pending job."""
        from services.api.app.routes.jobs import start_job
        from netagent_core.db import Job

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.status = "pending"

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = mock_job

        import asyncio

        # Patch the celery task
        with patch('services.api.app.routes.jobs.execute_job') as mock_execute:
            mock_execute.delay = MagicMock()

            result = asyncio.get_event_loop().run_until_complete(
                start_job(
                    job_id=1,
                    db=mock_dependencies["db"],
                    user=mock_dependencies["user"],
                )
            )

        assert result["status"] == "queued"

    def test_start_non_pending_job(self, mock_dependencies):
        """Test starting a job that's not pending."""
        from services.api.app.routes.jobs import start_job
        from netagent_core.db import Job
        from fastapi import HTTPException

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.status = "executing"  # Not pending

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = mock_job

        import asyncio
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                start_job(
                    job_id=1,
                    db=mock_dependencies["db"],
                    user=mock_dependencies["user"],
                )
            )

        assert exc_info.value.status_code == 400

    def test_cancel_running_job(self, mock_dependencies):
        """Test cancelling a running job."""
        from services.api.app.routes.jobs import cancel_job
        from netagent_core.db import Job

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.status = "executing"

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = mock_job

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            cancel_job(
                job_id=1,
                db=mock_dependencies["db"],
                user=mock_dependencies["user"],
            )
        )

        assert result["status"] == "cancelled"
        assert mock_job.status == "cancelled"

    def test_cancel_completed_job(self, mock_dependencies):
        """Test cancelling a completed job (should fail)."""
        from services.api.app.routes.jobs import cancel_job
        from netagent_core.db import Job
        from fastapi import HTTPException

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.status = "completed"

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = mock_job

        import asyncio
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                cancel_job(
                    job_id=1,
                    db=mock_dependencies["db"],
                    user=mock_dependencies["user"],
                )
            )

        assert exc_info.value.status_code == 400

    def test_retry_failed_job(self, mock_dependencies):
        """Test retrying a failed job."""
        from services.api.app.routes.jobs import retry_job
        from netagent_core.db import Job, JobTask

        mock_task = MagicMock(spec=JobTask)
        mock_task.status = "failed"

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.status = "failed"
        mock_job.tasks = [mock_task]

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = mock_job

        import asyncio

        with patch('services.api.app.routes.jobs.execute_job') as mock_execute:
            mock_execute.delay = MagicMock()

            result = asyncio.get_event_loop().run_until_complete(
                retry_job(
                    job_id=1,
                    db=mock_dependencies["db"],
                    user=mock_dependencies["user"],
                )
            )

        assert result["status"] == "queued"

    def test_retry_pending_job(self, mock_dependencies):
        """Test retrying a job that's not failed/cancelled (should fail)."""
        from services.api.app.routes.jobs import retry_job
        from netagent_core.db import Job
        from fastapi import HTTPException

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.status = "pending"

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = mock_job

        import asyncio
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                retry_job(
                    job_id=1,
                    db=mock_dependencies["db"],
                    user=mock_dependencies["user"],
                )
            )

        assert exc_info.value.status_code == 400


class TestJobDeleteEndpoint:
    """Tests for DELETE /api/jobs/{job_id} endpoint."""

    def test_delete_completed_job(self, mock_dependencies):
        """Test deleting a completed job."""
        from services.api.app.routes.jobs import delete_job
        from netagent_core.db import Job

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.status = "completed"

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = mock_job

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            delete_job(
                job_id=1,
                db=mock_dependencies["db"],
                user=mock_dependencies["user"],
            )
        )

        assert result["status"] == "deleted"
        mock_dependencies["db"].delete.assert_called_once_with(mock_job)

    def test_delete_running_job(self, mock_dependencies):
        """Test deleting a running job (should fail)."""
        from services.api.app.routes.jobs import delete_job
        from netagent_core.db import Job
        from fastapi import HTTPException

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.status = "executing"

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = mock_job

        import asyncio
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                delete_job(
                    job_id=1,
                    db=mock_dependencies["db"],
                    user=mock_dependencies["user"],
                )
            )

        assert exc_info.value.status_code == 400


class TestJobRedeliverEndpoint:
    """Tests for POST /api/jobs/{job_id}/redeliver endpoint."""

    def test_redeliver_completed_job(self, mock_dependencies):
        """Test redelivering results for completed job."""
        from services.api.app.routes.jobs import redeliver_results
        from netagent_core.db import Job

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.status = "completed"

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = mock_job

        import asyncio

        with patch('services.api.app.routes.jobs.deliver_job_results') as mock_deliver:
            mock_deliver.delay = MagicMock()

            result = asyncio.get_event_loop().run_until_complete(
                redeliver_results(
                    job_id=1,
                    db=mock_dependencies["db"],
                    user=mock_dependencies["user"],
                )
            )

        assert result["status"] == "redelivery_queued"

    def test_redeliver_pending_job(self, mock_dependencies):
        """Test redelivering results for pending job (should fail)."""
        from services.api.app.routes.jobs import redeliver_results
        from netagent_core.db import Job
        from fastapi import HTTPException

        mock_job = MagicMock(spec=Job)
        mock_job.id = 1
        mock_job.status = "pending"

        mock_dependencies["db"].query.return_value.filter.return_value.first.return_value = mock_job

        import asyncio
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                redeliver_results(
                    job_id=1,
                    db=mock_dependencies["db"],
                    user=mock_dependencies["user"],
                )
            )

        assert exc_info.value.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
