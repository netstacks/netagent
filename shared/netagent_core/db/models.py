"""Database models for NetAgent platform."""

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    Float,
    DateTime,
    ForeignKey,
    LargeBinary,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from .database import Base


class AgentType(Base):
    """Configurable agent types with icons, colors, and default system prompts."""

    __tablename__ = "agent_types"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    description = Column(Text)
    system_prompt = Column(Text)  # Default system prompt for agents of this type
    icon = Column(String(50), default="bi-robot")  # Bootstrap icon class
    color = Column(String(20), default="primary")  # Bootstrap color name
    is_system = Column(Boolean, default=False)  # True for built-in types
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base):
    """User accounts (populated from AWS ALB OIDC headers)."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(255))
    oidc_sub = Column(String(255), unique=True, index=True)
    roles = Column(JSONB, default=list)
    is_admin = Column(Boolean, default=False)
    last_login = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    agents = relationship("Agent", back_populates="creator")
    workflows = relationship("Workflow", back_populates="creator")
    sessions = relationship("AgentSession", back_populates="user")


class Agent(Base):
    """AI agent configurations."""

    __tablename__ = "agents"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    agent_type = Column(String(50), nullable=False, index=True)
    system_prompt = Column(Text, nullable=False)

    # LLM Configuration
    model = Column(String(100), default="gemini-2.0-flash")
    temperature = Column(Float, default=0.1)
    max_tokens = Column(Integer, default=4096)
    max_iterations = Column(Integer, default=10)

    # Capabilities
    autonomy_level = Column(String(20), default="recommend")
    allowed_tools = Column(JSONB, default=list)
    allowed_device_patterns = Column(JSONB, default=lambda: ["*"])
    mcp_server_ids = Column(JSONB, default=list)
    knowledge_base_ids = Column(JSONB, default=list)
    api_resource_ids = Column(JSONB, default=list)  # Custom REST API endpoints
    # If set, restricts which agents this agent can hand off to (None = all enabled agents)
    allowed_handoff_agent_ids = Column(JSONB)

    # Scheduling - run agent on a schedule with a configured prompt
    schedule_cron = Column(String(100))  # Cron expression (e.g., "0 8 * * *" for daily at 8am)
    schedule_prompt = Column(Text)  # The prompt to use when triggered by schedule
    schedule_enabled = Column(Boolean, default=False)
    last_scheduled_run = Column(DateTime)  # Track last run to prevent duplicates

    # Metadata
    is_template = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Ephemeral agent tracking (for auto-generated agents in jobs)
    is_ephemeral = Column(Boolean, default=False)
    created_for_job_id = Column(Integer, ForeignKey("jobs.id"))
    created_for_task_name = Column(String(255))

    # Relationships
    creator = relationship("User", back_populates="agents")
    sessions = relationship("AgentSession", back_populates="agent")


class AgentTemplate(Base):
    """Pre-built agent templates."""

    __tablename__ = "agent_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    agent_type = Column(String(50), nullable=False)
    system_prompt = Column(Text, nullable=False)
    default_tools = Column(JSONB, default=list)
    default_model = Column(String(100), default="gemini-2.0-flash")
    icon = Column(String(50))
    is_builtin = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Workflow(Base):
    """Visual workflow definitions."""

    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)

    # Workflow definition (nodes, edges, conditions)
    definition = Column(JSONB, nullable=False)

    # Trigger configuration
    trigger_type = Column(String(20), default="manual")
    schedule_cron = Column(String(100))
    webhook_secret = Column(String(255))

    # Default output
    default_output_type = Column(String(20))
    default_output_config = Column(JSONB)

    enabled = Column(Boolean, default=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    creator = relationship("User", back_populates="workflows")
    runs = relationship("WorkflowRun", back_populates="workflow")


class WorkflowRun(Base):
    """Workflow execution instances."""

    __tablename__ = "workflow_runs"

    id = Column(Integer, primary_key=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), index=True)

    status = Column(String(20), default="pending", index=True)
    trigger_type = Column(String(20))
    trigger_data = Column(JSONB)

    current_node_id = Column(String(100))
    context = Column(JSONB, default=dict)

    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error_message = Column(Text)

    initiated_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    workflow = relationship("Workflow", back_populates="runs")
    node_executions = relationship("WorkflowNodeExecution", back_populates="workflow_run")
    sessions = relationship("AgentSession", back_populates="workflow_run")
    approvals = relationship("Approval", back_populates="workflow_run")


class WorkflowNodeExecution(Base):
    """Per-node execution tracking within a workflow run."""

    __tablename__ = "workflow_node_executions"

    id = Column(Integer, primary_key=True)
    workflow_run_id = Column(Integer, ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    node_id = Column(String(100), nullable=False)
    node_type = Column(String(50), nullable=False)

    status = Column(String(20), default="pending")
    input_data = Column(JSONB)
    output_data = Column(JSONB)

    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error_message = Column(Text)

    # Relationships
    workflow_run = relationship("WorkflowRun", back_populates="node_executions")


class AgentSession(Base):
    """Individual agent conversation sessions."""

    __tablename__ = "agent_sessions"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), index=True)
    workflow_run_id = Column(Integer, ForeignKey("workflow_runs.id"), index=True)

    # Handoff tracking - links child sessions to parent
    parent_session_id = Column(Integer, ForeignKey("agent_sessions.id"), index=True)
    handoff_context = Column(JSONB)  # Context passed during handoff

    status = Column(String(20), default="active", index=True)
    trigger_type = Column(String(20))  # user, webhook, handoff

    # Stats
    message_count = Column(Integer, default=0)
    tool_call_count = Column(Integer, default=0)
    token_count = Column(Integer, default=0)

    context = Column(JSONB, default=dict)

    user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)

    # Relationships
    agent = relationship("Agent", back_populates="sessions")
    workflow_run = relationship("WorkflowRun", back_populates="sessions")
    user = relationship("User", back_populates="sessions")
    messages = relationship("AgentMessage", back_populates="session", cascade="all, delete-orphan")
    actions = relationship("AgentAction", back_populates="session", cascade="all, delete-orphan", foreign_keys="[AgentAction.session_id]")
    approvals = relationship("Approval", back_populates="session")
    # Self-referential relationship for handoffs
    parent_session = relationship("AgentSession", remote_side=[id], backref="child_sessions")


class AgentMessage(Base):
    """Conversation messages within a session."""

    __tablename__ = "agent_messages"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True)

    role = Column(String(20), nullable=False)
    content = Column(Text)
    tool_calls = Column(JSONB)
    tool_call_id = Column(String(100))

    token_count = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    session = relationship("AgentSession", back_populates="messages")


class AgentAction(Base):
    """Detailed audit of agent reasoning and actions."""

    __tablename__ = "agent_actions"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True)

    action_type = Column(String(30), nullable=False, index=True)

    # For tool calls
    tool_name = Column(String(100))
    tool_input = Column(JSONB)
    tool_output = Column(JSONB)

    # For handoff tool calls - links to the child session
    child_session_id = Column(Integer, ForeignKey("agent_sessions.id"))

    # For thoughts/reasoning
    reasoning = Column(Text)

    # Risk assessment
    risk_level = Column(String(20))
    requires_approval = Column(Boolean, default=False)
    approval_id = Column(Integer)

    status = Column(String(20), default="completed")
    error_message = Column(Text)
    duration_ms = Column(Integer)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    session = relationship("AgentSession", back_populates="actions", foreign_keys=[session_id])
    child_session = relationship("AgentSession", foreign_keys=[child_session_id])


class KnowledgeBase(Base):
    """RAG knowledge base collections."""

    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)

    # Source configuration
    source_type = Column(String(50), nullable=False)
    source_config = Column(JSONB)

    # Sync status
    last_sync_at = Column(DateTime)
    sync_status = Column(String(20))
    document_count = Column(Integer, default=0)
    chunk_count = Column(Integer, default=0)

    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    documents = relationship("KnowledgeDocument", back_populates="knowledge_base", cascade="all, delete-orphan")


class KnowledgeDocument(Base):
    """Individual documents/pages within a knowledge base."""

    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)

    source_id = Column(String(255), index=True)
    source_url = Column(String(500))
    title = Column(String(500))
    content_hash = Column(String(64))

    last_synced_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    chunks = relationship("KnowledgeChunk", back_populates="document", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    """Vector embeddings for RAG search."""

    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)

    content = Column(Text, nullable=False)
    chunk_index = Column(Integer)

    # pgvector embedding (768 dimensions for Gemini embeddings)
    embedding = Column(Vector(768))

    chunk_metadata = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    document = relationship("KnowledgeDocument", back_populates="chunks")

    __table_args__ = (
        Index("idx_knowledge_chunks_embedding", embedding, postgresql_using="ivfflat", postgresql_with={"lists": 100}, postgresql_ops={"embedding": "vector_cosine_ops"}),
    )


class DeviceCredential(Base):
    """Encrypted device credentials."""

    __tablename__ = "device_credentials"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)

    # Matching patterns (glob patterns)
    device_patterns = Column(JSONB, nullable=False)

    # Encrypted credentials
    username_encrypted = Column(LargeBinary, nullable=False)
    password_encrypted = Column(LargeBinary, nullable=False)

    # SSH settings
    device_type = Column(String(50), default="autodetect")
    port = Column(Integer, default=22)

    priority = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)

    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class MCPServer(Base):
    """External MCP server configurations."""

    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)

    # Connection
    base_url = Column(String(500), nullable=False)
    transport = Column(String(20), default="http")

    # Authentication
    auth_type = Column(String(20))
    auth_config_encrypted = Column(LargeBinary)

    # Discovered tools (cached)
    tools = Column(JSONB, default=list)
    last_discovered_at = Column(DateTime)

    health_status = Column(String(20), default="unknown")
    enabled = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class APIResource(Base):
    """Custom REST API endpoints that agents can call as tools.

    API Resources allow users to define external REST APIs that agents can invoke,
    similar to MCP servers but with direct HTTP calls without protocol overhead.
    """

    __tablename__ = "api_resources"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)  # AI context for when/how to use this API

    # Endpoint configuration
    url = Column(String(500), nullable=False)  # Supports {param} templates
    http_method = Column(String(10), nullable=False)  # GET, POST, PUT, DELETE, PATCH

    # Authentication (encrypted)
    auth_type = Column(String(30))  # none, bearer, basic, api_key, custom_headers
    auth_config_encrypted = Column(LargeBinary)

    # Request configuration
    request_headers = Column(JSONB, default=dict)  # Static headers to include
    request_body_schema = Column(JSONB)  # JSON Schema for POST/PUT body
    query_params_schema = Column(JSONB)  # JSON Schema for query parameters
    url_params_schema = Column(JSONB)  # JSON Schema for {path} parameters

    # Response configuration
    response_format = Column(String(20), default="json")  # json, text, binary
    response_path = Column(String(255))  # JSONPath to extract specific data
    success_codes = Column(JSONB, default=lambda: [200, 201, 204])

    # Tool settings
    risk_level = Column(String(20), default="low")  # low, medium, high, critical
    requires_approval = Column(Boolean, default=False)
    timeout_seconds = Column(Integer, default=30)

    # Status tracking
    health_status = Column(String(20), default="unknown")  # unknown, healthy, unhealthy
    last_tested_at = Column(DateTime)
    enabled = Column(Boolean, default=True)

    # Ownership
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Approval(Base):
    """Pending approval requests for risky actions."""

    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True)

    # Context
    agent_action_id = Column(Integer, ForeignKey("agent_actions.id"))
    session_id = Column(Integer, ForeignKey("agent_sessions.id"), index=True)
    workflow_run_id = Column(Integer, ForeignKey("workflow_runs.id"), index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), index=True)  # For job approvals

    # What needs approval
    action_type = Column(String(50), nullable=False)
    action_description = Column(Text, nullable=False)
    action_details = Column(JSONB)
    risk_level = Column(String(20))

    # Status
    status = Column(String(20), default="pending", index=True)

    # Slack integration
    slack_message_ts = Column(String(50))
    slack_channel_id = Column(String(50))

    # Resolution
    resolved_by = Column(Integer, ForeignKey("users.id"))
    resolved_at = Column(DateTime)
    resolution_note = Column(Text)

    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    session = relationship("AgentSession", back_populates="approvals")
    workflow_run = relationship("WorkflowRun", back_populates="approvals")
    job = relationship("Job", back_populates="approvals")


class AuditLog(Base):
    """Comprehensive platform audit log."""

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)

    event_type = Column(String(50), nullable=False, index=True)
    event_category = Column(String(30), index=True)

    # Actor
    user_id = Column(Integer, ForeignKey("users.id"))
    user_email = Column(String(255))

    # Target
    resource_type = Column(String(50))
    resource_id = Column(Integer)
    resource_name = Column(String(255))

    # Details
    action = Column(String(50))
    details = Column(JSONB)

    # Request context
    ip_address = Column(String(50))
    user_agent = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("idx_audit_log_resource", "resource_type", "resource_id"),
    )


class Settings(Base):
    """Key-value settings store."""

    __tablename__ = "settings"

    key = Column(String(100), primary_key=True)
    value = Column(JSONB)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScheduledTask(Base):
    """Scheduled tasks that run agents on a cron schedule."""

    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)

    # Which agent to run
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False, index=True)

    # Schedule configuration
    schedule_cron = Column(String(100), nullable=False)  # Cron expression
    prompt = Column(Text, nullable=False)  # The prompt to send to the agent

    # Status
    enabled = Column(Boolean, default=True, index=True)
    last_run_at = Column(DateTime)
    last_run_status = Column(String(20))  # success, failed, running
    last_session_id = Column(Integer, ForeignKey("agent_sessions.id"))

    # Metadata
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    agent = relationship("Agent")
    last_session = relationship("AgentSession", foreign_keys=[last_session_id])


# =============================================================================
# JOB ORCHESTRATION MODELS
# =============================================================================


class Job(Base):
    """Orchestrated job containing multiple tasks.

    A Job represents a complex, multi-step task submitted by a user.
    Jobs can be submitted as structured markdown or natural language,
    and are executed by spawning worker agent sessions.
    """

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)

    # Job specification
    spec_raw = Column(Text, nullable=False)  # Original markdown/natural language
    spec_parsed = Column(JSONB)  # Structured JSON after parsing

    # Execution configuration
    status = Column(String(30), default="pending", index=True)
    # Status values: pending, awaiting_confirmation, queued, executing,
    #                validating, awaiting_approval, delivering, completed, failed, cancelled
    execution_mode = Column(String(20), default="batch")  # parallel, sequential, batch
    batch_size = Column(Integer, default=5)
    on_failure = Column(String(20), default="continue")  # stop, continue, retry
    retry_count = Column(Integer, default=3)  # For retry mode
    validation_mode = Column(String(20), default="ai")  # ai, human, ai+human

    # Delivery configuration
    delivery_config = Column(JSONB)  # {email: [], slack: [], s3: [], webhook: []}

    # Execution tracking
    orchestrator_session_id = Column(Integer, ForeignKey("agent_sessions.id"))
    results = Column(JSONB)  # Aggregated results from all tasks
    error_summary = Column(Text)

    # Progress tracking
    total_tasks = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
    failed_tasks = Column(Integer, default=0)

    # Ownership
    created_by = Column(Integer, ForeignKey("users.id"), index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # Relationships
    tasks = relationship("JobTask", back_populates="job", cascade="all, delete-orphan", order_by="JobTask.sequence")
    creator = relationship("User")
    orchestrator_session = relationship("AgentSession", foreign_keys=[orchestrator_session_id])
    approvals = relationship("Approval", back_populates="job")

    __table_args__ = (
        Index("idx_jobs_status_created", "status", "created_at"),
    )


class JobTask(Base):
    """Individual task within a job.

    Each JobTask represents a single step in a job workflow.
    Tasks are executed by agents (either pre-existing or ephemeral).
    """

    __tablename__ = "job_tasks"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)

    # Task definition
    sequence = Column(Integer, nullable=False)  # Execution order: 1, 2, 3...
    name = Column(String(255), nullable=False)
    description = Column(Text)
    spec = Column(JSONB, nullable=False)  # Task details from parsed spec

    # Agent assignment
    agent_id = Column(Integer, ForeignKey("agents.id"))  # Pre-existing agent
    agent_name_hint = Column(String(100))  # Hint for agent matching (e.g., "netbox-query")
    is_ephemeral_agent = Column(Boolean, default=False)
    ephemeral_agent_id = Column(Integer, ForeignKey("agents.id"))  # Auto-generated agent
    ephemeral_prompt = Column(Text)  # Generated prompt for ephemeral agent

    # Execution
    session_id = Column(Integer, ForeignKey("agent_sessions.id"))
    status = Column(String(30), default="pending", index=True)
    # Status values: pending, running, completed, failed, skipped

    # Dependencies - list of task sequences this task depends on
    depends_on = Column(JSONB, nullable=True, default=list)  # [1, 2] means depends on task 1 and 2

    # For batch tasks (e.g., "for each device")
    is_batch = Column(Boolean, default=False)
    batch_items = Column(JSONB)  # List of items to process
    batch_results = Column(JSONB)  # Results per item
    batch_source_task = Column(Integer, nullable=True)  # Sequence of task to get batch items from

    # Results
    result = Column(JSONB)
    error = Column(Text)

    # Timestamps
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # Relationships
    job = relationship("Job", back_populates="tasks")
    agent = relationship("Agent", foreign_keys=[agent_id])
    ephemeral_agent = relationship("Agent", foreign_keys=[ephemeral_agent_id])
    session = relationship("AgentSession")


# ==================== Memory Models ====================

class Memory(Base):
    """Persistent memory entries for agents.

    Memories are facts, preferences, or learnings that persist across sessions.
    They can be scoped to a user, agent, or be global.
    """

    __tablename__ = "memories"

    id = Column(Integer, primary_key=True)

    # Memory content
    content = Column(Text, nullable=False)
    memory_type = Column(String(30), nullable=False, index=True)  # preference, fact, summary, instruction

    # Scoping - at least one should be set, or none for global
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), index=True)

    # Source tracking
    source_session_id = Column(Integer, ForeignKey("agent_sessions.id"))
    source_job_id = Column(Integer, ForeignKey("jobs.id"))

    # Metadata
    category = Column(String(50), index=True)
    tags = Column(JSONB, default=list)
    confidence = Column(Float, default=1.0)

    # Vector embedding for semantic search (stored as JSONB for compatibility)
    embedding = Column(JSONB)

    # Lifecycle
    is_active = Column(Boolean, default=True, index=True)
    expires_at = Column(DateTime)
    access_count = Column(Integer, default=0)
    last_accessed_at = Column(DateTime)

    # Audit
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    agent = relationship("Agent", foreign_keys=[agent_id])
    source_session = relationship("AgentSession", foreign_keys=[source_session_id])
    source_job = relationship("Job", foreign_keys=[source_job_id])
    creator = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        Index("idx_memories_scope", "user_id", "agent_id", "is_active"),
        Index("idx_memories_category_active", "category", "is_active"),
    )


class SessionSummary(Base):
    """Condensed summary of a completed session."""

    __tablename__ = "session_summaries"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("agent_sessions.id", ondelete="CASCADE"),
                        unique=True, nullable=False)

    # Summary content
    summary = Column(Text, nullable=False)
    key_actions = Column(JSONB, default=list)
    key_findings = Column(JSONB, default=list)
    tools_used = Column(JSONB, default=list)

    # Extracted memories
    extracted_memory_ids = Column(JSONB, default=list)

    # Metadata
    message_count = Column(Integer)
    tool_call_count = Column(Integer)
    duration_seconds = Column(Integer)

    # Vector embedding
    embedding = Column(JSONB)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    session = relationship("AgentSession", backref="summary")
