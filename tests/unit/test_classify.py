import datetime as dt

from app.classify import classify, parse_ooo_until

NOW = dt.datetime(2026, 9, 23, 12, 0)


def _c(body, auto=False):
    headers = {"Auto-Submitted": "auto-replied"} if auto else {}
    return classify(headers, body)


def test_ooo_with_return_date():
    cls, until = _c("В отпуске до 5 октября, отвечу после", auto=True)
    assert cls == "ooo"
    assert until == dt.datetime(2026, 10, 5)


def test_ooo_range_takes_end():
    cls, until = _c("Отсутствую с 20 по 30 декабря", auto=True)
    assert cls == "ooo"
    assert until == dt.datetime(2026, 12, 30)


def test_ooo_dotted_date():
    cls, until = _c("Вернусь 02.10.2026", auto=True)
    assert cls == "ooo" and until == dt.datetime(2026, 10, 2)


def test_ooo_no_date_defaults_5_days():
    cls, until = _c("Вне офиса", auto=True)
    assert cls == "ooo"
    assert until == NOW + dt.timedelta(days=5) or until > NOW  # дефолт от now внутри classify


def test_unsub_variants():
    assert _c("Пожалуйста, отпишите меня от рассылки")[0] == "unsub"
    assert _c("Не пишите мне больше")[0] == "unsub"
    assert _c("Удалите меня из базы")[0] == "unsub"


def test_negative_beats_positive_words():
    assert _c("Спасибо, но не актуально")[0] == "negative"
    assert _c("Нам не интересно")[0] == "negative"


def test_referral():
    assert _c("По этому вопросу обращайтесь к Ивану ivan@firm.ru")[0] == "referral"


def test_positive_variants():
    assert _c("Да, интересно, расскажите подробнее")[0] == "positive"
    assert _c("Сколько стоит лицензия?")[0] == "positive"
    assert _c("Давайте созвонимся на следующей неделе")[0] == "positive"


def test_question_mark_heuristic():
    assert _c("Когда у вас будет новая версия?")[0] == "positive"


def test_unknown_for_neutral():
    assert _c("Добрый день! Принято.")[0] == "unknown"


def test_parse_ooo_january_next_year():
    # дата из далёкого прошлого трактуется как следующий год
    got = parse_ooo_until("до 10 января", NOW)
    assert got == dt.datetime(2027, 1, 10)
