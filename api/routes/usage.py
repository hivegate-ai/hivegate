"""Read-only usage & cost API: what every model call through the gateway cost.

Auth: an API key with the `usage:read` scope (or `admin`), or the X-Admin-Secret
header. A key whose only scope is `usage:read` can read this and nothing else - the
/v2 routes refuse it (api/services/auth.reject_usage_only_keys).

Rows are written by api/services/usage.py; prices come from agents/pricing.py.
"""

import hmac
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from sqlalchemy.orm import Session

from agents import pricing
from api.services.auth import admin_secret_header, get_admin_secret, get_optional_api_key, is_auth_disabled
from db import usage_queries as q
from db.api_key_crud import get_api_key_scopes
from db.db_models import ApiKeyDB
from db.session import get_db

USAGE_SCOPE = "usage:read"

Window = Literal["1h", "6h", "24h", "7d", "30d"]
GroupBy = Literal["agent", "tenant", "model", "api_key", "user", "status"]


async def require_usage_reader(
    api_key: Optional[ApiKeyDB] = Depends(get_optional_api_key),
    admin_secret: Optional[str] = Security(admin_secret_header),
) -> None:
    expected = get_admin_secret()
    if admin_secret and expected and hmac.compare_digest(admin_secret, expected):
        return
    if api_key is not None:
        scopes = get_api_key_scopes(api_key)
        if USAGE_SCOPE in scopes or "admin" in scopes:
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"API key lacks the '{USAGE_SCOPE}' scope")
    if is_auth_disabled():
        return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Provide an X-API-Key with usage:read, or X-Admin-Secret")


usage_router = APIRouter(prefix="/usage", tags=["usage"], dependencies=[Depends(require_usage_reader)])


@usage_router.get("/live")
def live(
    after_id: int = Query(0, ge=0, description="Return calls with id greater than this; 0 = the latest calls"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """New calls since `after_id`, oldest first - poll it with the last id you saw."""
    rows = q.live(db, after_id, limit)
    return {"calls": rows, "last_id": rows[-1]["id"] if rows else after_id}


@usage_router.get("/summary")
def summary(
    window: Window = "24h",
    group_by: GroupBy = "agent",
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Totals for the window, the breakdown by `group_by`, and cost per model type
    (main model vs memory model vs compression model ...)."""
    return {
        "window": window,
        "group_by": group_by,
        "totals": q.totals(db, window),
        "groups": q.summary(db, window, group_by, limit),
        "model_types": q.model_type_split(db, window),
        "prices_version": pricing.PRICES_VERSION,
    }


@usage_router.get("/timeseries")
def timeseries(
    window: Window = "24h",
    bucket: Literal["5m", "15m", "1h", "1d"] = "1h",
    group_by: GroupBy = "agent",
    top: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """Cost per time bucket per group (top `top` groups by cost, the rest as '(other)')."""
    return {
        "window": window,
        "bucket": bucket,
        "group_by": group_by,
        "points": q.timeseries(db, window, bucket, group_by, top),
    }


@usage_router.get("/calls")
def calls(
    window: Window = "24h",
    order: Literal["cost", "recent"] = "cost",
    agent: Optional[str] = None,
    tenant: Optional[str] = None,
    status_: Optional[str] = Query(None, alias="status"),
    min_cost: Optional[float] = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Individual calls - the most expensive first by default - with session and run ids
    for drill-down."""
    return {"calls": q.calls(db, window, order, agent, tenant, status_, min_cost, limit)}


@usage_router.get("/prices")
def prices():
    """The price table in use (USD per million tokens), for reference on the dashboard."""
    table = {}
    for model_id in sorted(pricing.PRICES):
        p = pricing.price_for(model_id)
        if p is not None:
            table[model_id] = {
                "input": float(p.input),
                "output": float(p.output),
                "cache_read": float(p.cache_read) if p.cache_read is not None else None,
                "cache_write": float(p.cache_write) if p.cache_write is not None else None,
            }
    return {"version": pricing.PRICES_VERSION, "prices": table}
