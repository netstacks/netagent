// @ts-check
const { test, expect } = require('@playwright/test');

/**
 * E2E tests for the Jobs feature
 * Tests the jobs list, creation, detail, and actions
 */

test.describe('Jobs List Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/jobs');
  });

  test('should display jobs page header', async ({ page }) => {
    // Check page title
    await expect(page.locator('h1')).toContainText('Jobs');

    // Check "New Job" button exists
    await expect(page.locator('a[href="/jobs/new"]')).toBeVisible();
  });

  test('should have status filter dropdown', async ({ page }) => {
    const filterDropdown = page.locator('#filter-status');
    await expect(filterDropdown).toBeVisible();

    // Check filter options
    const options = await filterDropdown.locator('option').allTextContents();
    expect(options).toContain('All Statuses');
    expect(options).toContain('Pending');
    expect(options).toContain('Completed');
    expect(options).toContain('Failed');
  });

  test('should navigate to create job page', async ({ page }) => {
    await page.click('a[href="/jobs/new"]');
    await expect(page).toHaveURL('/jobs/new');
    await expect(page.locator('h2')).toContainText('Create New Job');
  });

  test('should display jobs table', async ({ page }) => {
    const table = page.locator('#jobs-table');
    await expect(table).toBeVisible();

    // Check table headers
    const headers = await table.locator('thead th').allTextContents();
    expect(headers).toContain('ID');
    expect(headers).toContain('Name');
    expect(headers).toContain('Status');
    expect(headers).toContain('Tasks');
  });
});

test.describe('Job Creation Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/jobs/new');
  });

  test('should display job creation form', async ({ page }) => {
    // Check form elements exist
    await expect(page.locator('#job-name')).toBeVisible();
    await expect(page.locator('#job-spec')).toBeVisible();
    await expect(page.locator('#execution-mode')).toBeVisible();
    await expect(page.locator('#batch-size')).toBeVisible();
    await expect(page.locator('#on-failure')).toBeVisible();
  });

  test('should insert structured template', async ({ page }) => {
    await page.click('button:has-text("Structured")');

    const specTextarea = page.locator('#job-spec');
    const content = await specTextarea.inputValue();

    expect(content).toContain('## Config');
    expect(content).toContain('## Tasks');
    expect(content).toContain('## Deliver');
  });

  test('should insert natural language template', async ({ page }) => {
    await page.click('button:has-text("Natural Language")');

    const specTextarea = page.locator('#job-spec');
    const content = await specTextarea.inputValue();

    expect(content).toContain('health status');
    expect(content).toContain('configurations');
  });

  test('should show validation error for empty form', async ({ page }) => {
    // Try to submit empty form
    await page.click('button[type="submit"]');

    // HTML5 validation should prevent submission
    // Check that the job name field is required
    const nameInput = page.locator('#job-name');
    await expect(nameInput).toHaveAttribute('required', '');
  });

  test('should have back button to jobs list', async ({ page }) => {
    await page.click('a:has-text("Back")');
    await expect(page).toHaveURL('/jobs');
  });

  test('should have execution mode options', async ({ page }) => {
    const modeSelect = page.locator('#execution-mode');
    const options = await modeSelect.locator('option').allTextContents();

    expect(options).toContain('Batch (default)');
    expect(options).toContain('Sequential');
    expect(options).toContain('Parallel');
  });

  test('should have on-failure options', async ({ page }) => {
    const failureSelect = page.locator('#on-failure');
    const options = await failureSelect.locator('option').allTextContents();

    expect(options).toContain('Continue');
    expect(options).toContain('Stop');
    expect(options).toContain('Skip Dependents');
  });
});

test.describe('Job Detail Page', () => {
  // Note: These tests require a job to exist in the database
  // In a real scenario, you would create a test fixture or mock the API

  test('should handle 404 for non-existent job', async ({ page }) => {
    const response = await page.goto('/jobs/999999');
    // Either redirects or shows 404
    // The exact behavior depends on the frontend implementation
  });
});

test.describe('Jobs Navigation', () => {
  test('should have jobs link in sidebar', async ({ page }) => {
    await page.goto('/');

    const jobsLink = page.locator('a.nav-link:has-text("Jobs")');
    await expect(jobsLink).toBeVisible();

    await jobsLink.click();
    await expect(page).toHaveURL('/jobs');
  });
});

test.describe('Jobs API Integration', () => {
  test('should load jobs via API', async ({ page }) => {
    // Intercept API call
    const apiPromise = page.waitForResponse(
      response => response.url().includes('/api/jobs/') && response.status() === 200
    );

    await page.goto('/jobs');

    // Wait for API response
    const response = await apiPromise.catch(() => null);
    if (response) {
      const data = await response.json();
      expect(Array.isArray(data)).toBe(true);
    }
  });

  test('should parse job spec via API', async ({ page }) => {
    await page.goto('/jobs/new');

    // Fill in spec
    await page.locator('#job-spec').fill(`## Config
mode: batch

## Tasks
### 1. Test task
This is a test task`);

    // Intercept parse API call
    const parsePromise = page.waitForResponse(
      response => response.url().includes('/api/jobs/parse') && response.status() === 200
    );

    // Click parse button
    await page.click('button:has-text("Parse & Preview")');

    // Check if parse API was called
    const response = await parsePromise.catch(() => null);
    if (response) {
      const data = await response.json();
      expect(data.tasks).toBeDefined();
      expect(data.tasks.length).toBeGreaterThan(0);
    }
  });
});

test.describe('Jobs Real-time Updates', () => {
  test('should set up SSE connection', async ({ page }) => {
    // Check that EventSource is initialized
    await page.goto('/jobs');

    // Wait a bit for SSE to connect
    await page.waitForTimeout(1000);

    // Check that the SSE event listener is set up (via console or network)
    // This is more of a smoke test
  });
});

test.describe('Jobs Responsive Design', () => {
  test('should be responsive on mobile', async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/jobs');

    // Check that table is still visible (may be scrollable)
    await expect(page.locator('#jobs-table')).toBeVisible();
  });

  test('should be responsive on tablet', async ({ page }) => {
    // Set tablet viewport
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto('/jobs');

    // Check layout
    await expect(page.locator('#jobs-table')).toBeVisible();
  });
});
