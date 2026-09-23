# Sales engagement продукты и проектирование сервиса аутрича с интеграцией в Bitrix24

> Сырой отчёт исследовательского агента от 2026-09-23. Все факты подтверждены источниками (URL). Неподтверждённое помечено явно.
> Контекст: внутренний сервис аутрича по собственной клиентской базе (сотни контактов) из Bitrix24, РФ/СНГ.

---

## ЧАСТЬ 1. Фичи-инвентарь ведущих инструментов

### 1.1. Сводная таблица

| Блок | Instantly.ai | Lemlist | Smartlead | Reply.io | Woodpecker | Outreach / Salesloft |
|---|---|---|---|---|---|---|
| **Модель кампании** | Многошаговые email-секвенции, шаги email + пауза | Мультиканальные секвенции: email + LinkedIn + звонки | Многошаговые секвенции, «sub-sequences» и условное ветвление | Секвенции email + LinkedIn + звонки + SMS + WhatsApp; условное ветвление | Секвенции email с условиями, follow-up по поведению | Outreach «Sequences» / Salesloft «Cadences»: правила, расписания, права, volume controls |
| **Ротация ящиков** | Да, автоматическая (объём делится между ящиками) | Подключение своих ящиков | Да, sender rotation, unlimited mailboxes | Unlimited mailboxes | Подключение своих ящиков | Enterprise: свои почтовые серверы/интеграции |
| **Warmup** | Да, встроенный, включается автоматически, сеть warmup-аккаунтов | Не подтверждено в данном исследовании | Да, AI-driven warmup | Да (через MailToaster.ai) + Email Health Checker | Не подтверждено в данном исследовании | Не исследовалось детально |
| **A/B** | A/Z-тесты: до 26 вариантов на шаг, автопауза слабых | A/B subject lines, авто-распределение | A/B-варианты в секвенции | Не подтверждено | A/B (subject + тело) | Не исследовалось детально |
| **Обработка ответов** | Unibox + AI-теггинг: Interested / Not Interested / OOO / Meeting Request / Not Now + свои категории | «Stop sending messages when lead…» — продолжить/пауза/стоп | Master Inbox с AI-категоризацией | Детект ответов + ветвление по поведению | Автостоп follow-up при auto-reply (OOO), ручной resume | OOO-детект у обоих |
| **API** | Да, API v2, webhooks | Да, REST + webhooks | Да, REST v1 + webhooks | Да, REST + OpenAPI + OAuth + webhooks | Да, REST + webhooks | Enterprise API (Outreach — документированный) |

### 1.2. Instantly.ai — детали

- **Секвенции и расписание**: многошаговый sequence builder; правила отправки во вкладке «Options» кампании. **Rules & Alerts**: авто-пауза/троттлинг кампании при превышении порогов bounce, жалоб, inbox placement ([instantly.ai — Automate Campaign Pauses](https://instantly.ai)).
- **Ящики**: неограниченное число аккаунтов и warmup на всех тарифах; при запуске кампании объём автоматически делится между всеми ящиками (inbox rotation). Warmup включается автоматически, есть automated inbox placement testing, bounce monitoring с авто-подавлением.
- **A/Z-тестирование**: до **26 вариантов на один шаг** (A–Z) темы и тела; слабые варианты авто-ставятся на паузу ([help.instantly.ai — A/Z Testing](https://help.instantly.ai)).
- **Ответы — Unibox**: единый входящий; **AI-теггинг ответов**: Interested, Not Interested, Out of Office, Meeting Request, Not Now + кастомные категории ([help.instantly.ai — Unibox](https://help.instantly.ai/en/articles/6576561-how-to-manage-unibox-best-practices-for-replying-to-leads)).
- **API/webhooks**: публичный REST API, base `https://api.instantly.ai/api/v2`, Bearer API-ключ со скоупами; кампании, лиды, списки, email-аккаунты (OAuth Google/Microsoft), sub-workspace'ы; webhooks на email events (sent/delivered/opened/clicked/replied/bounced), lead events, meeting events; TypeScript SDK, CLI, MCP. Источник: [developer.instantly.ai](https://developer.instantly.ai/).
- **Цены**: Growth ~$37–47/мес (1 000 контактов, 5 000 писем/мес), Hypergrowth $97/мес; unlimited аккаунты и warmup; лид-база отдельно ([instantly.ai — pricing](https://instantly.ai)).

### 1.3. Lemlist — детали

- **Мультиканальность**: секвенции с шагами email, LinkedIn, звонки в одном UI.
- **Персонализация**: Liquid-синтаксис ([academy.lemlist.com](https://academy.lemlist.com)).
- **A/B**: кнопка A/B в шаге секвенции; рекомендация 50–100 лидов на вариант.
- **Обработка ответов**: Campaign Settings → «Stop sending messages when lead…» (например «Replies to email»).
- **API**: REST, basic-аутентификация по API-ключу; эндпоинты `GET /api/campaigns`, `/api/leads`, «Create Lead in Campaign»; webhooks на открытия/ответы/отписки ([developer.lemlist.com](https://developer.lemlist.com)).
- **Цены**: per-user: Email Pro ~$69/юзер/мес, Multichannel Expert ~$99/юзер/мес.

### 1.4. Smartlead — детали

- **Ящики**: unlimited mailboxes; автоматическая ротация отправителей; встроенная AI-warmup-сеть.
- **Секвенции**: мультиканальные, sub-sequence / условное ветвление при ответах.
- **Ответы**: Master Inbox с AI-категоризацией; классификация синхронизируется в CRM через нативную интеграцию с HubSpot. **С Bitrix24 нативной интеграции нет**.
- **API**: REST API v1, base `https://server.smartlead.ai/api/v1`, API-ключом; кампании, лиды, email-аккаунты (SMTP/Gmail/Outlook + ротация), warmup, аналитика, webhooks; превышение лимитов → HTTP 429, exponential backoff ([api.smartlead.ai/introduction](https://api.smartlead.ai/introduction)).
- **Цены**: флэт: Basic ~$33–39/мес, unlimited мест и ящиков.

### 1.5. Reply.io — детали

- **Секвенции**: email + LinkedIn (заявки, просмотры, сообщения, voice notes) + звонки + SMS + WhatsApp; условное ветвление.
- **AI**: Jason AI (драфты, обработка ответов) и Magic Sequence.
- **Deliverability**: unlimited mailboxes, warmup через MailToaster.ai, Email Health Checker, случайные задержки между письмами.
- **API**: REST с OpenAPI-спеками, OAuth, webhooks, async jobs; Bearer, `/v3/whoami` ([docs.reply.io](https://docs.reply.io)).
- **Цены**: от ~$59–69/место/мес.
- **Нативной интеграции с Bitrix24 нет** — синхронизация через n8n/Zapier ([breakcold.com](https://www.breakcold.com), [n8n.io](https://n8n.io)).

### 1.6. Woodpecker — детали

- **Секвенции**: follow-up-автоматизации, condition-based campaigns.
- **A/B**: subject + body; follow-up после A/B-шага уходит с прежней темой как «Re:», оставаясь в нити.
- **Ответы**: автоопределение auto-reply (OOO) → автоостановка follow-up'ов, ручной resume.
- **API**: REST — аккаунты, списки проспектов, кампании, отчёты, ящики, агентские аккаунты; webhooks ([developers.woodpecker.co](https://developers.woodpecker.co)).
- **Цены**: **за ящик, не за место**: ~$29/мес за email-аккаунт.

### 1.7. Outreach.io / Salesloft (кратко)

- Enterprise-класс: детальные правила, расписания, права, volume controls, аналитика, роли/комплаенс. Цены: Salesloft ~$75–165/юзер/мес; Outreach — не публикуется ([avoma.com](https://www.avoma.com)).

---

## ЧАСТЬ 2. Open-source и self-hosted альтернативы

| Проект | Что это | Стек | Зрелость | Пригодность для 1-to-1 секвенций | Вердикт |
|---|---|---|---|---|---|
| **Klay** | Коммерческий SaaS cold email + LinkedIn | закрытый код | n/a | n/a | **Не open source** — за основу взять нельзя |
| **Listmonk** | Self-hosted рассыльщик newsletter | Go + Vue + Postgres, AGPLv3 | Высокая | Нет: broadcast + transactional; **нет drip-секвенций с задержками/условиями**, нет warmup/ротации/детекта ответов ([listmonk.app/docs](https://listmonk.app/docs/)) | **Не основа**; максимум — движок доставки в связке с n8n |
| **Mautic** | Open-source маркетинг-автоматизация | PHP (Symfony) + MySQL | Высокая | Частично: Campaign Builder с conditions/decisions/actions, сегменты — nurture по существующей базе, не cold: нет ротации ящиков, warmup, reply-классификации ([docs.mautic.org](https://docs.mautic.org)) | Можно для nurture по базе CRM, но не для cold с пулом ящиков |
| **Parcelvoy** | Self-hosted мультиканальный маркетинг | TypeScript, MIT | **Заархивирован в феврале 2026** | Нет: lifecycle по событиям известных юзеров, не outbound ([github.com/parcelvoy/platform](https://github.com/parcelvoy/platform)) | **Нет** |
| **EmaReach** | Open-source cold email платформа: multi-step секвенции, A/B, warmup (LLM-нити), SPF/DKIM/DMARC-проверки, per-inbox лимиты, open/click трекинг | Python 3.12 + FastAPI + MongoDB, Next.js, Docker, MIT | **Очень ранняя: 1 коммит, 6 звёзд** ([github.com/ritik-prog/emareach](https://github.com/ritik-prog/emareach)) | Функционально — да, архитектурно близко к Instantly | **Не брать в продакшн**; полезен как референс архитектуры |
| Прочее | GitHub topics cold-emails / sales-automation | разные | низкая-средняя | точечные скрипты | Референсы |

**Вывод:** зрелого self-hosted аналога Instantly/Smartlead (ротация ящиков, warmup-сеть, reply-классификация) в open source **нет**. Всё специфичное для cold придётся строить самому. EmaReach — единственный найденный «open-source Instantly-клон», но с 1 коммитом не годится как основа.

---

## ЧАСТЬ 3. Bitrix24: встроенные возможности и REST API

### 3.1. Встроенные средства (облако)

- **CRM-маркетинг**: динамические/статические **сегменты** по базе CRM → рассылки по Email/SMS/мессенджерам; редактор писем; **триггерные цепочки** (триггер по стадии сделки, бездействию); SMS через локальных провайдеров ([helpdesk.bitrix24.ru](https://helpdesk.bitrix24.ru)).
- **Лимиты облака**: **до 1 000 писем в день на портал**; сверх — очередь на следующий день; лимиты в час/сутки/месяц по тарифу ([helpdesk.bitrix24.ru](https://helpdesk.bitrix24.ru)). Для доставляемости рекомендуют внешний SMTP-релей; в коробке жёстких облачных лимитов нет.
- **Cold outreach штатно вести нельзя**: CRM-маркетинг — маркетинг по существующей базе (newsletter/triggered), нет ротации ящиков, warmup, секвенций с детектом ответов и автостопом, per-step аналитики. Отдельные письма вручную из карточек — можно, автоматизации — нет.

### 3.2. REST API — проверенные методы (apidocs.bitrix24.com)

Старые страницы dev.1c-bitrix.ru 301-редиректят на [apidocs.bitrix24.com](https://apidocs.bitrix24.com).

**crm.activity.add** ([документация](https://apidocs.bitrix24.com/api-reference/crm/timeline/activities/activity-base/crm-activity-add.html)):
- Scope `crm`. Устарел с CRM 22.1350.0 (рекомендуют `crm.activity.todo.add`), но только через него создаются дела типа **Email**.
- Обязательные: `OWNER_ID`, `OWNER_TYPE_ID` (сделка=2, контакт=3), `TYPE_ID` (Email), `COMMUNICATIONS` (email получателя), `RESPONSIBLE_ID`.
- Email-специфика: `DIRECTION` (1/2); чтобы письмо **не отправлялось** Битриксом — `DIRECTION=2`, `COMPLETED='N'`; `SETTINGS.DISABLE_SENDING_MESSAGE_COPY='Y'`; завершение требует `crm.activity.update`.
- Вывод: логировать исходящее письмо в таймлайн как email-активность с маркером «не отправлять средствами Б24».

**crm.timeline.comment.add** ([документация](https://apidocs.bitrix24.com/api-reference/crm/timeline/comments/crm-timeline-comment-add.html)):
- Комментарий в таймлайне: `ENTITY_TYPE` (lead/deal/contact/company), `ENTITY_ID`, `COMMENT`, `FILES` (base64). Идеален для записи «пришёл ответ, классифицирован как Interested».

**crm.contact.list / crm.deal**: стандартные list-методы с фильтрами для выборки базы и сегментации.

**bizproc.robot.add** ([документация](https://apidocs.bitrix24.com/api-reference/bizproc/bizproc-robot/bizproc-robot-add.html)):
- Регистрирует **робота от приложения** для CRM-автоматизации и БП. Только в контексте установленного приложения, scope `bizproc`, права админа.
- Параметры: `CODE`, `HANDLER` (HTTPS-URL), `PROPERTIES` (bool/date/datetime/double/file/int/select/string/text/user), `RETURN_PROPERTIES` + `USE_SUBSCRIPTION=Y/N` (асинхронный результат через `bizproc.event.send`), `DOCUMENT_TYPE` (`['crm','CCrmDocumentDeal','DEAL']`, `CCrmDocumentLead`, смарт-процессы).
- Ключевой способ встроить «отправить лид в секвенцию аутрича» прямо в роботы CRM.

**bizproc.activity.add**: кастомная активность для шаблонов БП (более широкие `DOCUMENT_TYPE`, включая Списки). Роботы и активности делят один реестр (`ERROR_ACTIVITY_ALREADY_INSTALLED` общая).

**События (webhooks)** ([ONCRMCONTACTADD](https://apidocs.bitrix24.com/api-reference/crm/contacts/events/on-crm-contact-add.html)):
- Семейство: `ONCRMCONTACTADD/UPDATE/DELETE`, `ONCRMDEALADD/…`, `ONCRMACTIVITYADD/…`, `ONCRMTIMELINECOMMENTADD/…`.
- Payload POST: `event`, `event_handler_id`, `data.FIELDS.ID`, `ts`, `auth`. Токены могут отсутствовать — проверять.
- Подписка: `event.bind` или **исходящий вебхук** в UI Б24 (проще для самописного сервиса).

**Лимиты REST**: ~2 запроса/сек (`QUERY_LIMIT_EXCEEDED`); `batch` ≤ 50 подзапросов; с REST 22.0.0 — модель «стоимости» запросов по времени.

### 3.3. Нативные интеграции SaaS ↔ Б24

| Инструмент | Нативная интеграция с Б24 | Обходной путь |
|---|---|---|
| Reply.io | **Нет** | n8n, Zapier/Make |
| Lemlist | **Нет** | Zapier |
| Instantly | **Нет** | Webhooks API v2 + REST Б24, Zapier |
| Smartlead | **Нет** (нативная — HubSpot) | Webhooks + REST Б24 |

Путь «готовый инструмент + Б24» — всегда **webhook инструмента → свой glue-сервис → REST Б24** (или iPaaS-прослойка).

---

## ЧАСТЬ 4. Выводы: «готовый инструмент + Б24» vs «свой сервис»

### 4.1. Сравнительная таблица

| Критерий | Готовый инструмент + glue | Собственный сервис + Б24 |
|---|---|---|
| Секвенции, расписания, stop-правила | Из коробки | Строить: движок шагов (email/wait/condition), планировщик с окнами/таймзонами/рабочими днями |
| Ротация ящиков, лимиты | Из коробки | Строить: пул IMAP/SMTP + Graph, per-mailbox daily caps |
| Warmup | Из коробки — большая warmup-сеть | **Главная проблема**: нужна сеть ящиков |
| Reply-детект и классификация | Из коробки: AI-теггинг, автостоп | Строить: IMAP IDLE/Graph subscriptions + LLM-классификация |
| Персонализация | Merge-теги, Liquid, AI-строки | Строить: шаблонизатор + LLM-генерация |
| Интеграция с Б24 | **Нет нативной ни у одного**; webhook → glue → REST | Нативная глубина любая: события, роботы, email-дела и комментарии в таймлайне |
| Риски | Зависимость от вендора; API-лимиты вендора; нет контроля логики | Deliverability-риски, разработка, поддержка |
| Time-to-market | Дни–недели | Месяцы (MVP ~2–4 месяца) |
| Стоимость | $33–97/мес + glue-разработка | VPS + домены + ящики + разработка |

### 4.2. Практические выводы

1. **Паттерн интеграции одинаков**: (а) выборка базы — `crm.contact.list`/`crm.deal.list`; (б) триггеры — исходящий вебхук Б24 или робот `bizproc.robot.add`; (в) лог отправок — `crm.activity.add` (email-дело, чтобы Б24 не отправлял сам); (г) ответы — `crm.timeline.comment.add` + смена стадии/тега.
2. **Если объём и скорость важнее контроля** — Instantly или Smartlead + тонкий glue-сервис на webhook'ах.
3. **Если строить своё** — архитектурный минимум: (1) оркестратор секвенций с шагами email/wait/condition и расписанием по таймзоне; (2) пул ящиков с ротацией и per-mailbox лимитами; (3) инбокс-листенер (IMAP IDLE / Graph subscriptions) + LLM-классификация ответов; (4) B24-коннектор поверх REST (2 req/s, batch ≤ 50); (5) warmup — build (медленно) или партнёрский сервис. Референсы: EmaReach (архитектура), Listmonk (доставка), Mautic (модель кампаний).
4. **Штатный CRM-маркетинг Б24 для cold не годится** (1 000 писем/день, нет ротации/warmup/детекта), но частично пригоден для nurture по существующей базе.

### 4.3. Ключевые источники

- Instantly: [instantly.ai](https://instantly.ai), [developer.instantly.ai](https://developer.instantly.ai/), [help.instantly.ai](https://help.instantly.ai)
- Smartlead: [api.smartlead.ai](https://api.smartlead.ai/introduction), [smartlead.ai](https://www.smartlead.ai)
- Lemlist: [developer.lemlist.com](https://developer.lemlist.com), [help.lemlist.com](https://help.lemlist.com), [academy.lemlist.com](https://academy.lemlist.com)
- Reply.io: [docs.reply.io](https://docs.reply.io)
- Woodpecker: [developers.woodpecker.co](https://developers.woodpecker.co)
- Open source: [emareach](https://github.com/ritik-prog/emareach), [parcelvoy](https://github.com/parcelvoy/platform), [listmonk](https://listmonk.app/docs/), [mautic](https://docs.mautic.org), [topic cold-emails](https://github.com/topics/cold-emails)
- Bitrix24: [crm.activity.add](https://apidocs.bitrix24.com/api-reference/crm/timeline/activities/activity-base/crm-activity-add.html), [crm.timeline.comment.add](https://apidocs.bitrix24.com/api-reference/crm/timeline/comments/crm-timeline-comment-add.html), [bizproc.robot.add](https://apidocs.bitrix24.com/api-reference/bizproc/bizproc-robot/bizproc-robot-add.html), [ONCRMCONTACTADD](https://apidocs.bitrix24.com/api-reference/crm/contacts/events/on-crm-contact-add.html), [helpdesk.bitrix24.ru](https://helpdesk.bitrix24.ru)

**Оговорки о непроверенном**: spintax в Lemlist, A/B в Reply.io, per-step аналитика Instantly в официальном хелпе, точные лимиты писем Б24 «в час/месяц» по тарифам — упомянуты в обзорах, но постранично в официальных доках не верифицированы.
