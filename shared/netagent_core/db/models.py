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

    # Metadata
    is_template = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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

    status = Column(String(20), default="active", index=True)
    trigger_type = Column(String(20))

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
    actions = relationship("AgentAction", back_populates="session", cascade="all, delete-orphan")
    approvals = relationship("Approval", back_populates="session")


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
    session = relationship("AgentSession", back_populates="actions")


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

    metadata = Column(JSONB)
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


class Approval(Base):
    """Pending approval requests for risky actions."""

    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True)

    # Context
    agent_action_id = Column(Integer, ForeignKey("agent_actions.id"))
    session_id = Column(Integer, ForeignKey("agent_sessions.id"), index=True)
    workflow_run_id = Column(Integer, ForeignKey("workflow_runs.id"), index=True)

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
