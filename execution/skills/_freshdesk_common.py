"""Shared plumbing for the Freshdesk skills (D-83). No NAME → invisible to the registry (I-1).

Freshdesk encodes priority/status/source as integers; these maps translate to/from the words a
human (and the LLM) actually use, so tools speak 'High'/'Pending' not 3/3.
"""
from __future__ import annotations

from typing import Any, Optional

PRIORITY_NAMES = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
PRIORITY_IDS = {v.lower(): k for k, v in PRIORITY_NAMES.items()}
STATUS_NAMES = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}
STATUS_IDS = {v.lower(): k for k, v in STATUS_NAMES.items()}
SOURCE_NAMES = {1: "Email", 2: "Portal", 3: "Phone", 7: "Chat", 9: "Feedback Widget",
                10: "Outbound Email"}
SOURCE_IDS = {v.lower(): k for k, v in SOURCE_NAMES.items()}


def priority_id(value: Any) -> Optional[int]:
    """Accept 'high' or 3 → 3. None if not recognised."""
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        v = int(value)
        return v if v in PRIORITY_NAMES else None
    return PRIORITY_IDS.get(str(value or "").strip().lower())


def status_id(value: Any) -> Optional[int]:
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        v = int(value)
        return v if v in STATUS_NAMES else None
    return STATUS_IDS.get(str(value or "").strip().lower())


def source_id(value: Any) -> Optional[int]:
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        v = int(value)
        return v if v in SOURCE_NAMES else None
    return SOURCE_IDS.get(str(value or "").strip().lower())


def slim_ticket(t: dict) -> dict:
    """Trim a ticket to the fields a tech cares about, with priority/status as words."""
    return {
        "id": t.get("id"),
        "subject": t.get("subject"),
        "status": STATUS_NAMES.get(t.get("status"), t.get("status")),
        "priority": PRIORITY_NAMES.get(t.get("priority"), t.get("priority")),
        "type": t.get("type"),
        "requester_id": t.get("requester_id"),
        "responder_id": t.get("responder_id"),
        "group_id": t.get("group_id"),
        "company_id": t.get("company_id"),
        "tags": t.get("tags"),
        "due_by": t.get("due_by"),
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at"),
    }


def slim(row: dict, fields: tuple) -> dict:
    picked = {k: row.get(k) for k in fields if k in row}
    return picked or row
