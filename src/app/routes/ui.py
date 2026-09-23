import base64
import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.b24map import apply_classification, log_activity_payload  # noqa: F401 (log_activity_payload для отладки)
from app.mail.listener import _resolve_enrollment
from app.models import (
    B24User,
    Campaign,
    Contact,
    Enrollment,
    Incoming,
    Message,
    Segment,
    Sender,
    Suppression,
)
from app.segments import enroll as segment_enroll
from app.segments import preview as segment_preview
from app.models import SequenceStep, utcnow

templates = Jinja2Templates(directory="templates")


def check_auth(request: Request) -> bool:
    settings = request.app.state.settings
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode()
    except Exception:
        return False
    _, _, password = decoded.partition(":")
    return hmac.compare_digest(password, settings.operator_password)


def require_auth(request: Request) -> None:
    if not check_auth(request):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="outreacher"'},
        )


def render(request: Request, template: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, template, ctx)


from fastapi import APIRouter, Form  # noqa: E402

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    async with request.app.state.session_factory() as session:
        campaigns = (
            await session.execute(
                select(Campaign, func.count(Enrollment.id))
                .join(Enrollment, Enrollment.campaign_id == Campaign.id, isouter=True)
                .group_by(Campaign.id)
                .order_by(Campaign.id.desc())
            )
        ).all()
        rows = []
        for campaign, enrollment_count in campaigns:
            sent = (
                await session.execute(
                    select(func.count(Message.id)).join(Enrollment).where(
                        Enrollment.campaign_id == campaign.id, Message.status == "sent"
                    )
                )
            ).scalar_one()
            replied = (
                await session.execute(
                    select(func.count(Enrollment.id)).where(
                        Enrollment.campaign_id == campaign.id, Enrollment.status == "replied"
                    )
                )
            ).scalar_one()
            bounced = (
                await session.execute(
                    select(func.count(Enrollment.id)).where(
                        Enrollment.campaign_id == campaign.id, Enrollment.status == "bounced"
                    )
                )
            ).scalar_one()
            rows.append(
                {
                    "campaign": campaign,
                    "enrolled": enrollment_count,
                    "sent": sent,
                    "replied": replied,
                    "bounced": bounced,
                }
            )
        return render(request, "dashboard.html", rows=rows)


@router.get("/campaigns/new", response_class=HTMLResponse)
async def campaign_form(request: Request):
    async with request.app.state.session_factory() as session:
        segments = (await session.execute(select(Segment))).scalars().all()
        return render(request, "campaign_form.html", segments=segments, campaign=None, steps=[])


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def campaign_detail(request: Request, campaign_id: int):
    async with request.app.state.session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        if campaign is None:
            raise HTTPException(404)
        steps = (
            await session.execute(
                select(SequenceStep).where(SequenceStep.campaign_id == campaign_id).order_by(SequenceStep.position)
            )
        ).scalars().all()
        enrollments = (
            await session.execute(
                select(Enrollment, Contact).join(Contact, Enrollment.contact_id == Contact.id)
                .where(Enrollment.campaign_id == campaign_id)
                .order_by(Enrollment.id.desc())
                .limit(200)
            )
        ).all()
        segment = await session.get(Segment, campaign.segment_id) if campaign.segment_id else None
        preview_count = len(await segment_preview(session, segment.filters)) if segment else 0
        return render(
            request, "campaign.html", campaign=campaign, steps=steps,
            enrollments=enrollments, segment=segment, preview_count=preview_count,
        )


@router.post("/campaigns")
async def campaign_create(
    request: Request,
    name: str = Form(...),
    slug: str = Form(...),
    type: str = Form("reactivation"),
    segment_id: int = Form(0),
    daily_cap: int = Form(100),
    is_advertising: bool = Form(False),
    step_count: int = Form(2),
):
    form = await request.form()
    async with request.app.state.session_factory() as session:
        campaign = Campaign(
            name=name, slug=slug, type=type,
            segment_id=segment_id or None, daily_cap=daily_cap,
            is_advertising=is_advertising, status="draft",
        )
        session.add(campaign)
        await session.flush()
        for pos in range(1, step_count + 1):
            subject = str(form.get(f"subject_{pos}", "")).strip()
            body = str(form.get(f"body_{pos}", "")).strip()
            if not subject or not body:
                continue
            session.add(
                SequenceStep(
                    campaign_id=campaign.id, position=pos,
                    delay_days=int(form.get(f"delay_{pos}", 0) or 0),
                    subject_tpl=subject, body_tpl=body,
                    threaded=form.get(f"threaded_{pos}") is not None,
                )
            )
        await session.commit()
        return RedirectResponse(f"/campaigns/{campaign.id}", status_code=303)


@router.post("/campaigns/{campaign_id}/status")
async def campaign_status(request: Request, campaign_id: int, status: str = Form(...)):
    async with request.app.state.session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        if campaign is None:
            raise HTTPException(404)
        campaign.status = status
        await session.commit()
    return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/enroll")
async def campaign_enroll(request: Request, campaign_id: int):
    async with request.app.state.session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        if campaign is None or campaign.segment_id is None:
            raise HTTPException(400)
        segment = await session.get(Segment, campaign.segment_id)
        created = await segment_enroll(session, campaign_id, segment.filters)
    return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)


@router.get("/segments", response_class=HTMLResponse)
async def segments_page(request: Request):
    async with request.app.state.session_factory() as session:
        rows = (await session.execute(select(Segment))).scalars().all()
        return render(request, "segments.html", segments=rows)


@router.post("/segments")
async def segment_create(request: Request, name: str = Form(...), filters_json: str = Form("{}")):
    import json

    try:
        filters = json.loads(filters_json or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "bad json")
    async with request.app.state.session_factory() as session:
        session.add(Segment(name=name, filters=filters))
        await session.commit()
    return RedirectResponse("/segments", status_code=303)


@router.get("/inbox", response_class=HTMLResponse)
async def inbox_page(request: Request):
    async with request.app.state.session_factory() as session:
        rows = (
            await session.execute(select(Incoming).order_by(Incoming.id.desc()).limit(100))
        ).scalars().all()
        return render(request, "inbox.html", items=rows)


@router.post("/inbox/{incoming_id}/classify")
async def inbox_classify(request: Request, incoming_id: int, classification: str = Form(...)):
    settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        inc = await session.get(Incoming, incoming_id)
        if inc is None:
            raise HTTPException(404)
        inc.classification = classification
        inc.classified_by = "manual"
        enrollment = await _resolve_enrollment(session, inc.in_reply_to if inc.in_reply_to else None, inc.from_email)
        if enrollment is not None:
            await apply_classification(session, settings, inc, enrollment)
        await session.commit()
    return RedirectResponse("/inbox", status_code=303)


@router.get("/suppression", response_class=HTMLResponse)
async def suppression_page(request: Request):
    async with request.app.state.session_factory() as session:
        rows = (await session.execute(select(Suppression).order_by(Suppression.id.desc()))).scalars().all()
        return render(request, "suppression.html", items=rows)


@router.get("/senders", response_class=HTMLResponse)
async def senders_page(request: Request):
    async with request.app.state.session_factory() as session:
        rows = (await session.execute(select(Sender))).scalars().all()
        return render(request, "senders.html", items=rows)


@router.post("/senders")
async def sender_create(
    request: Request,
    email: str = Form(...), smtp_host: str = Form(...), smtp_user: str = Form(...),
    smtp_pass: str = Form(...), imap_host: str = Form(...), imap_user: str = Form(...),
    imap_pass: str = Form(...), daily_limit: int = Form(50), display_name: str = Form(""),
):
    async with request.app.state.session_factory() as session:
        session.add(
            Sender(
                email=email, display_name=display_name, smtp_host=smtp_host,
                smtp_port=465, smtp_user=smtp_user, smtp_pass=smtp_pass,
                imap_host=imap_host, imap_port=993, imap_user=imap_user, imap_pass=imap_pass,
                daily_limit=daily_limit,
            )
        )
        await session.commit()
    return RedirectResponse("/senders", status_code=303)
