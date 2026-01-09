-- Migration: Add Agent Memory System
-- Date: 2026-01-08
-- Description: Creates memories and session_summaries tables for agent memory feature

-- Create memories table
CREATE TABLE IF NOT EXISTS memories (
    id SERIAL PRIMARY KEY,

    -- Memory content
    content TEXT NOT NULL,
    memory_type VARCHAR(30) NOT NULL,  -- preference, fact, summary, instruction

    -- Scoping (null = global)
    user_id INTEGER REFERENCES users(id),
    agent_id INTEGER REFERENCES agents(id),

    -- Source tracking
    source_session_id INTEGER REFERENCES agent_sessions(id),
    source_job_id INTEGER REFERENCES jobs(id),

    -- Metadata
    category VARCHAR(50),
    tags JSONB DEFAULT '[]',
    confidence FLOAT DEFAULT 1.0,

    -- Vector embedding (stored as JSONB for compatibility, upgrade to pgvector if available)
    embedding JSONB,

    -- Lifecycle
    is_active BOOLEAN DEFAULT TRUE,
    expires_at TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    last_accessed_at TIMESTAMP,

    -- Audit
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for memories
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_user_id ON memories(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_agent_id ON memories(agent_id);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_memories_is_active ON memories(is_active);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(user_id, agent_id, is_active);
CREATE INDEX IF NOT EXISTS idx_memories_category_active ON memories(category, is_active);

-- Create session_summaries table
CREATE TABLE IF NOT EXISTS session_summaries (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE UNIQUE,

    -- Summary content
    summary TEXT NOT NULL,
    key_actions JSONB DEFAULT '[]',
    key_findings JSONB DEFAULT '[]',
    tools_used JSONB DEFAULT '[]',

    -- Extracted memories
    extracted_memory_ids JSONB DEFAULT '[]',

    -- Metadata
    message_count INTEGER,
    tool_call_count INTEGER,
    duration_seconds INTEGER,

    -- Vector embedding
    embedding JSONB,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create index for session_summaries
CREATE INDEX IF NOT EXISTS idx_session_summaries_session_id ON session_summaries(session_id);

-- Comments for documentation
COMMENT ON TABLE memories IS 'Persistent memories for agents - facts, preferences, and learnings';
COMMENT ON TABLE session_summaries IS 'Auto-generated summaries of completed agent sessions';
COMMENT ON COLUMN memories.memory_type IS 'preference (user likes/dislikes), fact (objective info), summary (condensed info), instruction (how to do something)';
COMMENT ON COLUMN memories.user_id IS 'If set, memory is user-specific; if null with agent_id set, agent-specific; if both null, global';
COMMENT ON COLUMN memories.confidence IS 'Confidence score 0-1 for this memory';
