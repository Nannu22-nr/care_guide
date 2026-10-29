"""Simple API-key auth.

The key may be sent as the `X-API-Key` header (used by fetch/curl) or as an
`api_key` query parameter (needed for <img>/<iframe>/<a download> previews,
which cannot set custom headers).

If no API_KEY is configured, auth is disabled (development mode).
"""
import secrets

from fastapi import Header, HTTPException, Query

import config


async def require_api_key(
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
):
    expected = config.API_KEY
    if not expected:
        return
    supplied = x_api_key or api_key or ""
    if not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Invalid or missing API key")
