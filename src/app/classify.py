import datetime as dt
import re

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

_AUTO_SUBMITTED = re.compile(r"auto-generated|auto-replied|vacation", re.I)

UNSUB_PATTERNS = [
    re.compile(r"отпиш", re.I),
    re.compile(r"не\s+пишите", re.I),
    re.compile(r"удали\w*\s+(меня|адрес|из\s+базы|из\s+списка)", re.I),
    re.compile(r"unsubscribe", re.I),
    re.compile(r"remove\s+me", re.I),
]

NEGATIVE_PATTERNS = [
    re.compile(r"не\s+актуальн", re.I),
    re.compile(r"не\s+интерес", re.I),
    re.compile(r"не\s+нужн", re.I),
    re.compile(r"откаж", re.I),
    re.compile(r"не\s+рассматриваем", re.I),
    re.compile(r"не\s+планируем", re.I),
    re.compile(r"нет,?\s+спасибо", re.I),
    re.compile(r"не\s+хочу", re.I),
]

REFERRAL_PATTERNS = [
    re.compile(r"спросите\s+у", re.I),
    re.compile(r"напишите\s+[^.]{0,40}@", re.I),
    re.compile(r"передайте\s+(в|моему|нашему)", re.I),
    re.compile(r"по\s+этому\s+вопросу\s+обращайтесь\s+к", re.I),
]

POSITIVE_PATTERNS = [
    re.compile(r"интересу", re.I),
    re.compile(r"давайте", re.I),
    re.compile(r"шлите|отправьте|пришлите", re.I),
    re.compile(r"сколько\s+стоит|какая\s+(цена|стоимость)", re.I),
    re.compile(r"расскажите|покажите|демо", re.I),
    re.compile(r"актуально|запишитесь|созвонимся", re.I),
    re.compile(r"да[,!\s]"),
]

QUESTION_WORDS = re.compile(r"\b(когда|как|что|почему|сколько|можно|где|кто)\b", re.I)


def _normalize_month(word: str) -> int | None:
    for stem, num in MONTHS.items():
        if word.lower().startswith(stem):
            return num
    return None


def _nearest_future(year: int, month: int, day: int, now: dt.datetime) -> dt.datetime:
    try:
        candidate = dt.datetime(year, month, day)
    except ValueError:  # 31 февраля и т.п. — катим на первый день следующего месяца
        candidate = dt.datetime(year + (month == 12), month % 12 + 1, 1)
    if candidate < now - dt.timedelta(days=30):
        candidate = candidate.replace(year=year + 1)  # дата из прошлого — значит следующий год
    return candidate


def parse_ooo_until(body: str, now: dt.datetime) -> dt.datetime | None:
    """«до 15 января», «с 5 по 20 декабря» (берём конец), «возвращаюсь 02.10», ISO-даты."""
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?", body)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.year
        if year < 100:
            year += 2000
        if 1 <= month <= 12 and 1 <= day <= 31:
            return _nearest_future(year, month, day, now)
    for m in re.finditer(r"(\d{1,2})\s+([а-яё]+)", body, re.I):
        month = _normalize_month(m.group(2))
        if month:
            return _nearest_future(now.year, month, int(m.group(1)), now)
    return now + dt.timedelta(days=5)  # даты нет — дефолтная пауза


def _auto_submitted(headers: dict[str, str]) -> bool:
    value = headers.get("auto-submitted", headers.get("Auto-Submitted", "no"))
    return bool(value and value.strip().lower() != "no") or bool(_AUTO_SUBMITTED.search(headers.get("precedence", "")))


def classify(headers: dict[str, str], body: str) -> tuple[str, dt.datetime | None]:
    """Возвращает (класс, ooo_until). Порядок: ooo → unsub → negative → referral → positive → unknown."""
    if _auto_submitted(headers):
        return "ooo", parse_ooo_until(body, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
    if any(p.search(body) for p in UNSUB_PATTERNS):
        return "unsub", None
    if any(p.search(body) for p in NEGATIVE_PATTERNS):
        return "negative", None
    if any(p.search(body) for p in REFERRAL_PATTERNS):
        return "referral", None
    if any(p.search(body) for p in POSITIVE_PATTERNS):
        return "positive", None
    if "?" in body and QUESTION_WORDS.search(body):
        return "positive", None
    return "unknown", None
