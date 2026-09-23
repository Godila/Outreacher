import re
import urllib.parse
from dataclasses import dataclass

from jinja2 import Environment, StrictUndefined

from app.config import Settings
from app.engine.unsub import make_token
from app.models import Campaign, Contact, SequenceStep

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

_env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


@dataclass
class Rendered:
    subject: str
    body: str
    unsub_token: str


def _months_ago(when) -> int | None:
    if when is None:
        return None
    import datetime as dt

    return max(0, (dt.datetime.utcnow().replace(tzinfo=None) - when.replace(tzinfo=None)).days // 30)


def build_context(contact: Contact, manager_name: str | None) -> dict:
    return {
        "name": contact.name or "коллега",
        "first_name": (contact.name or "коллега").split()[0] if contact.name else "коллега",
        "email": contact.email,
        "company": contact.company or "",
        "last_deal_title": contact.last_deal_title or "последний заказ",
        "last_deal_amount": int(contact.last_deal_amount or 0),
        "last_deal_months_ago": _months_ago(contact.last_deal_at),
        "deals_count": contact.deals_count,
        "assigned_manager_name": manager_name or "наша команда",
    }


def add_utm_to_links(text: str, campaign_slug: str, step_pos: int) -> str:
    """Каждой http(s)-ссылке без utm_source добавляем полный набор меток."""
    utm = {
        "utm_source": "outreach",
        "utm_medium": "email",
        "utm_campaign": campaign_slug,
        "utm_content": f"step{step_pos}",
    }

    def _sub(match: re.Match) -> str:
        url = match.group(0).rstrip(".,;")  # хвостовые знаки препинания не часть URL
        tail = match.group(0)[len(url):]
        parsed = urllib.parse.urlsplit(url)
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        keys = {k for k, _v in params}
        for k, v in utm.items():
            if k not in keys:
                params.append((k, v))
        query = urllib.parse.urlencode(params)
        new_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
        return new_url + tail

    return _URL_RE.sub(_sub, text)


def render_step(
    step: SequenceStep,
    campaign: Campaign,
    contact: Contact,
    manager_name: str | None,
    enrollment_id: int,
    settings: Settings,
) -> Rendered:
    ctx = build_context(contact, manager_name)
    subject = _env.from_string(step.subject_tpl).render(**ctx)
    body = _env.from_string(step.body_tpl).render(**ctx)
    body = add_utm_to_links(body, campaign.slug, step.position)

    token = make_token(enrollment_id, contact.email, settings.app_secret)
    unsub_url = f"{settings.base_url}/unsub/{token}"
    footer = (
        "\n\n—\n"
        f"{settings.legal_entity}\n{settings.legal_address}, {settings.legal_phone}\n"
        f"Отписаться: {unsub_url}"
    )
    if campaign.is_advertising:
        subject = f"Реклама: {subject}"
    return Rendered(subject=subject, body=body + footer, unsub_token=token)
