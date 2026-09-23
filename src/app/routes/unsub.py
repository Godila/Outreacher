from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select, update

from app.engine.unsub import parse_token
from app.models import B24Job, Contact, Enrollment, Suppression, utcnow

router = APIRouter()

UNSUB_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Отписка</title></head>
<body style="font-family:sans-serif;text-align:center;padding-top:4rem">
<h2>Вы отписаны от рассылок</h2>
<p>Больше мы не будем писать вам на этот адрес.</p>
</body></html>"""


async def _apply_unsub(request: Request, token: str) -> bool:
    """Идемпотентная отписка: suppression навсегда + стоп enrollment'ов + задача в Б24."""
    settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        eid = None
        try:
            eid = int(token.partition(".")[0])
        except ValueError:
            return False
        enrollment = await session.get(Enrollment, eid)
        if enrollment is None:
            return False
        contact = await session.get(Contact, enrollment.contact_id)
        if contact is None or parse_token(token, contact.email, settings.app_secret) is None:
            return False

        existing = (
            await session.execute(select(Suppression).where(Suppression.email == contact.email))
        ).scalars().first()
        if existing is None:
            session.add(Suppression(email=contact.email, reason="unsub", created_at=utcnow()))
            session.add(
                B24Job(
                    kind="mark_unsubscribed",
                    payload={"b24_contact_id": contact.b24_contact_id, "email": contact.email},
                )
            )
        await session.execute(
            update(Enrollment)
            .where(
                Enrollment.contact_id == contact.id,
                Enrollment.status.in_(("pending", "active", "paused_ooo")),
            )
            .values(status="unsubscribed")
        )
        await session.commit()
        return True


@router.get("/unsub/{token}", response_class=HTMLResponse)
async def unsub_get(request: Request, token: str):
    ok = await _apply_unsub(request, token)
    return HTMLResponse(UNSUB_PAGE if ok else "Ссылка недействительна", status_code=200 if ok else 404)


@router.post("/unsub/{token}")
async def unsub_post(request: Request, token: str) -> Response:
    """RFC 8058 one-click: серверный POST без захода на сайт, ответ 200 пустым телом."""
    ok = await _apply_unsub(request, token)
    return Response(status_code=200 if ok else 404)
