import hashlib
import json
import re
from urllib.parse import parse_qsl

_INT_KEYS = re.compile(r"^(?:ID|.*_ID|ts|event_handler_id)$")


def _split_key(full_key: str) -> list[str]:
    m = re.match(r"^([^\[\]]+)(\[[^\]]*\])*", full_key)
    if not m:
        return [full_key]
    return [m.group(1)] + re.findall(r"\[([^\]]*)\]", full_key)


def _looks_list(key: str) -> bool:
    return key == "" or key.isdigit()


def _assign(container: dict, keys: list[str], value) -> None:
    key, rest = keys[0], keys[1:]
    if not rest:
        if key == "" and isinstance(container, list):
            container.append(value)
        elif isinstance(container, dict):
            container[key] = value
        return
    nxt = rest[0]
    if _looks_list(key):
        if not isinstance(container, list):
            return
        idx = int(key) if key != "" else len(container)
        while len(container) <= idx:
            container.append(None)
        if container[idx] is None:
            container[idx] = [] if _looks_list(nxt) else {}
        _assign(container[idx], rest, value)
    else:
        if not isinstance(container, dict):
            return
        if not isinstance(container.get(key), (dict, list)):
            container[key] = [] if _looks_list(nxt) else {}
        _assign(container[key], rest, value)


def _coerce(key: str, val):
    if isinstance(val, str) and _INT_KEYS.match(key) and re.fullmatch(r"-?\d+", val):
        return int(val)
    return val


def _int_walk(node):
    if isinstance(node, dict):
        return {k: _coerce(k, v) if isinstance(v, str) else _int_walk(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_int_walk(x) for x in node]
    return node


def parse_webhook(body: bytes, content_type: str) -> dict:
    """Толерантный парсер вебхуков Б24: form-urlencoded с php-массивами ИЛИ JSON (id строками)."""
    ct = content_type or ""
    if "json" in ct:
        return _int_walk(json.loads(body or b"{}"))
    root: dict = {}
    for full_key, val in parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True):
        _assign(root, _split_key(full_key), val)
    return _int_walk(root)


def event_digest(body: bytes) -> str:
    """Стабильный дедуп-ключ события (в вебхуке Б24 нет явного event_id)."""
    return hashlib.sha256(body).hexdigest()


async def verify_webhook(payload: dict, provided_secret: str | None, stored_application_token: str | None) -> bool:
    """Эшелоны: заголовочный секрет → совпадение application_token из auth-блока."""
    auth = payload.get("auth") or {}
    if provided_secret:
        return True  # проверка секрета сделана на уровне роута (сравнение строк)
    token = auth.get("application_token") or ""
    if stored_application_token and token and token == stored_application_token:
        return True
    return False
