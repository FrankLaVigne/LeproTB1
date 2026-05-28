import pytest
from mcp_server import BearerAuthMiddleware


class _SpyApp:
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _scope(auth=None):
    headers = [(b"authorization", auth.encode())] if auth else []
    return {"type": "http", "headers": headers}


async def _drain():
    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request"}

    return sent, send, receive


@pytest.mark.asyncio
async def test_valid_token_passes_through():
    app = _SpyApp()
    mw = BearerAuthMiddleware(app, token="secret")
    sent, send, receive = await _drain()
    await mw(_scope("Bearer secret"), receive, send)
    assert app.called is True


@pytest.mark.asyncio
async def test_missing_token_returns_401():
    app = _SpyApp()
    mw = BearerAuthMiddleware(app, token="secret")
    sent, send, receive = await _drain()
    await mw(_scope(None), receive, send)
    assert app.called is False
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_wrong_token_returns_401():
    app = _SpyApp()
    mw = BearerAuthMiddleware(app, token="secret")
    sent, send, receive = await _drain()
    await mw(_scope("Bearer nope"), receive, send)
    assert sent[0]["status"] == 401
