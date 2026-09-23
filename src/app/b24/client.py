import asyncio
import json
import time
import urllib.parse
from typing import Any, Awaitable, Callable


class Bitrix24Error(Exception):
    def __init__(self, code: str, description: str = ""):
        self.code = code
        self.description = description
        super().__init__(f"{code}: {description}")


class Bitrix24Client:
    """Тонкий клиент REST Битрикс24: глобальный троттлинг ≤2 req/s, пагинация, batch ≤50."""

    _gap = 0.55  # сек между запросами (чуть меньше 2 req/s)
    _lock: asyncio.Lock | None = None
    _last_ts = 0.0

    def __init__(self, call_fn: Callable[[str, dict], Awaitable[dict]]):
        self._call_fn = call_fn

    @classmethod
    def _throttle_lock(cls) -> asyncio.Lock:
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    def reset_throttle(cls) -> None:
        """Для тестов: сброс общего состояния троттлера."""
        cls._lock = None
        cls._last_ts = 0.0

    async def _raw(self, method: str, params: dict) -> dict:
        async with self._throttle_lock():
            now = time.monotonic()
            wait = self._gap - (now - type(self)._last_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            type(self)._last_ts = time.monotonic()
        resp = await self._call_fn(method, params)
        if isinstance(resp, dict) and "error" in resp:
            raise Bitrix24Error(resp["error"], resp.get("error_description", ""))
        return resp

    async def call(self, method: str, params: dict | None = None) -> Any:
        return (await self._raw(method, params or {})).get("result")

    async def call_all(self, method: str, params: dict, key: str = "result") -> list:
        """Списочный метод с пагинацией: страницы по 50, идём за resp['next']."""
        out: list = []
        start = 0
        while True:
            resp = await self._raw(method, {**params, "start": start})
            chunk = resp.get(key)
            if isinstance(chunk, list):
                out.extend(chunk)
            elif chunk is not None:
                out.append(chunk)
            nxt = resp.get("next")
            if nxt is None:
                break
            start = int(nxt)
        return out

    async def batch(self, cmds: list[tuple[str, dict]]) -> list[Any]:
        """Batch-обёртка: чанки по 50 подзапросов, параметры — JSON в query-string."""
        results: list[Any] = []
        for i in range(0, len(cmds), 50):
            chunk = cmds[i : i + 50]
            payload = {
                f"cmd{j}": f"{method}?{urllib.parse.quote(json.dumps(params, ensure_ascii=False), safe='')}"
                for j, (method, params) in enumerate(chunk)
            }
            resp = await self._raw("batch", payload)
            result = resp.get("result") or {}
            results.extend(result.get(f"cmd{j}") for j in range(len(chunk)))
        return results
