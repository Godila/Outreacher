from sqlalchemy import select

from app.models import B24User, Contact
from app.sync import SyncStats, _aggregate_deals, _pick_email, sync_all


class FakeClient:
    def __init__(self, contacts_response=None, deals_response=None, users_response=None):
        self._contacts = contacts_response or []
        self._deals = deals_response or []
        self._users = users_response or {}
        self.deal_params: dict | None = None

    async def call_all(self, method: str, params: dict, key: str = "result") -> list:
        if method == "crm.contact.list":
            return self._contacts
        if method == "crm.item.list":
            self.deal_params = params
            return self._deals
        raise AssertionError(f"unexpected method {method}")

    async def batch(self, cmds):
        return [self._users.get(params["ID"], []) for _method, params in cmds]


def test_pick_email_prefers_work():
    emails = [
        {"VALUE": "home@x.ru", "VALUE_TYPE": "HOME"},
        {"VALUE": "work@x.ru", "VALUE_TYPE": "WORK"},
    ]
    assert _pick_email(emails) == "work@x.ru"
    assert _pick_email([{"VALUE": "only@x.ru", "VALUE_TYPE": "OTHER"}]) == "only@x.ru"
    assert _pick_email([]) == ""


def test_aggregate_deals_rfm():
    rows = [
        {"id": "1", "title": "Старая", "opportunity": "1000.00", "closed": True,
         "closedDate": "2025-01-10T00:00:00+03:00", "contacts": [11]},
        {"id": "2", "title": "Новая", "opportunity": "5000.00", "closed": True,
         "closedDate": "2026-02-01T00:00:00+03:00", "contactId": 11},
        {"id": "3", "title": "В работе", "opportunity": "3000.00", "closed": False,
         "closedDate": None, "contacts": [22]},
    ]
    agg = _aggregate_deals(rows)
    assert agg[11]["closed_count"] == 2
    assert agg[11]["active"] is False
    assert agg[11]["title"] == "Новая"
    assert agg[11]["amount"] == 5000.0
    assert agg[22]["active"] is True
    assert agg[22]["closed_count"] == 0


async def test_sync_all(db_session):
    client = FakeClient(
        contacts_response=[
            {"ID": "11", "NAME": "Иван", "LAST_NAME": "Иванов", "ASSIGNED_BY_ID": "5",
             "SOURCE_ID": "STORE", "DATE_CREATE": "2024-05-01T00:00:00+03:00",
             "EMAIL": [{"VALUE": "ivan@x.ru", "VALUE_TYPE": "WORK"}]},
            {"ID": "22", "NAME": "Пётр", "LAST_NAME": "Петров", "ASSIGNED_BY_ID": "6",
             "EMAIL": [{"VALUE": "petr@home.ru", "VALUE_TYPE": "HOME"}]},
            {"ID": "33", "NAME": "Без", "LAST_NAME": "Почты", "EMAIL": []},
        ],
        deals_response=[
            {"id": "1", "title": "Сделка А", "opportunity": "1500", "closed": True,
             "closedDate": "2026-01-15T00:00:00+03:00", "contacts": [11]},
            {"id": "2", "title": "Сделка Б", "opportunity": "900", "closed": True,
             "closedDate": "2026-03-01T00:00:00+03:00", "contacts": [22]},
            {"id": "3", "title": "Открытая", "opportunity": "100", "closed": False,
             "contacts": [22]},
        ],
        users_response={
            5: [{"ID": "5", "NAME": "Анна", "LAST_NAME": "Смирнова", "EMAIL": "anna@company.ru"}],
            6: [{"ID": "6", "NAME": "Олег", "LAST_NAME": "Козлов", "EMAIL": "oleg@company.ru"}],
        },
    )
    stats = await sync_all(client, db_session)
    assert isinstance(stats, SyncStats)
    # контакт без email пропущен
    contacts = (await db_session.execute(select(Contact))).scalars().all()
    assert {c.b24_contact_id for c in contacts} == {11, 22}
    ivan = next(c for c in contacts if c.b24_contact_id == 11)
    petr = next(c for c in contacts if c.b24_contact_id == 22)
    assert ivan.email == "ivan@x.ru"
    assert ivan.deals_count == 1 and ivan.has_active_deal is False
    assert ivan.last_deal_title == "Сделка А"
    assert petr.email == "petr@home.ru"  # единственный email, хоть и HOME
    assert petr.has_active_deal is True and petr.deals_count == 1
    assert client.deal_params["entityTypeId"] == 2
    users = (await db_session.execute(select(B24User))).scalars().all()
    assert {u.name for u in users} == {"Анна Смирнова", "Олег Козлов"}
    assert ivan.consent_source == "STORE" and ivan.consent_at is not None
