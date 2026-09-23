from email.message import EmailMessage
from email.utils import formataddr


def build_mime(
    *,
    from_email: str,
    from_name: str,
    to_email: str,
    subject: str,
    body: str,
    unsub_url: str,
    feedback_id: str,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> EmailMessage:
    """Plain-text письмо с заголовками отписки (RFC 8058), Feedback-ID и трединга."""
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_email))
    msg["To"] = to_email
    msg["Subject"] = subject
    if message_id:
        msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg["List-Unsubscribe"] = f"<{unsub_url}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["Feedback-ID"] = feedback_id
    msg.set_content(body)  # text/plain; charset="utf-8"
    return msg
