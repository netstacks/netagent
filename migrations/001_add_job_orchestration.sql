-- Migration: Add Job Orchestration Tables
-- Date: 2026-01-08
-- Description: Creates jobs and job_tasks tables for task orchestration feature

-- Add ephemeral agent fields to agents table
ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_ephemeral BOOLEAN DEFAULT FALSE;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS created_for_job_id INTEGER;

-- Add job_id to approvals table
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS job_id INTEGER;

-- Create jobs table
CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,

    -- Job specification
    spec_raw TEXT NOT NULL,
    spec_parsed JSONB,

    -- Execution configuration
    status VARCHAR(30) DEFAULT 'pending',
    execution_mode VARCHAR(20) DEFAULT 'batch',
    batch_size INTEGER DEFAULT 5,
    on_failure VARCHAR(20) DEFAULT 'continue',
    retry_count INTEGER DEFAULT 3,
    validation_mode VARCHAR(20) DEFAULT 'ai',

    -- Delivery configuration
    delivery_config JSONB,

    -- Execution tracking
    orchestrator_session_id INTEGER REFERENCES agent_sessions(id),
    results JSONB,
    error_summary TEXT,

    -- Progress tracking
    total_tasks INTEGER DEFAULT 0,
    completed_tasks INTEGER DEFAULT 0,
    failed_tasks INTEGER DEFAULT 0,

    -- Ownership
    created_by INTEGER REFERENCES users(id),

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- Create indexes for jobs
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_by ON jobs(created_by);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);

-- Create job_tasks table
CREATE TABLE IF NOT EXISTS job_tasks (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,

    -- Task definition
    sequence INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    spec JSONB NOT NULL,

    -- Agent assignment
    agent_id INTEGER REFERENCES agents(id),
    agent_name_hint VARCHAR(100),
    is_ephemeral_agent BOOLEAN DEFAULT FALSE,
    ephemeral_agent_id INTEGER REFERENCES agents(id),
    ephemeral_prompt TEXT,

    -- Execution
    session_id INTEGER REFERENCES agent_sessions(id),
    status VARCHAR(30) DEFAULT 'pending',

    -- For batch tasks
    is_batch BOOLEAN DEFAULT FALSE,
    batch_items JSONB,
    batch_results JSONB,

    -- Results
    result JSONB,
    error TEXT,

    -- Timestamps
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- Create indexes for job_tasks
CREATE INDEX IF NOT EXISTS idx_job_tasks_job_id ON job_tasks(job_id);
CREATE INDEX IF NOT EXISTS idx_job_tasks_status ON job_tasks(status);

-- Add foreign key constraints for new columns
ALTER TABLE agents
    ADD CONSTRAINT fk_agents_created_for_job
    FOREIGN KEY (created_for_job_id) REFERENCES jobs(id);

ALTER TABLE approvals
    ADD CONSTRAINT fk_approvals_job
    FOREIGN KEY (job_id) REFERENCES jobs(id);

-- Comments for documentation
COMMENT ON TABLE jobs IS 'Orchestrated jobs containing multiple tasks';
COMMENT ON TABLE job_tasks IS 'Individual tasks within a job workflow';
COMMENT ON COLUMN jobs.status IS 'pending, awaiting_confirmation, queued, executing, validating, awaiting_approval, delivering, completed, failed, cancelled';
COMMENT ON COLUMN jobs.execution_mode IS 'parallel, sequential, batch';
COMMENT ON COLUMN jobs.on_failure IS 'stop, continue, retry';
COMMENT ON COLUMN jobs.validation_mode IS 'ai, human, ai+human';
