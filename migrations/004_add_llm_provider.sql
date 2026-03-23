-- Migration: Add llm_provider column to agents table
-- Supports multi-provider LLM: gemini (via Apigee), bedrock (Anthropic via AWS)

ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_provider VARCHAR(20) DEFAULT 'gemini';

COMMENT ON COLUMN agents.llm_provider IS 'LLM provider: gemini (Apigee/Vertex), bedrock (AWS Bedrock/Anthropic)';
