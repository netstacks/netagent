# Task Orchestrator Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable network engineers to submit complex multi-step tasks via markdown or natural language, with automatic worker orchestration, validation, and delivery.

**Architecture:** A Job model tracks orchestrated workflows. An orchestrator Celery task parses job specs, spawns worker sessions (reusing existing agents or creating ephemeral ones), aggregates results, validates, delivers via existing notification tasks, and cleans up ephemeral resources.

**Tech Stack:** FastAPI, SQLAlchemy, Celery, Redis, PostgreSQL, existing notification tasks (email, Slack), existing handoff patterns.

---

## Plan Files

| File | Phase | Description |
|------|-------|-------------|
| [01-database-models.md](01-database-models.md) | Phase 1 | Job and JobTask database models |
| [02-job-parser.md](02-job-parser.md) | Phase 2 | Markdown/NL job specification parser |
| [03-agent-matcher.md](03-agent-matcher.md) | Phase 3 | Smart agent matching algorithm |
| [04-api-endpoints.md](04-api-endpoints.md) | Phase 4 | FastAPI job management routes |
| [05-job-executor.md](05-job-executor.md) | Phase 5 | Celery orchestration task |
| [06-frontend-ui.md](06-frontend-ui.md) | Phase 6 | Jobs UI pages |
| [07-testing.md](07-testing.md) | Phase 7 | Unit and integration tests |
| [08-agent-memory.md](08-agent-memory.md) | Phase 8 | Persistent agent memory system |

> **Important:** Each phase includes a **Verification** section at the end. Run all verification steps before proceeding to the next phase to ensure everything works correctly.

### Verification Approach

| Phase | Verification Type |
|-------|-------------------|
| 1-3 | Python scripts testing models/services |
| 4-5 | curl API smoke tests + Celery logs |
| 6 | **Playwright E2E tests** for WebUI |
| 7 | pytest with coverage |
| 8 | Python + curl + Playwright |

---

## Design Decisions Summary

| Decision | Choice |
|----------|--------|
| Agent Model | Hybrid: Pre-built specialists + ephemeral auto-generated |
| Validation | AI sanity check (always) + Human approval (configurable per-job) |
| Execution | Batch by default (N at a time), configurable: parallel/sequential/batch |
| Job Format | Hybrid: Structured markdown OR natural language (AI generates structure for confirmation) |
| Error Handling | Continue and report by default, configurable: stop/continue/retry(N) |
| Delivery | Multiple channels: email, Slack, S3, webhook (configurable per-job) |
| Agent Selection | Smart match to existing agents first, generate ephemeral only if no match |
| Agent Memory | Persistent memory for preferences, facts, and session summaries |

---

## Existing Infrastructure to Reuse

- **Notifications:** `services/worker/app/tasks/notifications.py` - email, Slack, Jira
- **Agent Executor:** `services/worker/app/tasks/agent_executor.py` - session execution
- **Approval System:** `Approval` model + routes in `services/api/app/routes/approvals.py`
- **Handoff Pattern:** `shared/netagent_core/tools/handoff_tool.py` - parent/child sessions
- **Redis Events:** `shared/netagent_core/redis_events.py` - pub/sub for live updates
