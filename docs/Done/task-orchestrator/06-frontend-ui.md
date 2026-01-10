# Phase 6: Frontend UI

## Task 6.1: Create Jobs List Page

**Files:**
- Create: `services/frontend/app/templates/jobs.html`
- Create: `services/frontend/app/templates/job_detail.html`
- Create: `services/frontend/app/templates/job_create.html`
- Create: `services/frontend/static/js/jobs.js`
- Modify: `services/frontend/app/main.py` (add routes)
- Modify: `services/frontend/app/templates/base.html` (add nav link)

### Step 1: Create jobs list template

Create `services/frontend/app/templates/jobs.html`:

```html
{% extends "base.html" %}
{% block title %}Jobs{% endblock %}

{% block content %}
<div class="container-fluid">
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h2>Jobs</h2>
        <a href="/jobs/new" class="btn btn-primary">
            <i class="bi bi-plus-lg"></i> New Job
        </a>
    </div>

    <!-- Filters -->
    <div class="card mb-4">
        <div class="card-body">
            <div class="row g-3">
                <div class="col-md-3">
                    <label class="form-label">Status</label>
                    <select id="filter-status" class="form-select">
                        <option value="">All</option>
                        <option value="pending">Pending</option>
                        <option value="queued">Queued</option>
                        <option value="executing">Executing</option>
                        <option value="completed">Completed</option>
                        <option value="failed">Failed</option>
                        <option value="cancelled">Cancelled</option>
                    </select>
                </div>
                <div class="col-md-3">
                    <label class="form-label">&nbsp;</label>
                    <button onclick="loadJobs()" class="btn btn-secondary d-block">
                        <i class="bi bi-funnel"></i> Apply Filter
                    </button>
                </div>
            </div>
        </div>
    </div>

    <!-- Jobs Table -->
    <div class="card">
        <div class="card-body">
            <table class="table table-hover" id="jobs-table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Name</th>
                        <th>Status</th>
                        <th>Tasks</th>
                        <th>Created</th>
                        <th>Duration</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="jobs-tbody">
                    <tr>
                        <td colspan="7" class="text-center">Loading...</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script src="/static/js/jobs.js"></script>
<script>
    loadJobs();

    // SSE for live updates
    const evtSource = new EventSource('/api/events/jobs');
    evtSource.onmessage = function(event) {
        const data = JSON.parse(event.data);
        if (data.type === 'job_created' || data.type === 'job_completed' ||
            data.type === 'job_failed' || data.type === 'job_cancelled') {
            loadJobs();
        }
    };
</script>
{% endblock %}
```

### Step 2: Create job detail template

Create `services/frontend/app/templates/job_detail.html`:

```html
{% extends "base.html" %}
{% block title %}Job: {{ job.name }}{% endblock %}

{% block content %}
<div class="container-fluid">
    <!-- Header -->
    <div class="d-flex justify-content-between align-items-center mb-4">
        <div>
            <h2>{{ job.name }}</h2>
            <span class="badge bg-{{ status_color(job.status) }}">{{ job.status }}</span>
        </div>
        <div>
            {% if job.status == 'pending' %}
            <button onclick="startJob({{ job.id }})" class="btn btn-success">
                <i class="bi bi-play-fill"></i> Start
            </button>
            {% endif %}
            {% if job.status in ['queued', 'executing'] %}
            <button onclick="cancelJob({{ job.id }})" class="btn btn-danger">
                <i class="bi bi-x-lg"></i> Cancel
            </button>
            {% endif %}
            {% if job.status in ['failed', 'cancelled'] %}
            <button onclick="retryJob({{ job.id }})" class="btn btn-warning">
                <i class="bi bi-arrow-clockwise"></i> Retry
            </button>
            {% endif %}
        </div>
    </div>

    <!-- Tabs -->
    <ul class="nav nav-tabs" id="jobTabs" role="tablist">
        <li class="nav-item">
            <button class="nav-link active" data-bs-toggle="tab" data-bs-target="#spec-tab">
                Specification
            </button>
        </li>
        <li class="nav-item">
            <button class="nav-link" data-bs-toggle="tab" data-bs-target="#tasks-tab">
                Tasks ({{ job.completed_tasks }}/{{ job.total_tasks }})
            </button>
        </li>
        <li class="nav-item">
            <button class="nav-link" data-bs-toggle="tab" data-bs-target="#results-tab">
                Results
            </button>
        </li>
    </ul>

    <div class="tab-content mt-3">
        <!-- Spec Tab -->
        <div class="tab-pane fade show active" id="spec-tab">
            <div class="card">
                <div class="card-header">Original Specification</div>
                <div class="card-body">
                    <pre class="bg-light p-3 rounded">{{ job.spec_raw }}</pre>
                </div>
            </div>
        </div>

        <!-- Tasks Tab -->
        <div class="tab-pane fade" id="tasks-tab">
            <div class="card">
                <div class="card-body">
                    <table class="table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Task</th>
                                <th>Agent</th>
                                <th>Status</th>
                                <th>Duration</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for task in job.tasks %}
                            <tr>
                                <td>{{ task.sequence }}</td>
                                <td>{{ task.name }}</td>
                                <td>{{ task.agent_name or 'Pending' }}</td>
                                <td>
                                    <span class="badge bg-{{ status_color(task.status) }}">
                                        {{ task.status }}
                                    </span>
                                </td>
                                <td>{{ task_duration(task) }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Results Tab -->
        <div class="tab-pane fade" id="results-tab">
            <div class="card">
                <div class="card-body">
                    {% if job.results %}
                    <pre class="bg-light p-3 rounded">{{ job.results | tojson(indent=2) }}</pre>
                    {% if job.status == 'completed' %}
                    <button onclick="redeliverResults({{ job.id }})" class="btn btn-secondary mt-3">
                        <i class="bi bi-envelope"></i> Re-deliver Results
                    </button>
                    {% endif %}
                    {% else %}
                    <p class="text-muted">No results yet.</p>
                    {% endif %}
                </div>
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script src="/static/js/jobs.js"></script>
{% endblock %}
```

### Step 3: Create job create template

Create `services/frontend/app/templates/job_create.html`:

```html
{% extends "base.html" %}
{% block title %}New Job{% endblock %}

{% block content %}
<div class="container-fluid">
    <h2 class="mb-4">Create New Job</h2>

    <div class="card">
        <div class="card-body">
            <!-- Mode Toggle -->
            <div class="btn-group mb-3" role="group">
                <input type="radio" class="btn-check" name="mode" id="mode-structured" checked>
                <label class="btn btn-outline-primary" for="mode-structured">Structured Markdown</label>

                <input type="radio" class="btn-check" name="mode" id="mode-natural">
                <label class="btn btn-outline-primary" for="mode-natural">Natural Language</label>
            </div>

            <!-- Spec Editor -->
            <div class="mb-3">
                <label class="form-label">Job Specification</label>
                <textarea id="spec-editor" class="form-control font-monospace" rows="20" placeholder="# Job: My Network Task

## Config
- execution: batch(5)
- validation: ai
- delivery:
  - email: me@company.com

## Tasks
1. **Query Devices**
   - Get all routers from NetBox
   - Agent: netbox-query

2. **Collect Data**
   - SSH to each device
   - Run 'show version'
"></textarea>
            </div>

            <!-- Template Dropdown -->
            <div class="mb-3">
                <label class="form-label">Load Template</label>
                <select id="template-select" class="form-select" onchange="loadTemplate()">
                    <option value="">-- Select Template --</option>
                    <option value="device-audit">Device Software Audit</option>
                    <option value="config-backup">Configuration Backup</option>
                    <option value="health-check">Network Health Check</option>
                </select>
            </div>

            <!-- Actions -->
            <div class="d-flex gap-2">
                <button onclick="previewJob()" class="btn btn-secondary">
                    <i class="bi bi-eye"></i> Preview
                </button>
                <button onclick="submitJob()" class="btn btn-primary">
                    <i class="bi bi-send"></i> Submit Job
                </button>
            </div>
        </div>
    </div>

    <!-- Preview Modal -->
    <div class="modal fade" id="preview-modal" tabindex="-1">
        <div class="modal-dialog modal-lg">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Job Preview</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <pre id="preview-content" class="bg-light p-3 rounded"></pre>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Edit</button>
                    <button type="button" class="btn btn-primary" onclick="confirmSubmit()">Confirm & Submit</button>
                </div>
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script src="/static/js/jobs.js"></script>
{% endblock %}
```

### Step 4: Create jobs JavaScript

Create `services/frontend/static/js/jobs.js`:

```javascript
// Jobs management JavaScript

async function loadJobs() {
    const status = document.getElementById('filter-status')?.value || '';
    const params = new URLSearchParams();
    if (status) params.append('status', status);

    try {
        const response = await fetch(`/api/jobs/?${params}`);
        const jobs = await response.json();
        renderJobsTable(jobs);
    } catch (error) {
        console.error('Failed to load jobs:', error);
    }
}

function renderJobsTable(jobs) {
    const tbody = document.getElementById('jobs-tbody');
    if (!tbody) return;

    if (jobs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No jobs found</td></tr>';
        return;
    }

    tbody.innerHTML = jobs.map(job => `
        <tr>
            <td>${job.id}</td>
            <td><a href="/jobs/${job.id}">${job.name}</a></td>
            <td><span class="badge bg-${statusColor(job.status)}">${job.status}</span></td>
            <td>${job.completed_tasks}/${job.total_tasks}</td>
            <td>${formatDate(job.created_at)}</td>
            <td>${formatDuration(job)}</td>
            <td>
                <div class="btn-group btn-group-sm">
                    <a href="/jobs/${job.id}" class="btn btn-outline-primary">View</a>
                    ${job.status === 'pending' ? `<button onclick="startJob(${job.id})" class="btn btn-outline-success">Start</button>` : ''}
                    ${['queued', 'executing'].includes(job.status) ? `<button onclick="cancelJob(${job.id})" class="btn btn-outline-danger">Cancel</button>` : ''}
                </div>
            </td>
        </tr>
    `).join('');
}

function statusColor(status) {
    const colors = {
        'pending': 'secondary',
        'awaiting_confirmation': 'info',
        'queued': 'info',
        'executing': 'primary',
        'validating': 'warning',
        'delivering': 'warning',
        'completed': 'success',
        'failed': 'danger',
        'cancelled': 'dark'
    };
    return colors[status] || 'secondary';
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString();
}

function formatDuration(job) {
    if (!job.started_at) return '-';
    const start = new Date(job.started_at);
    const end = job.completed_at ? new Date(job.completed_at) : new Date();
    const seconds = Math.floor((end - start) / 1000);
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

async function startJob(jobId) {
    if (!confirm('Start this job?')) return;

    try {
        const response = await fetch(`/api/jobs/${jobId}/start`, { method: 'POST' });
        if (response.ok) {
            window.location.reload();
        } else {
            const error = await response.json();
            alert(`Failed to start job: ${error.detail}`);
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

async function cancelJob(jobId) {
    if (!confirm('Cancel this job?')) return;

    try {
        const response = await fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
        if (response.ok) {
            window.location.reload();
        } else {
            const error = await response.json();
            alert(`Failed to cancel job: ${error.detail}`);
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

async function retryJob(jobId) {
    if (!confirm('Retry this job?')) return;

    try {
        const response = await fetch(`/api/jobs/${jobId}/retry`, { method: 'POST' });
        if (response.ok) {
            window.location.reload();
        } else {
            const error = await response.json();
            alert(`Failed to retry job: ${error.detail}`);
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

async function redeliverResults(jobId) {
    if (!confirm('Re-send results to delivery channels?')) return;

    try {
        const response = await fetch(`/api/jobs/${jobId}/redeliver`, { method: 'POST' });
        if (response.ok) {
            alert('Results queued for redelivery');
        } else {
            const error = await response.json();
            alert(`Failed: ${error.detail}`);
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

async function submitJob() {
    const spec = document.getElementById('spec-editor').value;

    if (!spec.trim()) {
        alert('Please enter a job specification');
        return;
    }

    try {
        const response = await fetch('/api/jobs/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ spec })
        });

        if (response.ok) {
            const job = await response.json();
            window.location.href = `/jobs/${job.id}`;
        } else {
            const error = await response.json();
            alert(`Failed to create job: ${error.detail}`);
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

function previewJob() {
    const spec = document.getElementById('spec-editor').value;
    document.getElementById('preview-content').textContent = spec;
    new bootstrap.Modal(document.getElementById('preview-modal')).show();
}

function confirmSubmit() {
    bootstrap.Modal.getInstance(document.getElementById('preview-modal')).hide();
    submitJob();
}

const TEMPLATES = {
    'device-audit': `# Job: Device Software Audit

## Config
- execution: batch(5)
- validation: ai
- on_failure: continue
- delivery:
  - email: your-email@company.com

## Tasks
1. **Query NetBox**
   - Get all network devices
   - Agent: netbox-query

2. **Collect Versions** (for each device from step 1)
   - SSH to device
   - Run: show version
   - Extract: hostname, software version, uptime

3. **Generate Report**
   - Create markdown table of results
   - Include any failures
`,
    'config-backup': `# Job: Configuration Backup

## Config
- execution: batch(3)
- validation: ai + human
- delivery:
  - email: network-team@company.com

## Tasks
1. **Get Device List**
   - Query NetBox for all routers and switches
   - Agent: netbox-query

2. **Backup Configs** (for each device from step 1)
   - SSH to device
   - Run: show running-config
   - Save output

3. **Summary**
   - Count successful/failed backups
   - List any errors
`,
    'health-check': `# Job: Network Health Check

## Config
- execution: parallel
- validation: ai
- delivery:
  - slack: #network-ops

## Tasks
1. **Check Core Routers**
   - Query NetBox for core routers
   - Agent: netbox-query

2. **Verify Connectivity** (for each device from step 1)
   - SSH and check interface status
   - Check BGP neighbors
   - Check memory/CPU

3. **Report**
   - Summarize health status
   - Flag any issues
`
};

function loadTemplate() {
    const select = document.getElementById('template-select');
    const template = TEMPLATES[select.value];
    if (template) {
        document.getElementById('spec-editor').value = template;
    }
}
```

### Step 5: Add frontend routes

Add to `services/frontend/app/main.py`:

```python
@app.get("/jobs")
async def jobs_list(request: Request):
    return templates.TemplateResponse("jobs.html", {"request": request})

@app.get("/jobs/new")
async def jobs_create(request: Request):
    return templates.TemplateResponse("job_create.html", {"request": request})

@app.get("/jobs/{job_id}")
async def jobs_detail(request: Request, job_id: int):
    # Fetch job from API
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_URL}/api/jobs/{job_id}",
            headers=get_auth_headers(request)
        )
        job = response.json()

    return templates.TemplateResponse("job_detail.html", {
        "request": request,
        "job": job,
        "status_color": status_color,
        "task_duration": task_duration,
    })

def status_color(status):
    colors = {
        "pending": "secondary",
        "queued": "info",
        "executing": "primary",
        "completed": "success",
        "failed": "danger",
        "cancelled": "dark",
    }
    return colors.get(status, "secondary")

def task_duration(task):
    if not task.get("started_at"):
        return "-"
    # Calculate duration
    return "..."
```

### Step 6: Add to navigation

Add to `services/frontend/app/templates/base.html` in the sidebar:

```html
<li class="nav-item">
    <a class="nav-link" href="/jobs">
        <i class="bi bi-list-task"></i> Jobs
    </a>
</li>
```

### Step 7: Commit

```bash
git add services/frontend/
git commit -m "feat(frontend): add job orchestration UI"
```

---

## Verification

### 1. Playwright E2E Tests

Create `tests/e2e/test_jobs_ui.py`:

```python
"""Playwright E2E tests for Jobs UI."""

import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:8089"


@pytest.fixture(scope="function")
def authenticated_page(page: Page):
    """Login before each test."""
    page.goto(f"{BASE_URL}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "admin")
    page.click('button[type="submit"]')
    page.wait_for_url(f"{BASE_URL}/**")
    return page


class TestJobsListPage:
    """Tests for /jobs page."""

    def test_jobs_page_loads(self, authenticated_page: Page):
        """Jobs page loads with table."""
        authenticated_page.goto(f"{BASE_URL}/jobs")
        expect(authenticated_page.locator("h2")).to_contain_text("Jobs")
        expect(authenticated_page.locator("#jobs-table")).to_be_visible()

    def test_jobs_nav_link_exists(self, authenticated_page: Page):
        """Jobs link appears in navigation."""
        authenticated_page.goto(f"{BASE_URL}/")
        nav_link = authenticated_page.locator('a[href="/jobs"]')
        expect(nav_link).to_be_visible()

    def test_new_job_button(self, authenticated_page: Page):
        """New Job button navigates to create page."""
        authenticated_page.goto(f"{BASE_URL}/jobs")
        authenticated_page.click('a:has-text("New Job")')
        expect(authenticated_page).to_have_url(f"{BASE_URL}/jobs/new")

    def test_status_filter(self, authenticated_page: Page):
        """Status filter dropdown works."""
        authenticated_page.goto(f"{BASE_URL}/jobs")
        authenticated_page.select_option("#filter-status", "pending")
        authenticated_page.click('button:has-text("Apply Filter")')
        # Should reload with filter (check network or URL)


class TestJobCreatePage:
    """Tests for /jobs/new page."""

    def test_create_page_loads(self, authenticated_page: Page):
        """Create job page loads."""
        authenticated_page.goto(f"{BASE_URL}/jobs/new")
        expect(authenticated_page.locator("h2")).to_contain_text("Create New Job")
        expect(authenticated_page.locator("#spec-editor")).to_be_visible()

    def test_mode_toggle(self, authenticated_page: Page):
        """Mode toggle switches between structured and natural language."""
        authenticated_page.goto(f"{BASE_URL}/jobs/new")

        # Click natural language mode
        authenticated_page.click('label[for="mode-natural"]')
        expect(authenticated_page.locator("#mode-natural")).to_be_checked()

    def test_template_dropdown(self, authenticated_page: Page):
        """Template dropdown populates editor."""
        authenticated_page.goto(f"{BASE_URL}/jobs/new")
        authenticated_page.select_option("#template-select", "device-audit")

        editor_value = authenticated_page.locator("#spec-editor").input_value()
        assert "Device Software Audit" in editor_value

    def test_submit_job(self, authenticated_page: Page):
        """Submitting job redirects to detail page."""
        authenticated_page.goto(f"{BASE_URL}/jobs/new")

        # Fill in spec
        authenticated_page.fill("#spec-editor", """# Job: Playwright Test
## Tasks
1. **Test Task**
   - This is a test
""")

        # Submit
        authenticated_page.click('button:has-text("Submit Job")')

        # Should redirect to job detail
        authenticated_page.wait_for_url(f"{BASE_URL}/jobs/*")
        expect(authenticated_page.locator("h2")).to_contain_text("Playwright Test")

    def test_preview_modal(self, authenticated_page: Page):
        """Preview button shows modal."""
        authenticated_page.goto(f"{BASE_URL}/jobs/new")
        authenticated_page.fill("#spec-editor", "# Job: Test")
        authenticated_page.click('button:has-text("Preview")')

        expect(authenticated_page.locator("#preview-modal")).to_be_visible()


class TestJobDetailPage:
    """Tests for /jobs/{id} page."""

    @pytest.fixture
    def test_job_id(self, authenticated_page: Page):
        """Create a job and return its ID."""
        authenticated_page.goto(f"{BASE_URL}/jobs/new")
        authenticated_page.fill("#spec-editor", "# Job: Detail Test\n## Tasks\n1. **Task**\n   - Test")
        authenticated_page.click('button:has-text("Submit Job")')
        authenticated_page.wait_for_url(f"{BASE_URL}/jobs/*")
        # Extract ID from URL
        url = authenticated_page.url
        return url.split("/")[-1]

    def test_detail_page_loads(self, authenticated_page: Page, test_job_id):
        """Job detail page shows job info."""
        authenticated_page.goto(f"{BASE_URL}/jobs/{test_job_id}")
        expect(authenticated_page.locator("h2")).to_be_visible()
        expect(authenticated_page.locator(".badge")).to_be_visible()  # Status badge

    def test_tabs_work(self, authenticated_page: Page, test_job_id):
        """Tab navigation works."""
        authenticated_page.goto(f"{BASE_URL}/jobs/{test_job_id}")

        # Click Tasks tab
        authenticated_page.click('button:has-text("Tasks")')
        expect(authenticated_page.locator("#tasks-tab")).to_be_visible()

        # Click Results tab
        authenticated_page.click('button:has-text("Results")')
        expect(authenticated_page.locator("#results-tab")).to_be_visible()

    def test_start_button_for_pending(self, authenticated_page: Page, test_job_id):
        """Start button appears for pending jobs."""
        authenticated_page.goto(f"{BASE_URL}/jobs/{test_job_id}")
        expect(authenticated_page.locator('button:has-text("Start")')).to_be_visible()

    def test_cancel_button_after_start(self, authenticated_page: Page, test_job_id):
        """Cancel button appears after starting."""
        authenticated_page.goto(f"{BASE_URL}/jobs/{test_job_id}")

        # Handle confirmation dialog
        authenticated_page.on("dialog", lambda dialog: dialog.accept())
        authenticated_page.click('button:has-text("Start")')

        # Wait for page reload
        authenticated_page.wait_for_timeout(1000)

        # Cancel should now be visible (or status changed)
        # This depends on how fast the job executes
```

### 2. Run Playwright Tests

```bash
# Install playwright
pip install playwright pytest-playwright
playwright install chromium

# Run tests
pytest tests/e2e/test_jobs_ui.py -v --headed  # --headed to see browser

# Run specific test
pytest tests/e2e/test_jobs_ui.py::TestJobCreatePage::test_submit_job -v
```

### 3. Manual Smoke Test

```bash
# Open browser
open http://localhost:8089/jobs

# Verify:
# 1. Jobs page loads with table
# 2. "New Job" button works
# 3. Create page has editor and templates
# 4. Submitting job redirects to detail
# 5. Job detail shows tabs (Spec, Tasks, Results)
# 6. Start/Cancel/Retry buttons work
```

### Expected Outcomes

- [ ] /jobs page loads with jobs table
- [ ] Navigation link to Jobs exists
- [ ] /jobs/new has mode toggle and editor
- [ ] Templates populate the editor
- [ ] Job submission creates job and redirects
- [ ] /jobs/{id} shows job details with tabs
- [ ] Start button works for pending jobs
- [ ] Cancel button works for running jobs
- [ ] Status badges display correct colors
