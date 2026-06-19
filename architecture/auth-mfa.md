# SOP — Optional login MFA (TOTP) (D-87)

MSP AI's own login second factor, opt-in per user. Stdlib only (no pyotp/qrcode deps).

- `execution/web/totp.py` — RFC 6238 (SHA1, 6 digits, 30s step, ±1 window, constant-time compare),
  `generate_secret` / `now_code` / `verify` / `provisioning_uri` (otpauth:// for authenticator apps).
- `AuthStore` (auth.py): columns `mfa_secret`, `mfa_enabled` (migrated on boot). Methods
  start_mfa_setup (pending secret + uri), confirm_mfa (verify→enable), verify_mfa (login check),
  disable_mfa (code-gated, or admin=True to skip), mfa_is_enabled. get_user/list_users expose
  mfa_enabled.
- Login (`_login`): password first; if mfa on, a valid TOTP code is also required. Missing/bad code →
  HTTP 200 `{mfa_required:true[,invalid_code:true]}` and NO session (200 not 401 so the dashboard's
  api() helper re-prompts instead of bouncing to the login screen).
- Self-service: POST /api/me/mfa/setup|enable|disable. Enable AND disable both require a current code
  (enroll proves the authenticator works; disable re-auths so a hijacked session can't silently
  disable it). Lockout recovery: admin POST /api/users/{name}/mfa-reset (audited config_change).
- UI (dashboard/index.html): login form has a hidden 6-digit field shown on mfa_required; the
  My-account settings card has a 2FA on/off section (setup shows the secret + otpauth link to scan or
  key in manually); the admin Users list shows a 2FA pill + "Reset MFA".
- DEFAULT OFF for everyone. This is separate from the M365/Entra per-user MFA tooling (client tenants).

## Amendment (D-87 follow-up) — QR for MFA enrollment
The /api/me/mfa/setup response now includes `qr_svg` (an on-box SVG data-URI rendered with **segno**,
pure-python zero-dep, added to requirements.txt). No external QR service — the secret never leaves the
box. Optional: wrapped in try/except, so if segno is absent the UI falls back to the manual setup key.
Dashboard shows the QR with the key beneath as a "can't scan?" fallback.

## Amendment (D-87 follow-up 2) — trusted-device window (remember this device)
After the FIRST MFA sign-in on a device, the device can be remembered so later sign-ins skip the code.
Per-user window `mfa_trust_days` (0=until-signed-out, else 30/60/90). A signed **trust cookie**
(`mspai_trust`, separate from the session, scoped "trust|user|exp|tag") is issued on a successful code
entry when the login "remember" flag is set. Its `tag` = sha256(current mfa_secret)[:12], so a
re-enroll/admin-reset invalidates every trusted device. Login skips the code iff a valid trust cookie
matches the user AND the current secret tag. Logout clears the trust cookie ONLY in until-signed-out
mode (0). Endpoints: POST /api/me/mfa/trust {days}. Server emits/reads `mspai_trust` (HttpOnly,
SameSite=Strict, +Secure if configured). UI: login has a "Remember this device" checkbox; the 2FA
card has the window selector. First sign-in on a NEW device ALWAYS requires the code.
