# SOP — Email alerts (one integration, every transport — D-28)

> One `email` integration card covers smtp2go (API **and** SMTP login) and any other relay or
> mail server the owner points it at. Used by the `send_email` alert tool and the card's
> "send test email" button.

## Configuration (Integrations → Email)

| key | meaning |
|---|---|
| `EMAIL_FROM` | **required** — sender, e.g. `MSP AI <alerts@example.com>` |
| `EMAIL_MODE` | `api` \| `smtp` — optional; auto-detected (API key set → `api`, else `smtp`) |
| `EMAIL_API_KEY` | smtp2go-style API key (mode `api`) |
| `EMAIL_API_URL` | API base (default `https://api.smtp2go.com/v3` — any compatible relay) |
| `EMAIL_SMTP_HOST` / `EMAIL_SMTP_PORT` | SMTP relay (mode `smtp`); port default 587 |
| `EMAIL_SMTP_USER` / `EMAIL_SMTP_PASS` | SMTP login (optional for IP-authenticated relays) |
| `EMAIL_SMTP_SECURITY` | `starttls` (default) \| `ssl` \| `none` |
| `EMAIL_DEFAULT_TO` | default recipient when a tool call omits `to` |
| `EMAIL_ALLOWED_RECIPIENTS` | recipient floor — see below |

Client: `execution/clients/email.py` (`EmailClient`). API mode posts to `<EMAIL_API_URL>/email/send`
with the key in `X-Smtp2go-Api-Key` (+ body, for compatible relays). SMTP mode uses stdlib
`smtplib` with STARTTLS/SSL per `EMAIL_SMTP_SECURITY`.

`probe()`: API mode → auth-proving `stats/email_summary` call; SMTP mode → connect + EHLO +
STARTTLS + login + NOOP (never sends mail).

## Recipient floor (exfiltration guard)

Email FROM the agent is an outbound data channel, so recipients are allowlisted **in the client**,
not just in prose:

- `EMAIL_ALLOWED_RECIPIENTS` unset → only `EMAIL_DEFAULT_TO` may receive mail.
- CSV of addresses and/or `@domains` → only those.
- `*` → anyone (explicit owner opt-out).

## The tool

`skills/send_email.py` — `send_email(subject, body, to?, html?)`, `CATEGORY="alert"`,
`RISK_LEVEL="medium"`, `ENABLED_BY_DEFAULT=False`, `SOURCE="email"`. Disabled until the owner
flips it in the Capability Console (I-4); every send is audited like any dispatch() call.

## Test path

Card button → `POST /api/integrations/email/test {to?}` (admin-gated, audited) → real send via the
configured transport → result shown with latency. Recipient floor applies to tests too.

---

## Amendment (2026-06-11, D-38) — markdown bodies render as real HTML

The agent naturally writes markdown (tables, bold, lists) — sent as raw plain text it arrives as
pipe-soup once the mail client wraps the lines. So `send_email` now sends **multipart/alternative**:

- The tool body is treated as **markdown** and rendered to inline-styled HTML
  (`execution/core/mdmail.py` → `md_to_html`, using the `Markdown` package with the `tables`,
  `nl2br` + `sane_lists` extensions; table/th/td get inline borders + padding so tables survive
  Gmail/Outlook). The original markdown text is kept as the plain-text part, so nothing is lost
  on text-only clients — and if the `Markdown` package is missing, the mail simply goes out
  plain-text as before (the render step fails soft, the send never breaks).
- `EmailClient.send` gains an optional `html_body` argument: API mode posts `text_body` +
  `html_body` together; SMTP mode uses `set_content(text)` + `add_alternative(html)`.
- The tool's `html=true` flag keeps its old meaning — "the body I'm passing is ALREADY HTML,
  send it as-is" — and skips the markdown render.

---

## Lesson (2026-06-11, D-42) — validate the default recipient
A corrupted `EMAIL_DEFAULT_TO` (`owner@example.com`) made every to-less send fail at the
recipient floor with no clear cause. The recipients API now returns `default_to_valid` and the
Email card shows a red "not a valid email address" warning when it's malformed. The recipient
floor itself was working correctly (fail-closed); the gap was that a bad CONFIG value was invisible.

---

## Amendment (2026-06-11, D-46) — multiple recipients in one send
`to` accepts comma/semicolon-separated recipients (deduped). EVERY address must individually pass
the recipient floor or NOTHING is sent — no partial sends, the blocked address is named. API mode
posts the list in `to`; SMTP joins them in one message's To header. The skill resolves `me` inside
a list ("me, alex@…" works) and its schema tells the model one send reaches all recipients —
so "email it to me and X" is one call, not N.
