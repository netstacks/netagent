-- Migration: Add dependency tracking to job_tasks table
-- Date: 2026-01-09
-- Description: Adds depends_on and batch_source_task columns for orchestrated execution
--
-- Run this migration against your PostgreSQL database:
--   psql -U netagent -d netagent -f 001_add_jobtask_dependencies.sql

-- Add depends_on column (JSONB array of task sequence numbers)
-- Check if column exists first to make migration idempotent
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'job_tasks' AND column_name = 'depends_on'
    ) THEN
        ALTER TABLE job_tasks ADD COLUMN depends_on JSONB DEFAULT '[]'::jsonb;
        RAISE NOTICE 'Added column depends_on to job_tasks';
    ELSE
        RAISE NOTICE 'Column depends_on already exists';
    END IF;
END $$;

-- Add batch_source_task column (integer reference to source task sequence)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'job_tasks' AND column_name = 'batch_source_task'
    ) THEN
        ALTER TABLE job_tasks ADD COLUMN batch_source_task INTEGER;
        RAISE NOTICE 'Added column batch_source_task to job_tasks';
    ELSE
        RAISE NOTICE 'Column batch_source_task already exists';
    END IF;
END $$;

-- Add comments for documentation
COMMENT ON COLUMN job_tasks.depends_on IS 'List of task sequence numbers this task depends on';
COMMENT ON COLUMN job_tasks.batch_source_task IS 'Sequence number of task that provides batch items';

-- Verify the migration
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'job_tasks'
AND column_name IN ('depends_on', 'batch_source_task');
