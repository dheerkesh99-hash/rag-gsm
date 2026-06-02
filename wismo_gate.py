"""
wismo_gate.py — WISMO (Where Is My Order?) intent detection and PII extraction.

Mirrors the structure of brand_gate.py: pure functions + a session dataclass.
No PII is stored beyond the current session; nothing is logged.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

# ── States ────────────────────────────────────────────────────────────────────
STATE_IDLE         = "idle"
STATE_AWAIT_PII    = "await_pii"      # waiting for email / phone / name
STATE_AWAIT_RETRY  = "await_retry"    # first identifier failed — asking again
STATE_RESOLVED     = "resolved"       # CRM lookup succeeded
STATE_ESCALATED    = "escalated"      # exceeded retry limit → hand off to human

MAX_PII_ATTEMPTS = 3

# ── WISMO signal phrases ──────────────────────────────────────────────────────
WISMO_SIGNALS = [
    "where is my order", "where's my order", "wheres my order",
    "track my order", "track my package", "order status",
    "order hasn't arrived", "order has not arrived",
    "order not received", "missing order", "lost order",
    "delayed shipment", "delayed order", "shipment delay",
    "shipping status", "delivery status", "delivery update",
    "when will my order", "when will i receive",
    "hasn't shipped", "has not shipped", "not yet shipped",
    "not delivered", "not received my order",
    "tracking number", "tracking info", "expected delivery",
    "order is late", "late delivery", "package missing",
    "package not arrived", "still waiting for my order",
    "where is my package", "what happened to my order",
    "order update", "wismo",
]


def detect_wismo(text: str) -> bool:
    """Return True if the message looks like a WISMO query."""
    t = text.lower()
    return any(sig in t for sig in WISMO_SIGNALS)


# ── PII extraction ────────────────────────────────────────────────────────────

def extract_email(text: str) -> Optional[str]:
    m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    return m.group(0).lower() if m else None


def extract_phone(text: str) -> Optional[str]:
    """Return digits-only phone string (10+ digits) or None."""
    m = re.search(r"(\+?1[\s.\-]?)?(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})", text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    return digits if len(digits) >= 10 else None


def extract_name(text: str) -> Optional[str]:
    """
    Return a plausible full name if the text contains 2–4 capitalised words
    and no other strong signals (email/phone).  Intentionally conservative.
    """
    # Don't try to extract a name if an email or phone is present
    if extract_email(text) or extract_phone(text):
        return None
    words = text.strip().split()
    if 2 <= len(words) <= 4 and all(w[0].isupper() for w in words if w.isalpha()):
        return " ".join(words)
    return None


# ── Session dataclass ─────────────────────────────────────────────────────────

@dataclass
class WismoSession:
    state:        str           = STATE_IDLE
    pii_attempts: int           = 0
    # Identifiers collected — used for CRM lookup only, not echoed to LLM
    email:        Optional[str] = None
    phone:        Optional[str] = None
    name:         Optional[str] = None

    def get_session_defaults(self) -> dict:
        return {
            "state":        self.state,
            "pii_attempts": self.pii_attempts,
            "email":        self.email,
            "phone":        self.phone,
            "name":         self.name,
        }


def get_session_defaults() -> dict:
    return WismoSession().get_session_defaults()


def from_session(d: dict) -> WismoSession:
    return WismoSession(
        state        = d.get("state",        STATE_IDLE),
        pii_attempts = d.get("pii_attempts", 0),
        email        = d.get("email"),
        phone        = d.get("phone"),
        name         = d.get("name"),
    )


def to_session(ws: WismoSession) -> dict:
    return {
        "state":        ws.state,
        "pii_attempts": ws.pii_attempts,
        "email":        ws.email,
        "phone":        ws.phone,
        "name":         ws.name,
    }


# ── Prompt helpers ────────────────────────────────────────────────────────────

def ask_for_pii() -> str:
    return (
        "I can look up your order status right away. "
        "Please share your **email address**, **phone number**, "
        "or **full name** associated with your account."
    )


def ask_for_pii_retry() -> str:
    return (
        "I wasn't able to find an account with those details. "
        "Could you try your **email address** instead? "
        "That's usually the most reliable way to look up an order."
    )


def escalation_message() -> str:
    return (
        "I'm sorry — I wasn't able to locate your order with the information provided. "
        "Please contact our support team directly so they can assist you:\n\n"
        "📧 **fishinginfo@gsmorg.com**\n"
        "📞 **877-269-8490**\n\n"
        "Please have your order number ready if possible."
    )


def format_order_result(result: dict) -> str:
    """Convert a CRM lookup result dict into a customer-facing message."""
    status = result.get("status")

    if status == "not_found":
        return None  # caller handles retry

    if status == "ambiguous":
        return (
            "I found multiple accounts matching that information. "
            "Could you provide your **email address** so I can pinpoint the right one?"
        )

    if status == "no_orders":
        return (
            "I found your account but there are no recent orders on file. "
            "If you believe this is incorrect, please contact us at "
            "fishinginfo@gsmorg.com."
        )

    if status == "api_error":
        return (
            "I'm having trouble reaching our order system right now. "
            "Please try again in a moment or contact us at fishinginfo@gsmorg.com."
        )

    if status == "found":
        orders = result.get("orders", [])
        if not orders:
            return "Your account was found but has no recent orders."

        lines = ["Here are your recent order(s):\n"]
        for o in orders:
            lines.append(f"**Order {o.get('order_number', 'N/A')}**")

            state_label = {0: "Active", 1: "Submitted", 2: "Cancelled", 5: "On Hold"}.get(
                o.get("state"), "In Progress"
            )
            lines.append(f"- Status: {state_label}")

            if o.get("tracking_url"):
                lines.append(f"- Tracking: {o['tracking_url']}")
            if o.get("estimated_delivery"):
                lines.append(f"- Estimated delivery: {o['estimated_delivery']}")
            if o.get("delay_reason"):
                lines.append(f"- Delay reason: {o['delay_reason']}")
            lines.append("")

        lines.append(
            "If you have further questions about your shipment, "
            "please contact us at fishinginfo@gsmorg.com or 877-269-8490."
        )
        return "\n".join(lines)

    return None
