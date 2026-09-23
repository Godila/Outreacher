import re

_FROM_NDR = re.compile(r"mailer-daemon|postmaster|no-?reply|maildaemon", re.I)
_STATUS_CODE = re.compile(r"\b([45])\.(\d{1,3})\.(\d{1,3})\b")


def is_ndr(from_email: str, headers: dict[str, str]) -> bool:
    """Отчёт о недоставке: служебный отправитель + авто-заголовки или X-Failed-Recipients."""
    if headers.get("x-failed-recipients"):
        return True
    auto = (headers.get("auto-submitted") or "").lower()
    return bool(_FROM_NDR.search(from_email or "")) and auto not in ("", "no")


def parse_ndr_code(body: str) -> tuple[int, int, int] | None:
    """Код статуса из NDR; 5xx — постоянная ошибка (hard), 4xx — временная."""
    codes = _STATUS_CODE.findall(body or "")
    if not codes:
        return None
    hard = [c for c in codes if c[0] == "5"]
    chosen = hard[0] if hard else codes[0]
    return (int(chosen[0]), int(chosen[1]), int(chosen[2]))
