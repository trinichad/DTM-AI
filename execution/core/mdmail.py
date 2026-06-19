"""Markdown → email-ready HTML (D-38; SOP: email-alerts).

The agent writes markdown (tables, bold, lists); mail clients line-wrap raw markdown into
pipe-soup. `md_to_html` renders it with inline styles (Gmail/Outlook ignore <style> blocks in
many cases, so table borders/padding must be inline). Fails SOFT: without the `Markdown`
package it returns "" and the caller sends plain text only — a missing renderer must never
break the email path.
"""
from __future__ import annotations

_STYLES = {
    "table": "border-collapse:collapse;margin:10px 0;",
    "th": ("border:1px solid #d1d5db;padding:6px 10px;background:#f3f4f6;"
           "text-align:left;font-weight:600;"),
    "td": "border:1px solid #d1d5db;padding:6px 10px;",
    "code": "background:#f3f4f6;padding:1px 4px;border-radius:3px;font-size:13px;",
    "h1": "font-size:20px;margin:14px 0 6px;",
    "h2": "font-size:17px;margin:12px 0 6px;",
    "h3": "font-size:15px;margin:10px 0 4px;",
}
_WRAP = ('<div style="font-family:system-ui,\'Segoe UI\',Arial,sans-serif;font-size:14px;'
         'line-height:1.5;color:#111827">{}</div>')


def md_to_html(body: str) -> str:
    """Render a markdown email body to inline-styled HTML. "" when no renderer is available
    (caller then sends the plain-text part only)."""
    if not (body or "").strip():
        return ""
    try:
        import markdown
    except ImportError:
        return ""
    try:
        html = markdown.markdown(body, extensions=["tables", "nl2br", "sane_lists"])
    except Exception:           # noqa: BLE001 — a render bug must never block the send
        return ""
    for tag, style in _STYLES.items():
        html = html.replace(f"<{tag}>", f'<{tag} style="{style}">')
    return _WRAP.format(html)
