# Outreacher

Внутренний сервис email-кампаний по собственной клиентской базе Битрикс24: реактивация молчаливых клиентов, анонсы, допродажи, контент. Рынок РФ/СНГ.

- **Дискавери и методология:** [docs/DISCOVERY.md](docs/DISCOVERY.md)
- **Инфраструктура (Beget, DNS, почта):** [docs/INFRA.md](docs/INFRA.md)
- **Спецификация:** [docs/superpowers/specs/2026-09-23-outreacher-design.md](docs/superpowers/specs/2026-09-23-outreacher-design.md)
- **План реализации:** [docs/superpowers/plans/2026-09-23-outreacher-mvp.md](docs/superpowers/plans/2026-09-23-outreacher-mvp.md)

## Архитектура

```
nginx (TLS) → web (FastAPI: UI/API/вебхуки Б24/отписка)
worker — циклы: scheduler (60с) · sender (30с) · IMAP-листенер (60с) · B24-jobs (30с) · sync (15мин)
postgres 16 — единственное хранилище (очереди в нём же)
```

Отправка — SMTP пула ящиков (старт: почта Beget на сабдомене `news.<домен>`), приём ответов — IMAP. Пул отправителей абстрактен: свой MTA и любые провайдерские SMTP добавляются как записи в таблице `senders`.

Ключевые свойства (из дискавери):

- секвенции 2–4 шага, follow-up в тот же тред, автостоп на ответ/отписку/активную сделку;
- отписка мгновенная и навсегда (RFC 8058 one-click + HMAC-токен), глобальный стоп-лист;
- hard bounce → suppression; 4xx → ретраи; bounce-код письма классифицируется из NDR;
- ответы классифицируются правилами (positive/negative/ooo/unsub/referral), unknown — оператору;
- отправка логируется в таймлайн Б24 email-делом (`TYPE_ID=4`, `DISABLE_SENDING_MESSAGE_COPY`), ответ — комментарием + задачей менеджеру;
- без трекинг-пикселей: opens не меряем, трафик — UTM-метками (`utm_source=outreach`).

## Запуск (docker compose)

```bash
cp .env.example .env  # заполните секреты
docker compose up -d postgres
docker compose run --rm web alembic upgrade head
docker compose up -d
curl http://localhost:8000/health
```

UI: `http://<host>:8000/` (Basic-auth, пароль `OUTREACHER_OPERATOR_PASSWORD`).

Разработка локально:

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest tests/ -q
OUTREACHER_DATABASE_URL=sqlite+aiosqlite:///./dev.db .venv/Scripts/python -m uvicorn app.main:app --reload
```

## Установка приложения Битрикс24

1. Портал → Разработчикам → «Другое» → локальное приложение:
   - URL обработчика: `https://outreach.<домен>/b24/event`
   - Права: CRM (чтение/запись), Пользователи, batch; `bizproc` — для робота (виток 2).
2. `OUTREACHER_B24_CLIENT_ID/SECRET` — из настроек приложения.
3. Установите приложение на портале → придёт `ONAPPINSTALL` (токены сохранятся, `application_token` захватится автоматически).
4. Ручной синк: подождать цикл sync (15 мин) или перезапустить worker.

## Почтовые ящики (пул отправителей)

UI → «Отправители» → добавить ящик (SMTP/IMAP host+user+pass, лимит/день ≤50). Для Beget-почты: `smtp.beget.com:465` / `imap.beget.com:993`, ящики на сабдомене `news.<домен>` (см. [docs/INFRA.md](docs/INFRA.md): DNS, DKIM, постмастеры, прогрев).

## Операционные правила (зашиты в код)

- Окно отправки: рабочие дни, `OUTREACHER_SEND_HOUR_FROM..TO` (Мск, по умолчанию 8–12).
- Задержки шагов считаются в рабочих днях; OOO — пауза с возобновлением через 1 раб. день после даты возврата.
- Лимиты: `campaigns.daily_cap`, `senders.daily_limit`; ротация — наименее загруженный ящик.
- Стоп-лист (`suppression`) — навсегда: отписки, FBL-жалобы (вносятся вручную/скриптом), hard bounce.

## Юридический контур (РФ)

- Письма своим клиентам о своих услугах — вне ФЗ-38 «рекламы»; чужие офферы — только с пометкой «Реклама» (флаг кампании).
- Отписка мгновенная (ст. 18 ФЗ-38); футер: юрлицо, адрес, телефон (env `OUTREACHER_LEGAL_*`).
- ПДн хранятся только в своей БД (VM в РФ); марк `erid` на email-рассылки не требуется (позиция РКН/ФАС 2023, подтверждена после 01.09.2025 — мониторим).

## E2E-приёмка

Чек-лист приёмного теста: [docs/E2E-CHECKLIST.md](docs/E2E-CHECKLIST.md).
