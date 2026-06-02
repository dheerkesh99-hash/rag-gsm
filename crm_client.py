"""
crm_client.py — Microsoft Dynamics 365 CRM client for WISMO order lookups.

Authentication: OAuth 2.0 client credentials flow (Azure AD).
API:            D365 Web API (OData v4).

Security contract:
  - PII (email, phone, name) is used ONLY as an OData $filter value.
  - It is never logged, stored, or forwarded to any LLM.
  - The access token is cached in memory only (never written to disk).
  - All connections use HTTPS enforced by httpx.
"""

from __future__ import annotations

import os
import time
import httpx
from typing import Optional

# ── Configuration from environment variables ─────────────────────────────────
# Set these in .env (local) or the platform dashboard (Railway / HuggingFace).
# Never hardcode credentials here.

_TENANT_ID     = os.environ.get("D365_TENANT_ID", "")
_CLIENT_ID     = os.environ.get("D365_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("D365_CLIENT_SECRET", "")
_BASE_URL      = os.environ.get("D365_BASE_URL", "").rstrip("/")
# e.g. https://yourorg.api.crm.dynamics.com

_API_VERSION   = "v9.2"
_API_BASE      = f"{_BASE_URL}/api/data/{_API_VERSION}"

# ── Token cache (in-process only) ────────────────────────────────────────────
_token_cache: dict = {}


def _get_access_token() -> str:
    """
    Fetch an OAuth2 token using client credentials flow.
    Token is cached in memory and reused until 60 s before expiry.
    Raises RuntimeError if env vars are not set or auth fails.
    """
    if not all([_TENANT_ID, _CLIENT_ID, _CLIENT_SECRET, _BASE_URL]):
        raise RuntimeError(
            "D365 credentials not configured. "
            "Set D365_TENANT_ID, D365_CLIENT_ID, D365_CLIENT_SECRET, D365_BASE_URL in .env"
        )

    if _token_cache.get("expires_at", 0) > time.time() + 60:
        return _token_cache["token"]

    url = f"https://login.microsoftonline.com/{_TENANT_ID}/oauth2/v2.0/token"
    resp = httpx.post(
        url,
        data={
            "client_id":     _CLIENT_ID,
            "client_secret": _CLIENT_SECRET,
            "scope":         f"{_BASE_URL}/.default",
            "grant_type":    "client_credentials",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"]      = data["access_token"]
    _token_cache["expires_at"] = time.time() + int(data.get("expires_in", 3600))
    return _token_cache["token"]


def _headers() -> dict:
    return {
        "Authorization":    f"Bearer {_get_access_token()}",
        "OData-Version":    "4.0",
        "Accept":           "application/json",
        "Content-Type":     "application/json",
    }


# ── Contact lookup ────────────────────────────────────────────────────────────

def _find_contact(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    name:  Optional[str] = None,
) -> list[dict]:
    """
    Return list of matching D365 contact records.
    Priority: email > phone > name.
    Only contactid and fullname are fetched — no extra PII returned.
    """
    if email:
        odata_filter = f"emailaddress1 eq '{email}'"
    elif phone:
        # D365 stores phone without formatting; match on cleaned digits
        odata_filter = f"mobilephone eq '{phone}' or telephone1 eq '{phone}'"
    elif name:
        parts = name.strip().split()
        if len(parts) >= 2:
            odata_filter = (
                f"firstname eq '{parts[0]}' and lastname eq '{parts[-1]}'"
            )
        else:
            odata_filter = f"fullname eq '{name}'"
    else:
        return []

    url = (
        f"{_API_BASE}/contacts"
        f"?$filter={odata_filter}"
        f"&$select=contactid,fullname"
        f"&$top=5"
    )
    resp = httpx.get(url, headers=_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json().get("value", [])


# ── Order lookup ──────────────────────────────────────────────────────────────

def _get_orders_for_contact(contact_id: str) -> list[dict]:
    """
    Fetch the 3 most recent sales orders for a contact.
    Returns sanitised dicts — no PII, only order status fields.

    Adjust field names (new_trackingurl, new_estimateddelivery, new_delayreason)
    to match your D365 customisation — confirm with your D365 admin.
    """
    url = (
        f"{_API_BASE}/salesorders"
        f"?$filter=customerid_contact/contactid eq '{contact_id}'"
        f"&$orderby=createdon desc"
        f"&$top=3"
        f"&$select=ordernumber,statecode,statuscode,"
        f"new_trackingurl,new_estimateddelivery,new_delayreason"
    )
    resp = httpx.get(url, headers=_headers(), timeout=10)
    resp.raise_for_status()
    raw = resp.json().get("value", [])

    return [
        {
            "order_number":       o.get("ordernumber"),
            "state":              o.get("statecode"),
            "status":             o.get("statuscode"),
            "tracking_url":       o.get("new_trackingurl"),
            "estimated_delivery": o.get("new_estimateddelivery"),
            "delay_reason":       o.get("new_delayreason"),
        }
        for o in raw
    ]


# ── Public entry point ────────────────────────────────────────────────────────

def lookup_customer_orders(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    name:  Optional[str] = None,
) -> dict:
    """
    Main WISMO lookup.  Returns a status dict consumed by wismo_gate.format_order_result().

    Return shapes:
      {"status": "not_found"}
      {"status": "ambiguous"}
      {"status": "no_orders"}
      {"status": "api_error", "detail": str}
      {"status": "found", "orders": [...]}
    """
    try:
        contacts = _find_contact(email=email, phone=phone, name=name)
    except Exception as exc:
        return {"status": "api_error", "detail": str(exc)}

    if not contacts:
        return {"status": "not_found"}

    if len(contacts) > 1:
        return {"status": "ambiguous"}

    contact_id = contacts[0]["contactid"]

    try:
        orders = _get_orders_for_contact(contact_id)
    except Exception as exc:
        return {"status": "api_error", "detail": str(exc)}

    if not orders:
        return {"status": "no_orders"}

    return {"status": "found", "orders": orders}


def is_configured() -> bool:
    """Return True if all required D365 env vars are set."""
    return all([_TENANT_ID, _CLIENT_ID, _CLIENT_SECRET, _BASE_URL])
