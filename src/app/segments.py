import datetime as dt

from sqlalchemy import and_, exists, select

from app.models import Campaign, Contact, Enrollment, Suppression, utcnow

# Полный cooldown между кампаниями — виток 2 (спека §10); в MVP контакт исключается,
# пока числится в любой незавершённой кампании.

ACTIVE_ENROLLMENT_STATUSES = ("pending", "active", "paused_ooo")


def segment_conditions(filters: dict) -> list:
    """Условия SQLAlchemy из JSON-фильтров сегмента."""
    conds = []
    days_gte = filters.get("last_deal_days_gte")
    if days_gte:
        # «спящие N+ дней»: последняя сделка старше cutoff
        conds.append(Contact.last_deal_at <= utcnow() - dt.timedelta(days=int(days_gte)))
    days_lte = filters.get("last_deal_days_lte")
    if days_lte:
        conds.append(Contact.last_deal_at >= utcnow() - dt.timedelta(days=int(days_lte)))
    if filters.get("deals_count_gte"):
        conds.append(Contact.deals_count >= int(filters["deals_count_gte"]))
    if filters.get("last_deal_amount_gte"):
        conds.append(Contact.last_deal_amount >= float(filters["last_deal_amount_gte"]))
    return conds


async def preview(session, filters: dict) -> list[Contact]:
    """Контакты сегмента минус стоп-лист (для показа размера оператору)."""
    conds = segment_conditions(filters)
    conds.append(~exists(select(Suppression).where(Suppression.email == Contact.email)))
    return (await session.execute(select(Contact).where(and_(*conds)))).scalars().all()


async def enroll(session, campaign_id: int, filters: dict) -> int:
    """Создаёт enrollment'ы: сегмент MINUS стоп-лист MINUS активные кампании MINUS активные сделки."""
    conds = segment_conditions(filters)
    conds.append(~exists(select(Suppression).where(Suppression.email == Contact.email)))
    conds.append(Contact.has_active_deal.is_(False))
    conds.append(
        ~exists(
            select(Enrollment)
            .where(
                Enrollment.contact_id == Contact.id,
                Enrollment.campaign_id != campaign_id,
                Enrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
            )
        )
    )
    rows = (await session.execute(select(Contact).where(and_(*conds)))).scalars().all()
    for contact in rows:
        existing = (
            await session.execute(
                select(Enrollment).where(
                    Enrollment.campaign_id == campaign_id, Enrollment.contact_id == contact.id
                )
            )
        ).scalars().first()
        if existing is None:
            session.add(
                Enrollment(
                    campaign_id=campaign_id,
                    contact_id=contact.id,
                    status="pending",
                    next_send_at=utcnow(),
                )
            )
    await session.commit()
    return len(rows)
