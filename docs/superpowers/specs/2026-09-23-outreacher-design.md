# Outreacher — спецификация сервиса аутрича по клиентской базе Битрикс24

**Дата:** 2026-09-23 · **Статус:** на ревью заказчиком
**Основа:** дискавери ([docs/DISCOVERY.md](../../DISCOVERY.md)) · инфраструктура ([docs/INFRA.md](../../INFRA.md)) · верификация методов Б24 по официальной документации (apidocs.bitrix24.ru, сентябрь 2026).

---

## 1. Контекст и цели

Внутренний инструмент (один портал Б24, один оператор) для email-кампаний по собственной клиентской базе: реактивация молчаливых, анонсы, допродажи, контент/опросы. Рынок РФ/СНГ. База — сотни контактов. Успех — ответы (reply) и трафик/заявки.

Отправка — старт на управляемой почте Beget (сабдомен `news.<домен>`), пул отправителей абстрактен (свой MTA / провайдерские SMTP — конфигурация). Фаза 2 (закладные, не реализация) — cold-аутрич по РФ: пулы ящиков, warmup, верификация, реестр согласий.

Ключевые принципы из дискавери, обязательные в реализации:
- секвенции 2–4 касания, follow-up в том же треде, автостоп на ответ/отписку/активную сделку;
- письма plain-text, ≤1 UTM-ссылка, без трекинг-пикселей (opens не меряем принципиально);
- отписка мгновенная, необратимая, глобальная (ст. 18 ФЗ-38) + RFC 8058 one-click;
- hard bounce <2%, стоп кампании при >4%; cooldown контакта между кампаниями 2–3 мес.

## 2. Архитектура

Монолит, один docker compose на VM-APP (Beget, Ubuntu 24.04):

```
nginx (TLS: outreach.<домен>)        — UI, API, вебхуки Б24, /unsub
web      (FastAPI + Jinja2 SSR)      — UI оператора, REST, приём вебхуков/unsub
worker   (asyncio-процессы)          — scheduler / sender / imap-listener / b24-sync
postgres 16                          — единственное хранилище (очереди в нём же)
```

Стек: Python 3.12, FastAPI, SQLAlchemy 2 (async) + Alembic, Postgres 16, Jinja2, aiosmtplib, imapclient (IDLE), APScheduler не нужен — свой tick-цикл (1 мин). Деплой по канону: бэкап `pg_dump | gzip` + `gzip -t` → `alembic upgrade head` → `up -d` → restart nginx → `/health`.

## 3. Модель данных (Postgres)

| Таблица | Ключевые поля |
|---|---|
| `contacts` | `b24_contact_id` (UQ), `email`, `name`, `company`, `assigned_b24_user_id`, `has_active_deal bool`, `last_deal_at`, `last_deal_amount`, `deals_count`, `last_deal_title`, `consent_source`, `consent_at`, `synced_at` |
| `segments` | `name`, `filters jsonb` (давность от/до, сумма от, число сделок, наличие активной сделки, категории) |
| `campaigns` | `name`, `slug` (UTM), `type` (reactivation/announce/upsell/content), `status` (draft/active/paused/done), `send_window` (часы Мск), `daily_cap`, `sender_pool`, `is_advertising bool` (пометка «Реклама» в футере) |
| `sequence_steps` | `campaign_id`, `position`, `delay_days` (рабочие дни от отправки предыдущего), `subject_tpl`, `body_tpl` (Jinja2), `threaded bool` (reply в тред), `ab_variant` nullable (виток 2) |
| `senders` | `email`, `smtp_host/port/user/pass` (секрет), `daily_limit`, `sent_today`, `sent_date`, `health` (ok/paused) |
| `enrollments` | `campaign_id`, `contact_id`, `status` (pending/active/replied/unsubscribed/bounced/paused_ooo/done/excluded), `current_step`, `next_send_at`, `ooO_until`, `paused_reason` |
| `messages` | `enrollment_id`, `step_pos`, `message_id` (RFC822 Message-ID, UQ — идемпотентность), `sender_email`, `subject`, `body` (снапшот), `status` (queued/sent/bounced/failed), `smtp_error`, `retry_count`, `sent_at`, `b24_activity_id` |
| `incoming` | `message_id`, `in_reply_to` → `messages.message_id`, `from_email`, `classification` (positive/negative/ooo/unsub/referral/unknown), `body_snippet`, `classified_by` (rule/manual/llm), `received_at`, `b24_comment_id` |
| `suppression` | `email` (UQ), `reason` (unsub/fbl/hard_bounce/manual), `created_at` — **навсегда** |
| `b24_state` | `portal_domain`, токены OAuth (access/refresh/member_id/application_token), `contacts_watermark`, `deals_watermark` |

Сущности-закладные фазы 2 (создаются схемой, используются минимально): `domains` (DNS-статус, прогрев), `warmup_tasks`, `verification_runs`, `consents` (реестр согласий) — отдельные таблицы, чтобы фаза 2 не ломала схему.

## 4. Компоненты и потоки

### 4.1 b24-sync (worker, каждые 15 мин + вручную)
- Контакты: `crm.contact.list` c `select: [ID, NAME, LAST_NAME, EMAIL, ASSIGNED_BY_ID, DATE_CREATE, ...]` — **классический метод, не crm.item.*** (мульти-поля EMAIL item.* не отдаёт надёжно; в contact.list EMAIL работает и в select, и в filter — подтверждено доками). Пагинация по `next`/`start`, троттлинг ≤2 req/s.
- Сделки: `crm.item.list` c `entityTypeId=2` — **не crm.deal.list** (тот не возвращает привязку к контактам — подтверждено доками). Select (camelCase): `id, title, opportunity, stageId, closed, closedDate, contactId, contacts, utmSource, utmCampaign`. Из сделок считаем RFM: `last_deal_at`, `last_deal_amount`, `deals_count`, `has_active_deal` (= есть незакрытая сделка) → пишем в `contacts`.
- Пользователи: имена ответственных менеджеров — `user.get` (batch по встреченным `ASSIGNED_BY_ID`) → кэш `id→имя` для merge-тега `{{assigned_manager_name}}` и RESPONSIBLE_ID в делах.
- Водяные знаки по `DATE_MODIFY`/`dateModify` (или полная перечитка — база сотни, дельта не критична на старте: полная перечитка раз в 15 мин допустима, ≤2 req/s хватает).

### 4.2 scheduler (worker, tick 1 мин)
Для каждого `enrollments.status IN (pending, active)` c `next_send_at <= now()`:
1. Проверки стопа: `suppression` по email · `contacts.has_active_deal` → excluded · кампания paused/done · `paused_ooo` (resume через 1–2 раб. дня после `ooo_until`).
2. Проверка окна: рабочий день, час в `send_window` (Мск, UTC+3).
3. Лимиты: `campaigns.daily_cap`, `senders.daily_limit` (выбор отправителя с остатком), cooldown контакта (не в другой активной кампании).
4. Рендер Jinja2: контекст = поля контакта + RFM (`{{name}}`, `{{last_deal_title}}`, `{{last_deal_months_ago}}`, `{{assigned_manager_name}}`…). Авто-UTM: `utm_source=outreach&utm_medium=email&utm_campaign={{campaign.slug}}&utm_content=step{{n}}`.
5. Генерация `Message-ID` ДО отправки → вставка `messages` (status=queued) — идемпотентность.
6. Футер: юрлицо + контакты + ссылка отписки (HMAC-токен); `is_advertising=true` → префикс «Реклама».

### 4.3 sender (worker)
Забирает `queued`, шлёт через SMTP пула (aiosmtplib, TLS 465). MIME: plain text; заголовки `List-Unsubscribe: <https://outreach.<домен>/unsub/{token}>`, `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, `Feedback-ID: <campaign-slug>:<enrollment_id>:<sender_email>`. Follow-up (threaded): `In-Reply-To`/`References` = Message-ID первого письма шага 1. SMTP-ошибки: 4xx → ретрай с backoff (15 мин → 1ч → 4ч, ≤3), 5xx → failed. После sent: `crm.activity.add` (см. §5) асинхронно, сбой Б24 не роняет отправку (очередь повторной записи).

### 4.4 imap-listener (worker, IDLE на каждом ящике пула)
Новое письмо → если `In-Reply-To` матчит наш `messages.message_id`: сохранить в `incoming`, классифицировать, применить. Незаматченные письма от адресатов активных кампаний (новый тред в ответ) — тоже в `incoming` с классификацией правилами; unknown → оператору, ничего не классифицируется «по умолчанию positive». NDR (от MAILER-DAEMON, `Auto-Submitted`/`Delivery Status`): парсинг кодов — 5.x → hard bounce → suppression + enrollment.bounced; 4.x → софт-ретрай не нужен (шаг не создаёт), фиксируем.

### 4.5 Классификация ответов (MVP — правила, слой заменяемый)
Порядок: (1) заголовок `Auto-Submitted != no` → OOO (дата возврата — парсинг RU-паттернов «до 15 января», «с 5 по 20», нет даты → `ooo_until = now + 5 дней`); (2) RU-паттерны отписки («отпишите», «не пишите», «удалите») → unsub; (3) негатив («не актуально», «не интересно», «откажитесь»); (4) позитив («интересно», «да», «давайте», «шлите», «сколько стоит», вопросительные знаки + вопросительные слова) — приоритет ниже негатива; (5) referral («напишите Иванову», «спросите у…»); (6) иначе → unknown. Действия по классам — из §4.6/дискавери §2.5. Unknown и ошибочные — в UI-инбокс оператору на разметку (кнопки), ручная метка важнее правил (`classified_by=manual`).

### 4.6 Применение классификаций
- positive → enrollment.replied + **стоп секвенции**; `crm.timeline.comment.add` в контакт + `crm.activity.add` дело «Связаться с клиентом» ответственному (DEADLINE=now+4ч, TYPE_ID=3 Задача).
- negative → replied + стоп; комментарий в таймлайн; контакт в cooldown.
- ooo → paused_ooo, `next_send_at = ooo_until + 1 раб.день`.
- unsub → suppression навсегда + стоп + пометка контакта в Б24 (UF/комментарий).
- referral → стоп + комментарий с контактом-маршрутом.
- bounce hard → suppression + стоп.

### 4.7 Отписка (web)
`GET /unsub/{token}` (страница «Вы отписаны», токен HMAC(enrollment_id+email)) и `POST /unsub/{token}` (RFC 8058 one-click, ответ 200 без редиректа). Оба → suppression + Б24-пометка. Токен не протухает (Mail.ru — ссылка ≥30 дней; фактически вечный).

## 5. Интеграция Б24 — верифицированные методы и грабли

| Задача | Метод | Параметры (проверено по докам 2026-09) |
|---|---|---|
| Контакты | `crm.contact.list` | select c EMAIL (массив VALUE/VALUE_TYPE), filter EMAIL=…, пагинация start/next. DEPRECATED-метка — игнорируем, item.* ненадёжен с мульти-полями |
| Сделки + контакты сделок | `crm.item.list` | `entityTypeId=2`, camelCase-поля, `contacts`/`contactId` в select. crm.deal.list НЕ отдаёт CONTACT_IDS |
| Лог отправки | `crm.activity.add` | `TYPE_ID=4` (Письмо, enum подтверждён), `OWNER_TYPE_ID=3` (контакт) / 2 (сделка), `COMMUNICATIONS=[{VALUE: email, ENTITY_ID, ENTITY_TYPE_ID:3}]` (ровно одна), `DIRECTION=2` (исходящее), `COMPLETED='Y'` (история), `SETTINGS: {DISABLE_SENDING_MESSAGE_COPY: 'Y'}` — **чтобы Б24 сам не слал копию письма**, `SUBJECT`, `RESPONSIBLE_ID`=менеджер, `DESCRIPTION`=текст письма |
| Ответ | `crm.timeline.comment.add` | `ENTITY_TYPE='contact'`, `ENTITY_ID`, `COMMENT` («Ответ на кампанию X: <класс> — <snippet>») |
| Дело менеджеру | `crm.activity.add` | TYPE_ID=3 (Задача), DEADLINE, RESPONSIBLE_ID |
| OAuth | локальное приложение | **refresh_token ротирует** на каждый refresh → TokenManager с anyio.Lock + перечитывание из БД под локом; URL oauth.bitrix24.tech; выдача прав ≠ живой токен → форс-рефреш после ONAPPINSTALL |
| Вебхуки | ONAPPINSTALL + event.bind | form-urlencoded с php-массивами ИЛИ JSON (id строками) → толерантный парсер + int-коэрсинг; авторизация эшелонами (secret → user.current self-check → application_token) |
| Лимиты | REST | ≤2 req/s (QUERY_LIMIT_EXCEEDED → backoff), batch ≤50 |

Троттлер Б24-клиента — глобальный (семафор), все вызовы через него. Сбои Б24 — очередь повторов с backoff, не блокируют отправку писем.

## 6. Юридический контур (в коде)

- `suppression` — единственная точка истины; ВСЕ выборки перед отправкой фильтруются по нему (in-DB join, не в приложении).
- Футер каждого письма: наименование юрлица, адрес, телефон; отписка; `is_advertising` → «Реклама» + рекламодатель.
- Согласия: `contacts.consent_source/consent_at` — из Б24 (источник контакта), виток 2 — формы захвата (`consents`).
- ПДн: только VM в РФ, бэкапы в РФ; UI за basic-auth (operator), секреты в env.

## 7. Метрики и UI

Метрики кампании (SQL по messages/incoming): sent, bounce (hard/soft), reply, positive reply rate, unsub, delivery rate; по шагам. Open rate отсутствует by design. «Ожившие сделки» (виток 2): сделки с `utmSource='outreach' && utmCampaign=slug` в окне кампании+30д.

UI (Jinja2-страницы): Дашборд (кампании+метрики) · Кампания (секвенция-редактор, предпросмотр с подстановкой случайного контакта) · Сегменты (конструктор фильтров + предпросмотр размера) · Инбокс ответов (классификация, ручная разметка unknown) · Стоп-лист · Отправители (пул, лимиты, health).

## 8. Устойчивость

- Outbox-паттерн: запись с Message-ID до SMTP; падение между SMTP и апдейтом статуса → при старте сверка (дубль не уйдёт: message_id UQ).
- Идемпотентность вебхуков: дедуп по `event_id`+`ts`.
- bounce-rate guard: >4% hard по кампании за день → авто-pause + уведомление оператору (в UI + email).
- Всё «непонятное» (unknown классификации, SMTP 5xx, ошибки Б24) — в лог + UI-список, ничего не теряется молча.
- Liveness: `/health` (web+worker heartbeat+БД), journald-логи.

## 9. Тестирование

- Unit: scheduler-логика (окна, задержки в рабочих днях, стоп-правила, лимиты, ротация senders) на фикстурах-времени; классификатор на корпусе RU-ответов (≥50 примеров на класс, включая OOO-формулировки); рендер шаблонов (merge, UTM, футер, токен).
- Integration: B24-клиент против мок-сервера (вебхуки form-urlencoded/JSON, ротация refresh, троттлинг, batch); БД-миграции.
- E2E (ручной чек-лист на тестовом портале + тестовых ящиках): установка приложения → синк → кампания на 3 тестовых контакта → письмо → ответ positive → стоп + комментарий в таймлайне Б24 + дело менеджеру → отписка → suppression → повторная кампания не включает контакт.

## 10. Фазирование

**MVP (эта спека):** синк, сегменты, кампании/секвенции, отправка (Beget-почта), IMAP-листенер + правила классификации + ручная разметка, отписка, Б24-маппинг, метрики, UI.
**Виток 2:** A/B вариантов шага, cooldown-контроль и календарь кампаний, верификатор email, LLM-классификация (замена слоя правил), «ожившие сделки», робот Б24 «добавить в кампанию» (`bizproc.robot.add`, суффикс `_robot`), формы сбора согласий.
**Фаза cold (закладные уже в схеме):** пулы senders+domains, warmup-воркер, верификация перед кампанией, вторичные домены, пулы провайдерских ящиков.

## 11. Открытые вопросы (не блокируют MVP)

1. Ответ поддержки Beget по лимитам почты (тикет из INFRA §1.1) — влияет только на daily_cap.
2. Реальные ID полей/источников на портале (SOURCE_ID, воронки) — уточняется при онбординге первого синка.
3. Учёт нескольких email у одного контакта Б24 — MVP: берём WORK-тип, при отсутствии — первый; дедуп по email в enrollments.
