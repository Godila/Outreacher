from app.mail.builder import build_mime


def _build(**kw):
    defaults = dict(
        from_email="anna@news.example.ru", from_name="Анна", to_email="cli@x.ru",
        subject="Тема", body="Текст письма", unsub_url="https://out.example.ru/unsub/1.abc",
        feedback_id="camp:1:sender",
    )
    defaults.update(kw)
    return build_mime(**defaults)


def test_headers_present():
    msg = _build(message_id="<abc@outreacher>")
    assert msg["List-Unsubscribe"] == "<https://out.example.ru/unsub/1.abc>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert msg["Feedback-ID"] == "camp:1:sender"
    assert msg["Message-ID"] == "<abc@outreacher>"
    assert msg["Content-Type"].startswith("text/plain")
    assert "Текст письма" in msg.get_content()
    assert msg["In-Reply-To"] is None


def test_thread_headers():
    msg = _build(in_reply_to="<abc@outreacher>", references="<abc@outreacher>")
    assert msg["In-Reply-To"] == "<abc@outreacher>"
    assert msg["References"] == "<abc@outreacher>"


def test_utf8_body():
    msg = _build(body="Привет, мир! Ёжик.")
    payload = msg.get_content()
    assert "Привет, мир! Ёжик." in payload
