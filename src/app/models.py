import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    b24_contact_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    company: Mapped[str] = mapped_column(String(255), default="")
    assigned_b24_user_id: Mapped[int] = mapped_column(Integer, default=0)
    has_active_deal: Mapped[bool] = mapped_column(Boolean, default=False)
    last_deal_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_deal_amount: Mapped[float] = mapped_column(default=0.0)
    last_deal_title: Mapped[str] = mapped_column(String(255), default="")
    deals_count: Mapped[int] = mapped_column(Integer, default=0)
    consent_source: Mapped[str] = mapped_column(String(255), default="")
    consent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    filters: Mapped[dict] = mapped_column(JSON, default=dict)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(32), default="reactivation")  # reactivation/announce/upsell/content
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/active/paused/done
    daily_cap: Mapped[int] = mapped_column(Integer, default=100)
    is_advertising: Mapped[bool] = mapped_column(Boolean, default=False)
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("segments.id"), nullable=True)


class SequenceStep(Base):
    __tablename__ = "sequence_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # 1-based
    delay_days: Mapped[int] = mapped_column(Integer, default=0)  # рабочих дней после отправки предыдущего
    subject_tpl: Mapped[str] = mapped_column(Text)
    body_tpl: Mapped[str] = mapped_column(Text)
    threaded: Mapped[bool] = mapped_column(Boolean, default=True)


class Sender(Base):
    __tablename__ = "senders"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    smtp_host: Mapped[str] = mapped_column(String(255))
    smtp_port: Mapped[int] = mapped_column(Integer, default=465)
    smtp_user: Mapped[str] = mapped_column(String(255))
    smtp_pass: Mapped[str] = mapped_column(String(255))
    imap_host: Mapped[str] = mapped_column(String(255))
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    imap_user: Mapped[str] = mapped_column(String(255))
    imap_pass: Mapped[str] = mapped_column(String(255))
    daily_limit: Mapped[int] = mapped_column(Integer, default=50)
    sent_today: Mapped[int] = mapped_column(Integer, default=0)
    sent_date: Mapped[str] = mapped_column(String(10), default="")
    health: Mapped[str] = mapped_column(String(16), default="ok")  # ok/paused


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("campaign_id", "contact_id", name="uq_enrollment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending/active/replied/unsubscribed/bounced/paused_ooo/done/excluded
    current_step: Mapped[int] = mapped_column(Integer, default=0)  # последний отправленный
    next_send_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    ooo_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    paused_reason: Mapped[str] = mapped_column(String(255), default="")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id"), index=True)
    step_pos: Mapped[int] = mapped_column(Integer)
    message_id: Mapped[str] = mapped_column(String(255), unique=True)  # "<hex@outreacher>"
    sender_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued/sent/bounced/failed
    smtp_error: Mapped[str] = mapped_column(Text, default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    b24_activity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Incoming(Base):
    __tablename__ = "incoming"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(255), index=True)
    in_reply_to: Mapped[str] = mapped_column(String(255), default="", index=True)
    from_email: Mapped[str] = mapped_column(String(320), default="")
    classification: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    # positive/negative/ooo/unsub/referral/unknown
    body_snippet: Mapped[str] = mapped_column(Text, default="")
    classified_by: Mapped[str] = mapped_column(String(16), default="rule")  # rule/manual/llm
    received_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    b24_comment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_ndr: Mapped[bool] = mapped_column(Boolean, default=False)


class Suppression(Base):
    __tablename__ = "suppression"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(32))  # unsub/fbl/hard_bounce/manual
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class B24State(Base):
    __tablename__ = "b24_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    portal_domain: Mapped[str] = mapped_column(String(255), unique=True)
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    member_id: Mapped[str] = mapped_column(String(64), default="")
    application_token: Mapped[str] = mapped_column(Text, default="")
    contacts_watermark: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    deals_watermark: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class B24User(Base):
    """Кэш пользователей портала (id -> имя) для шаблонов и дел."""

    __tablename__ = "b24_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # = B24 user id
    name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(320), default="")


class ProcessedEvent(Base):
    """Дедупликация вебхуков Б24."""

    __tablename__ = "processed_events"
    __table_args__ = (UniqueConstraint("event_id", name="uq_processed_event"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128))
    received_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class B24Job(Base):
    """Outbox задач записи в Б24 (best-effort с ретраями)."""

    __tablename__ = "b24_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))  # log_activity/log_reply/create_task/mark_unsubscribed
    payload: Mapped[dict] = mapped_column(JSON)
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeat"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # всегда 1
    component: Mapped[str] = mapped_column(String(32))  # scheduler/sender/listener/sync
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


# --- Закладные фазы cold (создаются, в MVP не используются) ---

class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    dns_status: Mapped[str] = mapped_column(String(16), default="unknown")
    warmup_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class WarmupTask(Base):
    __tablename__ = "warmup_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("senders.id"))
    scheduled_at: Mapped[dt.datetime] = mapped_column(DateTime)
    done: Mapped[bool] = mapped_column(Boolean, default=False)


class VerificationRun(Base):
    __tablename__ = "verification_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    verdict: Mapped[str] = mapped_column(String(16))  # valid/invalid/catch_all/unknown
    ran_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    source: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
