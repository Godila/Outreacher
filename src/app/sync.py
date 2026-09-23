import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select

from app.b24.client import Bitrix24Client
from app.models import B24User, Contact, utcnow


@dataclass
class SyncStats:
    contacts: int = 0
    deals: int = 0
    users: int = 0


def _pick_email(emails: list | None) -> str:
    """WORK-тип приоритетен, иначе первый непустой."""
    for entry in emails or []:
        if entry.get("VALUE_TYPE") == "WORK" and entry.get("VALUE"):
            return entry["VALUE"]
    for entry in emails or []:
        if entry.get("VALUE"):
            return entry["VALUE"]
    return ""


def _parse_dt(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _as_bool(value) -> bool:
    return value in (True, 1, "1", "Y", "y")


async def sync_contacts(client: Bitrix24Client, session) -> int:
    rows = await client.call_all(
        "crm.contact.list",
        {
            "select": ["ID", "NAME", "LAST_NAME", "EMAIL", "ASSIGNED_BY_ID", "DATE_CREATE", "SOURCE_ID"],
            "order": {"ID": "ASC"},
        },
    )
    for row in rows:
        email = _pick_email(row.get("EMAIL"))
        if not email:
            continue  # без email контакт для аутрича бесполезен
        b24_id = int(row["ID"])
        contact = (
            await session.execute(select(Contact).where(Contact.b24_contact_id == b24_id))
        ).scalars().first()
        if contact is None:
            contact = Contact(b24_contact_id=b24_id, email=email.lower())
            session.add(contact)
        contact.email = email.lower()
        contact.name = f"{row.get('NAME', '')} {row.get('LAST_NAME', '')}".strip()
        contact.assigned_b24_user_id = int(row.get("ASSIGNED_BY_ID") or 0)
        contact.consent_source = row.get("SOURCE_ID") or ""
        consent_dt = _parse_dt(row.get("DATE_CREATE"))
        if consent_dt and not contact.consent_at:
            contact.consent_at = consent_dt
        contact.synced_at = utcnow()
    await session.commit()
    return len(rows)


def _aggregate_deals(rows: list[dict]) -> dict[int, dict]:
    """RFM-агрегат по contactId: последняя закрытая сделка, их число, флаг активной."""
    agg: dict[int, dict] = {}
    for deal in rows:
        contact_ids = deal.get("contacts") or []
        if not contact_ids and deal.get("contactId"):
            contact_ids = [deal["contactId"]]
        for cid in contact_ids:
            if not cid:
                continue
            record = agg.setdefault(
                int(cid),
                {"last": None, "closed_count": 0, "active": False, "amount": 0.0, "title": ""},
            )
            closed = _as_bool(deal.get("closed"))
            if closed:
                record["closed_count"] += 1
            else:
                record["active"] = True
            when = _parse_dt(deal.get("closedDate") or deal.get("dateModify"))
            if when and (record["last"] is None or when > record["last"]):
                record["last"] = when
                record["amount"] = float(deal.get("opportunity") or 0)
                record["title"] = deal.get("title") or ""
    return agg


async def sync_deals(client: Bitrix24Client, session) -> int:
    rows = await client.call_all(
        "crm.item.list",
        {
            "entityTypeId": 2,
            "select": ["id", "title", "opportunity", "closed", "closedDate", "contactId", "contacts", "dateModify"],
        },
    )
    for cid, agg in _aggregate_deals(rows).items():
        contact = (
            await session.execute(select(Contact).where(Contact.b24_contact_id == cid))
        ).scalars().first()
        if contact is None:
            continue
        contact.last_deal_at = agg["last"]
        contact.deals_count = agg["closed_count"]
        contact.has_active_deal = agg["active"]
        contact.last_deal_amount = agg["amount"]
        contact.last_deal_title = agg["title"]
    await session.commit()
    return len(rows)


async def sync_users(client: Bitrix24Client, session) -> int:
    ids = sorted(
        {
            contact.assigned_b24_user_id
            for contact in (await session.execute(select(Contact))).scalars()
            if contact.assigned_b24_user_id
        }
    )
    if not ids:
        return 0
    results = await client.batch([("user.get", {"ID": uid}) for uid in ids])
    count = 0
    for user_list in results:
        for user in user_list or []:
            uid = int(user.get("ID"))
            cached = await session.get(B24User, uid)
            if cached is None:
                cached = B24User(id=uid)
                session.add(cached)
            cached.name = f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip()
            cached.email = user.get("EMAIL") or ""
            count += 1
    await session.commit()
    return count


async def sync_all(client: Bitrix24Client, session) -> SyncStats:
    stats = SyncStats()
    stats.contacts = await sync_contacts(client, session)
    stats.deals = await sync_deals(client, session)
    stats.users = await sync_users(client, session)
    return stats
