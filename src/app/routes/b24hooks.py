import hmac

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.b24.tokenmanager import TokenManager
from app.b24.webhooks import event_digest, parse_webhook, verify_webhook
from app.models import B24State, ProcessedEvent, utcnow

router = APIRouter()


def get_token_manager(request: Request) -> TokenManager:
    return request.app.state.token_manager


@router.post("/b24/event")
async def b24_event(request: Request) -> Response:
    body = await request.body()
    payload = parse_webhook(body, request.headers.get("content-type", ""))

    # Дедуп: повторная доставка того же события — молча 200
    async with request.app.state.session_factory() as session:
        digest = event_digest(body)
        exists = (
            await session.execute(select(ProcessedEvent).where(ProcessedEvent.event_id == digest))
        ).scalars().first()
        if exists is None:
            session.add(ProcessedEvent(event_id=digest, received_at=utcnow()))
            await session.commit()
        else:
            return Response(status_code=200)

    stored_token = None
    async with request.app.state.session_factory() as session:
        state = (await session.execute(select(B24State))).scalars().first()
        stored_token = state.application_token if state else None

    provided_secret = request.headers.get("x-webhook-secret", "")
    configured_secret = request.app.state.settings.b24_webhook_secret
    secret_ok = bool(configured_secret) and hmac.compare_digest(provided_secret, configured_secret)
    if not secret_ok and not await verify_webhook(payload, None, stored_token):
        return Response(status_code=403)

    event = payload.get("event", "")
    auth = payload.get("auth") or {}
    domain = auth.get("domain", "")

    if event == "ONAPPINSTALL" and domain:
        tm: TokenManager = get_token_manager(request)
        await tm.install(domain, auth)
    # прочие события в MVP не подписаны — игнорируем

    return Response(status_code=200)
