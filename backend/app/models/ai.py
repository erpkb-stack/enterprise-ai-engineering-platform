"""Agents, model providers, MCP servers, tools and their executions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, JSONType, TenantMixin, TimestampMixin, UpdatedMixin


class ModelProvider(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "model_providers"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(
        String(40)
    )  # mock|ollama|openai|anthropic|gemini|nvidia_nim|openai_compatible
    base_url: Mapped[str | None] = mapped_column(String(500))
    credential_encrypted: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)


class ModelConfiguration(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "model_configurations"
    __table_args__ = (UniqueConstraint("provider_id", "model_name"),)
    provider_id: Mapped[str] = mapped_column(String(36), ForeignKey("model_providers.id", ondelete="CASCADE"))
    model_name: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    context_window: Mapped[int] = mapped_column(Integer, default=8192)
    input_cost_per_1k: Mapped[float] = mapped_column(Float, default=0.0)
    output_cost_per_1k: Mapped[float] = mapped_column(Float, default=0.0)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)

    provider: Mapped[ModelProvider] = relationship(lazy="raise", foreign_keys=[provider_id])


class Agent(IdMixin, TenantMixin, TimestampMixin, UpdatedMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)
    team_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("teams.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    agent_type: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class AgentVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSONType)
    change_note: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[str | None] = mapped_column(String(36))

    agent: Mapped[Agent] = relationship(lazy="raise", foreign_keys=[agent_id])


class AgentExecution(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "agent_executions"
    __table_args__ = (Index("ix_agent_exec_org_created", "organization_id", "created_at"),)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    agent_version: Mapped[int] = mapped_column(Integer)
    parent_execution_id: Mapped[str | None] = mapped_column(String(36))
    user_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    input: Mapped[str] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[list] = mapped_column(JSONType, default=list)
    citations: Mapped[list] = mapped_column(JSONType, default=list)
    evaluation: Mapped[dict] = mapped_column(JSONType, default=dict)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(200))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agent: Mapped[Agent] = relationship(lazy="raise", foreign_keys=[agent_id])


class McpServer(IdMixin, TenantMixin, TimestampMixin, UpdatedMixin, Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    transport: Mapped[str] = mapped_column(String(30))  # stdio | streamable_http | builtin
    command: Mapped[str | None] = mapped_column(String(500))
    args: Mapped[list] = mapped_column(JSONType, default=list)
    url: Mapped[str | None] = mapped_column(String(500))
    auth_type: Mapped[str] = mapped_column(String(30), default="none")  # none|api_key|oauth2|service
    credential_encrypted: Mapped[str | None] = mapped_column(Text)
    timeout_s: Mapped[float] = mapped_column(Float, default=20.0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(20), default="disconnected")
    last_health: Mapped[dict] = mapped_column(JSONType, default=dict)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    config_history: Mapped[list] = mapped_column(JSONType, default=list)
    allowed_roles: Mapped[list] = mapped_column(JSONType, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class McpTool(IdMixin, TenantMixin, Base):
    __tablename__ = "mcp_tools"
    __table_args__ = (UniqueConstraint("server_id", "name"),)
    server_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    input_schema: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # deny by default; must be allowlisted
    risk_level: Mapped[str] = mapped_column(String(10), default="medium")
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    total_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)

    server: Mapped[McpServer] = relationship(lazy="raise", foreign_keys=[server_id])


class ToolExecution(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "tool_executions"
    __table_args__ = (Index("ix_tool_exec_org_created", "organization_id", "created_at"),)
    tool_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("mcp_tools.id", ondelete="SET NULL"))
    server_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("mcp_servers.id", ondelete="SET NULL")
    )
    agent_execution_id: Mapped[str | None] = mapped_column(String(36), index=True)
    workflow_execution_id: Mapped[str | None] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36))
    tool_name: Mapped[str] = mapped_column(String(200))
    arguments: Mapped[dict] = mapped_column(JSONType, default=dict)
    result_preview: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))  # success | error | denied
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
