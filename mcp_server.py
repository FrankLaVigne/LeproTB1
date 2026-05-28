#!/usr/bin/env python3
"""Networked MCP server for Lepro lights (streamable-HTTP, bearer-token auth)."""

from __future__ import annotations

import hmac


class BearerAuthMiddleware:
    """ASGI middleware enforcing `Authorization: Bearer <token>` on HTTP requests."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        expected = f"Bearer {self.token}"
        if not (auth and hmac.compare_digest(auth, expected)):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await self.app(scope, receive, send)
