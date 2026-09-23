from app.b24.webhooks import parse_webhook


def test_form_urlencoded_php_arrays():
    body = b"event=ONAPPINSTALL&auth[access_token]=tok&auth[refresh_token]=ref&auth[domain]=x.bitrix24.ru&data[FIELDS][ID]=17&data[FIELDS][ts]=1750000000"
    payload = parse_webhook(body, "application/x-www-form-urlencoded")
    assert payload["event"] == "ONAPPINSTALL"
    assert payload["auth"]["access_token"] == "tok"
    assert payload["data"]["FIELDS"]["ID"] == 17  # int-коэрсинг
    assert payload["data"]["FIELDS"]["ts"] == 1750000000


def test_token_keys_not_coerced():
    body = b"auth[application_token]=12345678901234567890&auth[member_id]=abc123"
    payload = parse_webhook(body, "application/x-www-form-urlencoded")
    # application_token/member_id остаются строками
    assert payload["auth"]["application_token"] == "12345678901234567890"
    assert payload["auth"]["member_id"] == "abc123"


def test_json_with_string_ids():
    import json

    body = json.dumps(
        {"event": "ONCRMCONTACTADD", "ts": "1750000000", "data": {"FIELDS": {"ID": "42"}}}
    ).encode()
    payload = parse_webhook(body, "application/json")
    assert payload["data"]["FIELDS"]["ID"] == 42
    assert payload["ts"] == 1750000000


def test_numeric_lists():
    body = b"arr[]=1&arr[]=2&m[0][x]=a&m[1][x]=b"
    payload = parse_webhook(body, "application/x-www-form-urlencoded")
    assert payload["arr"] == ["1", "2"]
    assert payload["m"][0]["x"] == "a"
    assert payload["m"][1]["x"] == "b"
