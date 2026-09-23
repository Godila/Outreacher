import datetime as dt

from app.config import Settings
from app.engine.render import add_utm_to_links, render_step
from app.models import Campaign, Contact, SequenceStep


def _settings():
    return Settings(
        app_secret="sec",
        base_url="https://outreach.example.ru",
        legal_entity="ООИ «Тест»",
        legal_address="г. Москва, ул. Тестовая, 1",
        legal_phone="+7 495 000",
    )


def test_add_utm_skips_existing():
    text = "Смотрите https://x.ru/page и https://y.ru/?utm_source=other привет"
    out = add_utm_to_links(text, "camp", 2)
    assert "utm_source=outreach" in out and "utm_campaign=camp" in out and "utm_content=step2" in out
    # ссылка с чужим utm_source не тронута
    assert "https://y.ru/?utm_source=other" in out
    assert out.count("utm_source=") == 2


def test_add_utm_keeps_punctuation():
    out = add_utm_to_links("Перейдите https://x.ru/offer.", "c", 1)
    assert out.startswith("Перейдите https://x.ru/offer?")
    assert out.endswith(".")  # точка-хвост не попала в URL
    assert "utm_campaign=c" in out


def test_render_step_merge_and_footer():
    settings = _settings()
    contact = Contact(
        b24_contact_id=1, email="cli@x.ru", name="Мария Иванова",
        last_deal_title="Лицензия Pro", last_deal_amount=12000.0,
        last_deal_at=dt.datetime.utcnow() - dt.timedelta(days=100), deals_count=3,
    )
    campaign = Campaign(name="C", slug="react-q4", status="active")
    step = SequenceStep(
        campaign_id=1, position=1, delay_days=0,
        subject_tpl="{{first_name}}, ваш {{last_deal_title}}",
        body_tpl="Здравствуйте, {{name}}! Вы брали {{last_deal_title}} {{last_deal_months_ago}} мес. назад: https://site.ru/offer",
    )
    rendered = render_step(step, campaign, contact, "Анна", 42, settings)
    assert rendered.subject.startswith("Мария")
    assert "Лицензия Pro" in rendered.subject
    assert "Мария Иванова" in rendered.body
    assert "utm_campaign=react-q4" in rendered.body
    assert "https://outreach.example.ru/unsub/42." in rendered.body
    assert "ООИ «Тест»" in rendered.body


def test_render_advertising_prefix():
    settings = _settings()
    contact = Contact(b24_contact_id=1, email="c@x.ru", name="Иван")
    campaign = Campaign(name="C", slug="adv", status="active", is_advertising=True)
    step = SequenceStep(campaign_id=1, position=1, delay_days=0, subject_tpl="Тема", body_tpl="Текст")
    rendered = render_step(step, campaign, contact, None, 1, settings)
    assert rendered.subject == "Реклама: Тема"
