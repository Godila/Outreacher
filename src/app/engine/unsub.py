import hashlib
import hmac


def make_token(enrollment_id: int, email: str, secret: str) -> str:
    mac = hmac.new(secret.encode(), f"{enrollment_id}:{email}".encode(), hashlib.sha256).hexdigest()[:16]
    return f"{enrollment_id}.{mac}"


def parse_token(token: str, email: str, secret: str) -> int | None:
    """Возвращает enrollment_id, если подпись сходится с email; иначе None."""
    eid, _, mac = token.partition(".")
    if not eid.isdigit() or not mac:
        return None
    expected = hmac.new(secret.encode(), f"{int(eid)}:{email}".encode(), hashlib.sha256).hexdigest()[:16]
    return int(eid) if hmac.compare_digest(mac, expected) else None
