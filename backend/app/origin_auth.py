"""Shared-secret check for requests that must come through the Cloudflare proxy.

The API is exposed on a public Lambda Function URL. The Cloudflare Pages Function that fronts
it adds ``X-Origin-Auth: <ORIGIN_SECRET>``; anything else hitting the Function URL directly is
rejected here. Only active when the ``ORIGIN_SECRET`` environment variable is set.
"""

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

HEADER = b"x-origin-auth"
EXEMPT_PATHS = {"/api/v1/health"}
# EventBridge cannot send X-Origin-Auth; these routes check INTERNAL_JOB_SECRET themselves.
EXEMPT_PREFIXES = ("/api/v1/internal/jobs/",)


class OriginAuthMiddleware:
    def __init__(self, app: ASGIApp, secret: str) -> None:
        self.app = app
        self._secret = secret.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or (
            scope.get("path") in EXEMPT_PATHS
            or str(scope.get("path", "")).startswith(EXEMPT_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return
        supplied = next((v for k, v in scope["headers"] if k == HEADER), b"")
        if hmac.compare_digest(supplied, self._secret):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http":
            await JSONResponse({"detail": "Forbidden"}, status_code=403)(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})
