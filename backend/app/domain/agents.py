from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings

AgentType = Literal["research", "coding", "data_analysis", "planning", "security", "orchestrator", "custom"]
AgentStatus = Literal["draft", "active", "inactive", "archived"]

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active", "archived"},
    "active": {"inactive", "archived"},
    "inactive": {"active", "archived"},
    "archived": set(),
}


class FallbackRoute(BaseModel):
    provider: str
    model: str


class MemoryConfig(BaseModel):
    window_messages: int = Field(12, ge=2, le=100)
    max_context_tokens: int = Field(6000, ge=500, le=200_000)


class RetryConfig(BaseModel):
    max_attempts: int = Field(2, ge=1, le=5)


class AgentPolicies(BaseModel):
    require_citations: bool = True
    evaluate_output: bool = True
    allow_delegation: bool = False
    stop_on_policy_denial: bool = False


class RagConfig(BaseModel):
    collection: str | None = None
    top_k: int = Field(4, ge=1, le=10)


class AgentConfig(BaseModel):
    system_prompt: str = Field("You are a helpful enterprise assistant.", max_length=20_000)
    provider: str = "mock"
    model: str = "mock-reasoner-1"
    temperature: float = Field(0.2, ge=0, le=2)
    max_tokens: int = Field(800, ge=16, le=16_000)
    timeout_s: float = Field(45, ge=1, le=300)
    total_timeout_s: float = Field(120, ge=5, le=900)
    max_steps: int = Field(6, ge=1)
    tools: list[str] = Field(default_factory=list)
    mcp_server_ids: list[str] = Field(default_factory=list)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    fallbacks: list[FallbackRoute] = Field(default_factory=list)
    collaborators: list[str] = Field(default_factory=list)
    policies: AgentPolicies = Field(default_factory=AgentPolicies)
    rag: RagConfig = Field(default_factory=RagConfig)

    @field_validator("max_steps")
    @classmethod
    def _bounded(cls, v: int) -> int:
        hard = get_settings().max_agent_steps_hard_limit
        if v > hard:
            raise ValueError(f"max_steps cannot exceed platform hard limit {hard}")
        return v


class AgentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    agent_type: AgentType
    description: str = Field("", max_length=2000)
    team_id: str | None = None
    config: AgentConfig = Field(default_factory=AgentConfig)


class AgentUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    description: str | None = Field(None, max_length=2000)
    config: AgentConfig | None = None
    change_note: str = Field("", max_length=500)


class AgentExecuteRequest(BaseModel):
    input: str = Field(min_length=1, max_length=20_000)
    mode: Literal["sync", "async"] = "sync"
    version: int | None = None
