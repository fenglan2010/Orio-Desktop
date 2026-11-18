"""ORM models. SQLite today, shaped so it can move to PostgreSQL later."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import Base


def _utcnow() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="revenue_manager")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)



class HotelMembership(Base):
    """A user may later belong to more than one hotel; role is per membership."""

    __tablename__ = "hotel_memberships"
    __table_args__ = (UniqueConstraint("user_id", "hotel_id", name="uq_membership_user_hotel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="analyst")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Hotel(Base):
    __tablename__ = "hotels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str] = mapped_column(String(64), default="杭州")
    address: Mapped[str] = mapped_column(String(256), default="")
    positioning: Mapped[str] = mapped_column(String(128), default="")
    tenant_key: Mapped[str] = mapped_column(String(64), default="demo")
    platform: Mapped[str] = mapped_column(String(32), default="ctrip")

    room_types: Mapped[list[RoomType]] = relationship(back_populates="hotel")


class RoomType(Base):
    __tablename__ = "room_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    inventory: Mapped[int] = mapped_column(Integer, default=10)
    capacity: Mapped[int] = mapped_column(Integer, default=2)
    base_price: Mapped[float] = mapped_column(Float, nullable=False)
    min_price: Mapped[float] = mapped_column(Float, nullable=False)
    max_price: Mapped[float] = mapped_column(Float, nullable=False)

    hotel: Mapped[Hotel] = relationship(back_populates="room_types")


class DailyMetric(Base):
    __tablename__ = "daily_metrics"
    __table_args__ = (
        UniqueConstraint("hotel_id", "room_type_id", "date", "platform", name="uq_metric_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    room_type_id: Mapped[int] = mapped_column(ForeignKey("room_types.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), default="ctrip")
    is_future: Mapped[bool] = mapped_column(Boolean, default=False)
    available_rooms: Mapped[int] = mapped_column(Integer, default=0)
    sold_rooms: Mapped[int] = mapped_column(Integer, default=0)
    occupancy: Mapped[float] = mapped_column(Float, default=0.0)
    list_price: Mapped[float] = mapped_column(Float, default=0.0)
    adr: Mapped[float] = mapped_column(Float, default=0.0)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    search_rank: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    bookings: Mapped[int] = mapped_column(Integer, default=0)
    conversion_rate: Mapped[float] = mapped_column(Float, default=0.0)


class CompetitorSnapshot(Base):
    __tablename__ = "competitor_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "hotel_id", "room_type_id", "date", "competitor_name", name="uq_comp_day"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    room_type_id: Mapped[int] = mapped_column(ForeignKey("room_types.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    competitor_name: Mapped[str] = mapped_column(String(128), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("external_id", name="uq_review_ext"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), default="ctrip")
    guest_name: Mapped[str] = mapped_column(String(64), default="匿名")
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(128), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    review_date: Mapped[date] = mapped_column(Date, nullable=False)
    sentiment: Mapped[str] = mapped_column(String(16), default="neutral")
    reply_draft: Mapped[str] = mapped_column(Text, default="")
    reply_status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending | drafted | pending_review | approved | rejected | executed | rolled_back
    workflow_run_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_runs.id"), nullable=True)
    executed_reply: Mapped[str] = mapped_column(Text, default="")
    review_comment: Mapped[str] = mapped_column(Text, default="")


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("hotel_id", "stable_key", name="uq_alert_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    metric_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    diagnosis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running")
    current_node: Mapped[str] = mapped_column(String(32), default="collect")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    related_strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    related_review_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="admin")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    node_logs: Mapped[list[WorkflowNodeLog]] = relationship(back_populates="run")


class WorkflowNodeLog(Base):
    __tablename__ = "workflow_node_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    node_name: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    run: Mapped[WorkflowRun] = relationship(back_populates="node_logs")


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    workflow_run_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_runs.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="pricing")  # pricing | campaign
    room_type_id: Mapped[int | None] = mapped_column(ForeignKey("room_types.id"), nullable=True)
    date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    expected_impact: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    constraints: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    daily_breakdown: Mapped[list | None] = mapped_column(JSON, nullable=True)
    campaign_advice: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_review")
    reviewer: Mapped[str] = mapped_column(String(64), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_comment: Mapped[str] = mapped_column(Text, default="")
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    execution_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rollback_reason: Mapped[str] = mapped_column(Text, default="")
    previous_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="medium")
    checkpoint_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int | None] = mapped_column(ForeignKey("hotels.id"), nullable=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ModelCallLog(Base):
    __tablename__ = "model_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), default="")
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    hotel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tenant_key: Mapped[str] = mapped_column(String(64), default="demo")
    outbound: Mapped[bool] = mapped_column(Boolean, default=False)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class DecisionCheckpoint(Base):
    """Persisted OIROState snapshots so a decision can be replayed or rolled back."""

    __tablename__ = "decision_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    node_name: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="node")  # node | pre_execution | rollback_restore
    state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class MemoryRecord(Base):
    """In-hotel decision episode memory. Strictly per hotel_id; identifiable data never crosses hotels."""

    __tablename__ = "memory_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="decision_episode")
    run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("workflow_runs.id"), nullable=True, index=True)
    strategy_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("strategies.id"), nullable=True, index=True)
    checkpoint_id: Mapped[str] = mapped_column(String(64), default="")
    approval_status: Mapped[str] = mapped_column(String(32), default="")
    action_kind: Mapped[str] = mapped_column(String(32), default="")  # pricing | campaign | review_reply
    risk_level: Mapped[str] = mapped_column(String(16), default="")
    episode_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class WriteJob(Base):
    """Durable official-write ledger. Receipts live here, not only on Strategy.execution_payload."""

    __tablename__ = "write_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id"), nullable=True)
    workflow_run_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_runs.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # pricing | campaign | review_reply
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), default="ctrip")
    write_mode: Mapped[str] = mapped_column(String(16), default="sandbox")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    checkpoint_id: Mapped[str] = mapped_column(String(64), default="")
    intended_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    receipt_id: Mapped[str] = mapped_column(String(64), default="")
    line_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str] = mapped_column(Text, default="")
    refuse_reason: Mapped[str] = mapped_column(String(64), default="")
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reconcile_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

class ChannelConnection(Base):
    """Durable OTA channel authorization for a hotel. Secrets stay encrypted at rest."""

    __tablename__ = "channel_connections"
    __table_args__ = (
        UniqueConstraint("hotel_id", "platform", name="uq_channel_hotel_platform"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)  # ctrip | meituan | fliggy
    status: Mapped[str] = mapped_column(String(32), default="disconnected")
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_verified: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    platform_hotel_id: Mapped[str] = mapped_column(String(128), default="")
    platform_account_id: Mapped[str] = mapped_column(String(128), default="")
    credentials_encrypted: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class JobRun(Base):
    """Per-hotel background job ledger for ingest, write retry, and reconcile."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int | None] = mapped_column(ForeignKey("hotels.id"), nullable=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)  # ingest | write_retry | write_reconcile
    status: Mapped[str] = mapped_column(String(32), default="running")  # running | succeeded | failed | skipped
    actor: Mapped[str] = mapped_column(String(64), default="system:scheduler")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

class IndustryPattern(Base):
    """Anonymized cross-hotel decision pattern. This row stores no hotel identity."""

    __tablename__ = "industry_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    action_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    signal_types: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="")
    terminal_status: Mapped[str] = mapped_column(String(32), nullable=False)
    price_direction: Mapped[str] = mapped_column(String(16), default="")
    episode_count: Mapped[int] = mapped_column(Integer, default=0)
    last_contributed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class IndustryPatternContribution(Base):
    """Private contribution map for k-anonymity and self-exclusion. Never returned by hotel-facing APIs."""

    __tablename__ = "industry_pattern_contributions"
    __table_args__ = (
        UniqueConstraint("pattern_id", "episode_key", name="uq_pattern_episode_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern_id: Mapped[int] = mapped_column(ForeignKey("industry_patterns.id"), nullable=False, index=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False, index=True)
    memory_record_id: Mapped[int | None] = mapped_column(ForeignKey("memory_records.id"), nullable=True)
    episode_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class GraphNode(Base):
    """Hotel-scoped knowledge-graph node. Never stores another hotel's identity."""

    __tablename__ = "graph_nodes"
    __table_args__ = (UniqueConstraint("hotel_id", "node_key", name="uq_graph_node_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False, index=True)
    node_key: Mapped[str] = mapped_column(String(160), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(256), default="")
    attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class GraphEdge(Base):
    """Hotel-scoped knowledge-graph edge. Keys refer only to this hotel's nodes."""

    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint("hotel_id", "from_key", "to_key", "edge_type", name="uq_graph_edge"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False, index=True)
    from_key: Mapped[str] = mapped_column(String(160), nullable=False)
    to_key: Mapped[str] = mapped_column(String(160), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(32), nullable=False)
    attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class RLPolicyValue(Base):
    """Offline tabular action values fitted on one hotel's measured episodes."""

    __tablename__ = "rl_policy_values"
    __table_args__ = (
        UniqueConstraint("hotel_id", "context_key", "action_bucket", name="uq_rl_policy_value"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False, index=True)
    context_key: Mapped[str] = mapped_column(String(160), nullable=False)
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    action_bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    episode_count: Mapped[int] = mapped_column(Integer, default=0)
    mean_reward: Mapped[float] = mapped_column(Float, default=0.0)
    reward_unit: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class EvolutionEngineState(Base):
    """Per-hotel evolution engine switch. Default is off when no row exists."""

    __tablename__ = "evolution_engine_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sandbox_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class EvolutionCandidate(Base):
    """In-hotel candidate strategy proposed by the offline learner. Never auto-executed."""

    __tablename__ = "evolution_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False, index=True)
    action_bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    action_label: Mapped[str] = mapped_column(String(64), default="")
    context_key: Mapped[str] = mapped_column(String(160), default="")
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    episode_count: Mapped[int] = mapped_column(Integer, default=0)
    mean_reward: Mapped[float] = mapped_column(Float, default=0.0)
    reward_unit: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default="proposed")
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    sandbox_evaluation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class PmsConnection(Base):
    """Per-hotel official PMS/CRS authorization. Secrets stay encrypted at rest."""

    __tablename__ = "pms_connections"
    __table_args__ = (
        UniqueConstraint("hotel_id", "kind", name="uq_pms_hotel_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # pms | crs
    status: Mapped[str] = mapped_column(String(32), default="disconnected")
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_verified: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    credentials_encrypted: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class PmsStagingRecord(Base):
    """Thin staging for official PMS/CRS pulls. Does not replace demo OTA metrics."""

    __tablename__ = "pms_staging_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # pms | crs
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    family: Mapped[str] = mapped_column(String(32), default="")  # inventory | room_types | reservations
    status: Mapped[str] = mapped_column(String(32), default="staged")  # staged | mapped | failed
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    mapped_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    mapping_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

class HotelOnboardingAgreement(Base):
    """One current commercial onboarding record per hotel (or prospective tenant)."""

    __tablename__ = "hotel_onboarding_agreements"
    __table_args__ = (UniqueConstraint("hotel_id", name="uq_onboarding_hotel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="initiated")
    vendor_state: Mapped[str] = mapped_column(String(32), default="not_configured")
    approval_level: Mapped[int] = mapped_column(Integer, default=0)
    approval_required: Mapped[int] = mapped_column(Integer, default=2)
    initiated_by: Mapped[str] = mapped_column(String(64), default="")
    initiated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    signing_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_for_approval_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_approved_by: Mapped[str] = mapped_column(String(64), default="")
    last_approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    contract_effective_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payment_confirmed_by: Mapped[str] = mapped_column(String(64), default="")
    payment_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    product_enabled_by: Mapped[str] = mapped_column(String(64), default="")
    product_enabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

