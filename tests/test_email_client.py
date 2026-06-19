"""Email client tests (D-28) — recipient floor is a real boundary; both transports work."""
import unittest

from execution.clients.email import EmailClient


def _env(**over):
    base = {"EMAIL_FROM": "MSP AI <alerts@msp.example>",
            "EMAIL_API_KEY": "api-key-x", "EMAIL_DEFAULT_TO": "team@msp.example"}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


class RecipientFloor(unittest.TestCase):
    def test_unset_allows_only_default_to(self):
        c = EmailClient(_env(), transport=lambda *a, **k: (200, {"data": {}}))
        self.assertTrue(c.recipient_allowed("team@msp.example")[0])
        self.assertFalse(c.recipient_allowed("evil@exfil.example")[0])

    def test_csv_addresses_and_domains(self):
        c = EmailClient(_env(EMAIL_ALLOWED_RECIPIENTS="boss@msp.example, @client.example"),
                        transport=lambda *a, **k: (200, {"data": {}}))
        self.assertTrue(c.recipient_allowed("boss@msp.example")[0])
        self.assertTrue(c.recipient_allowed("anyone@client.example")[0])
        self.assertFalse(c.recipient_allowed("team@msp.example")[0])   # not listed

    def test_star_allows_anyone(self):
        c = EmailClient(_env(EMAIL_ALLOWED_RECIPIENTS="*"),
                        transport=lambda *a, **k: (200, {"data": {}}))
        self.assertTrue(c.recipient_allowed("anyone@anywhere.example")[0])

    def test_invalid_address_rejected(self):
        c = EmailClient(_env(EMAIL_ALLOWED_RECIPIENTS="*"),
                        transport=lambda *a, **k: (200, {"data": {}}))
        self.assertFalse(c.recipient_allowed("not-an-address")[0])

    def test_send_enforces_floor(self):
        called = []
        c = EmailClient(_env(), transport=lambda *a, **k: called.append(1) or (200, {"data": {}}))
        r = c.send("s", "b", to="evil@exfil.example")
        self.assertFalse(r["ok"])
        self.assertEqual(called, [])        # transport never touched


class ApiMode(unittest.TestCase):
    def test_send_via_api(self):
        seen = {}
        def t(method, url, headers=None, params=None, json_body=None, **_):
            seen.update({"url": url, "headers": headers, "body": json_body})
            return 200, {"data": {"email_id": "e-1", "succeeded": 1}}
        c = EmailClient(_env(), transport=t)
        r = c.send("Alert", "body text")     # defaults to EMAIL_DEFAULT_TO
        self.assertTrue(r["ok"])
        self.assertEqual(r["via"], "api")
        self.assertTrue(seen["url"].endswith("/email/send"))
        self.assertEqual(seen["headers"]["X-Smtp2go-Api-Key"], "api-key-x")
        self.assertEqual(seen["body"]["to"], ["team@msp.example"])
        self.assertEqual(seen["body"]["text_body"], "body text")

    def test_multiple_recipients_one_send(self):
        # D-46: comma/semicolon-separated recipients go out as ONE send
        seen = {}
        def t(method, url, headers=None, params=None, json_body=None, **_):
            seen.update(json_body); return 200, {"data": {"email_id": "e-3"}}
        c = EmailClient(_env(EMAIL_ALLOWED_RECIPIENTS="@msp.example"), transport=t)
        r = c.send("s", "b", to="a@msp.example, b@msp.example; a@msp.example")  # dupe dropped
        self.assertTrue(r["ok"])
        self.assertEqual(seen["to"], ["a@msp.example", "b@msp.example"])
        self.assertEqual(r["to"], "a@msp.example, b@msp.example")

    def test_multiple_recipients_fail_closed_if_any_blocked(self):
        called = []
        c = EmailClient(_env(EMAIL_ALLOWED_RECIPIENTS="@msp.example"),
                        transport=lambda *a, **k: called.append(1) or (200, {"data": {}}))
        r = c.send("s", "b", to="a@msp.example, evil@exfil.example")
        self.assertFalse(r["ok"])
        self.assertIn("exfil.example", r["error"])
        self.assertEqual(called, [])               # nothing sent to anyone — no partial sends

    def test_markdown_html_alternative_via_api(self):
        # D-38: html_body rides along with text_body so tables arrive formatted
        seen = {}
        def t(method, url, headers=None, params=None, json_body=None, **_):
            seen.update(json_body)
            return 200, {"data": {"email_id": "e-2"}}
        c = EmailClient(_env(), transport=t)
        r = c.send("Users", "| a | b |\n|---|---|\n| 1 | 2 |",
                   html_body="<table><tr><td>1</td></tr></table>")
        self.assertTrue(r["ok"])
        self.assertIn("| a | b |", seen["text_body"])         # plain part = original markdown
        self.assertIn("<table>", seen["html_body"])

    def test_relay_failure_surfaces(self):
        c = EmailClient(_env(), transport=lambda *a, **k: (
            200, {"data": {"failures": ["bad recipient"]}}))
        r = c.send("s", "b")
        self.assertFalse(r["ok"])

    def test_mode_autodetect(self):
        self.assertEqual(EmailClient(_env()).mode, "api")
        self.assertEqual(EmailClient(_env(EMAIL_API_KEY=None,
                                          EMAIL_SMTP_HOST="mail.x")).mode, "smtp")
        self.assertEqual(EmailClient(_env(EMAIL_MODE="smtp")).mode, "smtp")


class MarkdownRender(unittest.TestCase):
    """D-38 — md_to_html turns the agent's markdown into a styled table, never raises."""

    def test_table_renders_with_inline_styles(self):
        from execution.core.mdmail import md_to_html
        h = md_to_html("| Name | UPN |\n|---|---|\n| Alex | alex@x.com |")
        self.assertIn("<table style=", h)
        self.assertIn("<td style=", h)
        self.assertIn("alex@x.com", h)

    def test_plain_lines_and_emphasis(self):
        from execution.core.mdmail import md_to_html
        h = md_to_html("Alex,\nHere is the list.\n\n**13 users** found.")
        self.assertIn("<br", h)                              # single newline preserved (nl2br)
        self.assertIn("<strong", h)

    def test_empty_body_renders_nothing(self):
        from execution.core.mdmail import md_to_html
        self.assertEqual(md_to_html("  "), "")


class _FakeSMTP:
    sent = []
    def __init__(self, host, port): self.host, self.port = host, port
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self): self.tls = True
    def login(self, u, p): self.auth = (u, p)
    def noop(self): pass
    def send_message(self, msg): _FakeSMTP.sent.append(msg)


class SmtpMode(unittest.TestCase):
    def test_send_via_smtp(self):
        _FakeSMTP.sent = []
        c = EmailClient(_env(EMAIL_MODE="smtp", EMAIL_SMTP_HOST="mail.relay.example",
                             EMAIL_SMTP_USER="u", EMAIL_SMTP_PASS="p"),
                        smtp_factory=_FakeSMTP)
        r = c.send("Subject", "Body")
        self.assertTrue(r["ok"])
        self.assertEqual(len(_FakeSMTP.sent), 1)
        self.assertEqual(_FakeSMTP.sent[0]["To"], "team@msp.example")

    def test_multiple_recipients_via_smtp_one_message(self):
        _FakeSMTP.sent = []
        c = EmailClient(_env(EMAIL_MODE="smtp", EMAIL_SMTP_HOST="mail.relay.example",
                             EMAIL_ALLOWED_RECIPIENTS="@msp.example"), smtp_factory=_FakeSMTP)
        r = c.send("s", "b", to="a@msp.example, b@msp.example")
        self.assertTrue(r["ok"])
        self.assertEqual(len(_FakeSMTP.sent), 1)                       # ONE message
        self.assertEqual(_FakeSMTP.sent[0]["To"], "a@msp.example, b@msp.example")

    def test_markdown_html_alternative_via_smtp(self):
        _FakeSMTP.sent = []
        c = EmailClient(_env(EMAIL_MODE="smtp", EMAIL_SMTP_HOST="mail.relay.example"),
                        smtp_factory=_FakeSMTP)
        r = c.send("Users", "plain md", html_body="<p>rich</p>")
        self.assertTrue(r["ok"])
        msg = _FakeSMTP.sent[0]
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        parts = {p.get_content_type(): p.get_content() for p in msg.iter_parts()}
        self.assertIn("plain md", parts["text/plain"])
        self.assertIn("<p>rich</p>", parts["text/html"])

    def test_probe_never_sends(self):
        _FakeSMTP.sent = []
        c = EmailClient(_env(EMAIL_MODE="smtp", EMAIL_SMTP_HOST="mail.relay.example"),
                        smtp_factory=_FakeSMTP)
        self.assertTrue(c.probe()["ok"])
        self.assertEqual(_FakeSMTP.sent, [])

    def test_from_required(self):
        with self.assertRaises(ValueError):
            EmailClient({"EMAIL_API_KEY": "x"})


if __name__ == "__main__":
    unittest.main()
