"""Local access to the same authenticated application used by the browser.

No listening socket or second implementation of review rules. Filesystem access
to a private workspace is the CLI's trust boundary; browser sessions remain separate.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from starlette.types import Message, Scope

from .contracts.records import canonical, loads
from .web import runtime


class Application:
    def __init__(self, root: Path, providers: Path) -> None:
        self.root, self.providers = root.resolve(), providers.resolve()
        info = self.root.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("CLI requires an owned private workspace (mode 0700)")
        with runtime.connection(self.root) as db:
            accounts = db.execute("SELECT name FROM accounts ORDER BY name").fetchall()
        if len(accounts) != 1:
            raise ValueError("Local CLI requires exactly one workspace curator")
        self.actor = accounts[0][0]

    def request(self, method: str, path: str, body: object = None) -> tuple[bytes, str]:
        return asyncio.run(self._request(method.upper(), path, body))

    async def _request(self, method: str, path: str, body: object) -> tuple[bytes, str]:
        from .web.api import create_app

        if method not in {"GET", "POST"}:
            raise ValueError("Use GET or POST")
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or ".." in parsed.path:
            raise ValueError("Use a local application path, not a URL")
        route = "/api/" + parsed.path.removeprefix("/api/").lstrip("/")
        if route in {"/api/login", "/api/logout", "/api/auth-state", "/api/session"}:
            raise ValueError("Browser authentication is not a CLI operation")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        hashed = hashlib.sha256(token.encode()).hexdigest()
        origin = "http://127.0.0.1:18765"
        app = create_app(self.root, self.providers, origin, dev=True)
        payload = canonical(body if body is not None else {}).encode()
        headers = [
            (b"host", b"127.0.0.1:18765"),
            (b"origin", origin.encode()),
            (b"content-type", b"application/json"),
            (b"x-csrf-token", csrf.encode()),
            (b"cookie", ("genesis_session=" + token).encode()),
        ]
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": route,
            "raw_path": route.encode(),
            "query_string": parsed.query.encode(),
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 0),
            "server": ("127.0.0.1", 18765),
        }
        messages: list[Message] = []
        sent = False
        done = asyncio.Event()

        async def receive() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await done.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                done.set()

        with runtime.connection(self.root) as db:
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (hashed, csrf, self.actor, time.time() + 300),
            )
        try:
            await app(scope, receive, send)
        finally:
            with runtime.connection(self.root) as db:
                db.execute("DELETE FROM sessions WHERE token=?", (hashed,))
        start = next(m for m in messages if m["type"] == "http.response.start")
        content = b"".join(
            m.get("body", b"") for m in messages if m["type"] == "http.response.body"
        )
        media = dict(start["headers"]).get(b"content-type", b"application/octet-stream").decode()
        if start["status"] >= 400:
            value = loads(content.decode())
            error = value.get("error", value.get("detail", "Request failed"))
            raise ValueError(f"Application {start['status']}: {error}")
        return content, media
