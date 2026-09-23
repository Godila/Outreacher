import asyncio
import json
import urllib.parse

import pytest

from app.b24.client import Bitrix24Client, Bitrix24Error


async def test_call_throttles_to_2rps():
    Bitrix24Client.reset_throttle()
    calls: list[str] = []

    async def fake(method: str, params: dict) -> dict:
        calls.append(method)
        return {"result": 1}

    client = Bitrix24Client(fake)
    t0 = asyncio.get_event_loop().time()
    await client.call("a")
    await client.call("b")
    elapsed = asyncio.get_event_loop().time() - t0
    assert calls == ["a", "b"]
    assert elapsed >= 0.5  # второй запрос ждал троттл-гэп


async def test_call_all_paginates_over_next():
    Bitrix24Client.reset_throttle()
    pages = [
        {"result": [{"id": i} for i in range(50)], "next": 50},
        {"result": [{"id": 50}], "next": None},
    ]

    async def fake(method: str, params: dict) -> dict:
        return pages.pop(0)

    client = Bitrix24Client(fake)
    items = await client.call_all("crm.contact.list", {})
    assert len(items) == 51
    assert items[-1]["id"] == 50


async def test_error_raises_b24error():
    Bitrix24Client.reset_throttle()

    async def fake(method: str, params: dict) -> dict:
        return {"error": "QUERY_LIMIT_EXCEEDED", "error_description": "too many"}

    client = Bitrix24Client(fake)
    with pytest.raises(Bitrix24Error) as excinfo:
        await client.call("x")
    assert excinfo.value.code == "QUERY_LIMIT_EXCEEDED"


async def test_batch_chunks_and_payload():
    Bitrix24Client.reset_throttle()
    captured: list[dict] = []

    async def fake(method: str, params: dict) -> dict:
        assert method == "batch"
        captured.append(params)
        return {"result": {f"cmd{j}": j for j in range(len(params))}}

    client = Bitrix24Client(fake)
    cmds = [(f"m{i}", {"ID": i}) for i in range(60)]
    results = await client.batch(cmds)
    assert len(results) == 60
    assert len(captured) == 2  # 50 + 10
    # параметры сериализованы JSON-строкой в query
    first_cmd = captured[0]["cmd0"]
    method, _, query = first_cmd.partition("?")
    assert method == "m0"
    assert json.loads(urllib.parse.unquote(query)) == {"ID": 0}
