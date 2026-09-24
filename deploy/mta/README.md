# VM-MTA deployment kit (docker-mailserver)

Выделенный почтовый сервер кампаний: `news.adel-hat.ru` (From-домен писем), hostname `mta-news.adel-factory.ru`.

## 1. Предпосылки (на VM)

- Ubuntu 24.04, docker + compose plugin (как на VM-APP)
- Тикеты хостера ВЫПОЛНЕНЫ: исходящий 25/tcp открыт, PTR `<IP>` → `mta-news.adel-factory.ru`
- DNS: A `mta-news.adel-factory.ru` → `<IP>`; зона `adel-hat.ru` готова принять записи из п.5
- IP проверен: https://mxtoolbox.com/blacklists.aspx и https://check.spamhaus.org

## 2. Firewall (ufw)

```bash
ufw default deny incoming && ufw allow 22/tcp
ufw allow 25/tcp            # входящие ответы/bounce + исходящая отправка
ufw allow from 45.84.226.80 to any port 587,993 proto tcp  # только VM-APP
ufw enable
```

## 3. Файлы кита

`docker-compose.yml` и `mailserver.env` рядом с этим файлом. Запуск:

```bash
mkdir -p /opt/mta && cp docker-compose.yml mailserver.env /opt/mta/ && cd /opt/mta
mkdir -p docker-data/dms/config/{postfix,opendkim} letsencrypt
# TLS: certbot на хосте (80-й НЕ нужен, standalone в контейнере не используем):
docker run --rm -p 80:80 -v /opt/mta/letsencrypt:/etc/letsencrypt certbot/certbot \
  certonly --standalone -d mta-news.adel-factory.ru --agree-tos -m admin@adel-hat.ru -n
docker compose up -d
```

## 4. Ящики + DKIM

```bash
docker compose exec mailserver setup email add anna@news.adel-hat.ru 'PAROL1'
docker compose exec mailserver setup email add oleg@news.adel-hat.ru 'PAROL2'
docker compose exec mailserver setup config dkim keylength 2048   # selector: mail
cat docker-data/dms/config/opendkim/keys/news.adel-hat.ru/mail.txt  # -> DNS (п.5)
```

## 5. DNS-записи (зона `adel-hat.ru`, панель Beget)

| Имя | Тип | Значение |
|---|---|---|
| `news` | MX 10 | `mta-news.adel-factory.ru.` |
| `news` | TXT | `v=spf1 ip4:<IP-MTA> -all` |
| `mail._domainkey.news` | TXT | из `mail.txt` (п.4), в одну строку |
| `_dmarc.news` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@adel-hat.ru; adkim=s; aspf=s` |
| `_dmarc` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@adel-hat.ru` |

## 6. Проверки

1. `docker compose exec mailserver setup email list` — ящики на месте.
2. mail-tester.com с `anna@news.adel-hat.ru` — цель 10/10 (SPF/DKIM/DMARC/PTR pass).
3. Постмастеры: Mail.ru (домен + FBL `Feedback-ID`), Яндекс.
4. В UI Outreacher → «Отправители»: SMTP `mta-news.adel-factory.ru:465`, IMAP `:993`, лимит 50.

## 7. Прогрев

5/день → +5 каждые 2–3 дня → 30–50/день на ящик к концу 2-й недели. Первые письма НЕ в период прогрева не слать в бой.

## 8. Обслуживание

- Логи: `docker compose logs -f mailserver`
- Обновление: `docker compose pull && docker compose up -d`
- Бэкап: снапшот VPS раз в неделю; Maildir не критичен (сервис переносит ответы в свою БД)
- Сертификат: `certbot renew` в cron + `docker compose restart mailserver` (hooks)
