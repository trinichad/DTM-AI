"""DTM AI recovery console — a STANDALONE, terminal-only failover on its own port (default :8091).

Why it exists (D-22): it's an INDEPENDENT process (own systemd unit, runs as root, NOT restarted by the
normal deploy). When a bad update or restart takes the main app (:8090) down, this stays up so you can
log in from a browser and fix the box — instead of opening SSH. It serves ONLY a login page + an
interactive terminal (real PTY over WebSocket, xterm.js) plus the three xterm assets it needs: no
dashboard, no app APIs. Reuses the same users DB + session secret as the main app, so the same admin
login works here too. Stdlib only.

Guardrails: admin-only, every terminal open audited, `DTM_ADMIN_TERMINAL=0` kill switch. Full root comes
from this process running as root (systemd unit).
"""
from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from ..core.adminshell import terminal_enabled
from ..core.audit import AuditStore
from ..core.config import get_config
from . import pty_session, wsutil
from .api import SESSION_COOKIE
from .auth import AuthStore, SessionSigner

_VENDOR = Path(__file__).resolve().parents[2] / "dashboard" / "vendor"
_VENDOR_OK = {"xterm.js": "application/javascript", "addon-fit.js": "application/javascript",
              "xterm.css": "text/css"}

# Self-contained page (only depends on the three xterm files this server also serves). {braces} are safe.
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DTM AI — Recovery Console</title>
<link rel="stylesheet" href="/vendor/xterm.css">
<script src="/vendor/xterm.js"></script>
<script src="/vendor/addon-fit.js"></script>
<style>
  :root{color-scheme:dark}*{box-sizing:border-box}
  body{margin:0;height:100vh;display:flex;flex-direction:column;background:#080b13;color:#e2e8f0;
    font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  #login{margin:auto;width:20rem;padding:1.5rem;border:1px solid #202c40;border-radius:14px;background:#0e141f}
  #login h1{font-size:15px;margin:0 0 .25rem;font-family:ui-sans-serif,system-ui,sans-serif}
  #login p{margin:0 0 1rem;color:#6e7d96;font-size:11px}
  input{width:100%;margin:.3rem 0;padding:.55rem .7rem;background:#080b13;color:#e2e8f0;border:1px solid #202c40;border-radius:9px;font:inherit}
  input:focus{outline:none;border-color:#6366f1}
  button{cursor:pointer;border:0;border-radius:9px;padding:.55rem .9rem;color:#fff;font:inherit;font-weight:600;background:linear-gradient(135deg,#6d6cf6,#8b5cf6)}
  #err{color:#fb7185;font-size:11px;height:1rem;margin-top:.4rem}
  #app{display:none;flex:1;min-height:0;flex-direction:column;padding:.7rem;gap:.5rem}
  #bar{display:flex;align-items:center;gap:.6rem;font-size:11px;color:#6e7d96;flex-wrap:wrap}
  #bar .tag{background:rgba(251,191,36,.15);color:#fcd34d;padding:2px 8px;border-radius:9px;font-weight:600}
  #bar .sp{margin-left:auto;display:flex;gap:.6rem}#bar a{color:#94a3b8;cursor:pointer}#bar a:hover{color:#a5b4fc}
  #host{flex:1;min-height:0;border:1px solid #202c40;border-radius:11px;overflow:hidden;background:#0b0b10}
</style></head>
<body>
  <form id="login" autocomplete="on">
    <h1>DTM AI — Recovery Console</h1><p>Admin login. Interactive root terminal.</p>
    <input id="u" placeholder="username" autocomplete="username" autofocus>
    <input id="p" type="password" placeholder="password" autocomplete="current-password">
    <button type="submit" style="width:100%;margin-top:.5rem">Sign in</button>
    <div id="err"></div>
  </form>
  <div id="app">
    <div id="bar"><span class="tag">RECOVERY · ROOT · AUDITED</span><span id="st">connecting…</span>
      <span class="sp"><a id="logout">log out</a></span></div>
    <div id="host"></div>
  </div>
<script>
const $=s=>document.querySelector(s);
let TERM=null, WS=null;
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(b||{})});let j={};try{j=await r.json()}catch(e){}return{status:r.status,j};}
function setSt(c,t){const e=$('#st');if(e)e.innerHTML='<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+c+';margin-right:5px"></span>'+t;}
function initTerm(){
  if(TERM)return;
  TERM=new Terminal({fontFamily:'ui-monospace,Menlo,monospace',fontSize:13,cursorBlink:true,allowProposedApi:true,
    theme:{background:'#0b0b10',foreground:'#e6e7ea',cursor:'#8b5cf6',selectionBackground:'#8b5cf640'}});
  const fit=new FitAddon.FitAddon();TERM.loadAddon(fit);TERM.open($('#host'));setTimeout(()=>{try{fit.fit()}catch(e){}},0);
  const proto=location.protocol==='https:'?'wss':'ws';
  WS=new WebSocket(proto+'://'+location.host+'/ws/terminal');
  const rs=()=>{try{fit.fit()}catch(e){}if(WS.readyState===1)WS.send(JSON.stringify({type:'resize',cols:TERM.cols,rows:TERM.rows}));};
  WS.onopen=()=>{setSt('#10b981','live root shell');rs();TERM.focus();};
  WS.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='out')TERM.write(m.data);else if(m.type==='exit'){TERM.write('\\r\\n\\x1b[90m[session ended]\\x1b[0m\\r\\n');setSt('#ef4444','session ended');}};
  WS.onclose=()=>setSt('#ef4444','disconnected');
  WS.onerror=()=>setSt('#ef4444','connection error');
  TERM.onData(d=>{if(WS.readyState===1)WS.send(JSON.stringify({type:'in',data:d}));});
  new ResizeObserver(rs).observe($('#host'));
}
async function boot(){
  const r=await fetch('/whoami',{credentials:'same-origin'});
  if(r.status!==200){$('#login').style.display='';$('#app').style.display='none';$('#u').focus();return;}
  $('#login').style.display='none';$('#app').style.display='flex';initTerm();
}
$('#login').onsubmit=async e=>{e.preventDefault();$('#err').textContent='';
  const {status,j}=await jpost('/login',{username:$('#u').value,password:$('#p').value});
  if(status===200){$('#p').value='';boot();}else $('#err').textContent=(j&&j.error)||'login failed';};
$('#logout').onclick=async()=>{await jpost('/logout',{});location.reload();};
boot();
</script></body></html>
"""


def _make_handler(auth: AuthStore, signer: SessionSigner, audit: AuditStore,
                  ttl: int, secure_cookie: bool):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):
            pass

        def _user(self) -> Optional[str]:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            m = c.get(SESSION_COOKIE)
            return signer.verify(m.value) if m else None

        def _admin(self) -> Optional[str]:
            u = self._user()
            return u if (u and auth.get_role(u) == "admin") else None

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return {}

        def _cookie(self, value: str, max_age: int) -> None:
            attrs = [f"{SESSION_COOKIE}={value}", "HttpOnly", "SameSite=Strict", "Path=/", f"Max-Age={max_age}"]
            if secure_cookie:
                attrs.append("Secure")
            self.send_header("Set-Cookie", "; ".join(attrs))

        def _json(self, status: int, obj: dict, *, set_cookie=None, clear_cookie=False) -> None:
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if set_cookie is not None:
                self._cookie(set_cookie, ttl * 60)
            if clear_cookie:
                self._cookie("", 0)
            self.end_headers()
            self.wfile.write(data)

        def _html(self) -> None:
            body = _PAGE.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _vendor(self, path: str) -> None:
            name = path[len("/vendor/"):]
            ctype = _VENDOR_OK.get(name)
            if not ctype:
                self.send_response(HTTPStatus.NOT_FOUND); self.end_headers(); return
            try:
                body = (_VENDOR / name).read_bytes()
            except OSError:
                self.send_response(HTTPStatus.NOT_FOUND); self.end_headers(); return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)

        def _ws_terminal(self) -> None:
            self.close_connection = True
            u = self._admin()
            if not u or not terminal_enabled():
                self.send_response(HTTPStatus.FORBIDDEN); self.end_headers(); return
            if not wsutil.is_ws_upgrade(self.headers):
                self.send_response(HTTPStatus.BAD_REQUEST); self.end_headers(); return
            try:
                if not wsutil.handshake(self):
                    return
                audit.record(actor=u, tenant_id="*", action="terminal", detail="[recovery] pty open")
                pty_session.serve(self.connection)
            except Exception:
                pass   # disconnect / shell gone — pty_session always reaps the child

        def do_GET(self):
            if self.path == "/ws/terminal":
                return self._ws_terminal()
            if self.path.startswith("/vendor/"):
                return self._vendor(self.path)
            if self.path == "/" or self.path.startswith("/?"):
                return self._html()
            if self.path == "/whoami":
                u = self._admin()
                if not u:
                    return self._json(401, {"error": "auth"})
                return self._json(200, {"login": u, "enabled": terminal_enabled()})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            b = self._body()
            if self.path == "/login":
                role = auth.verify_login(b.get("username", ""), b.get("password", ""))
                if not role:
                    return self._json(401, {"error": "invalid credentials"})
                if role != "admin":
                    return self._json(403, {"error": "admin only"})
                return self._json(200, {"ok": True}, set_cookie=signer.make(b.get("username"), ttl))
            if self.path == "/logout":
                return self._json(200, {"ok": True}, clear_cookie=True)
            self._json(404, {"error": "not found"})

    return H


def create_server(port: int = 8091, host: str = "0.0.0.0", db_path: Optional[Path] = None):
    cfg = get_config()
    auth = AuthStore(db_path)
    audit = AuditStore(db_path)
    signer = SessionSigner()
    ttl = cfg.int("DTM_SESSION_TTL_MIN", 720)
    handler = _make_handler(auth, signer, audit, ttl, cfg.bool("DTM_COOKIE_SECURE", False))
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="DTM AI recovery console (terminal-only failover)")
    p.add_argument("--port", type=int, default=8091)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args(argv)
    srv = create_server(args.port, args.host)
    print(f"DTM AI recovery console (interactive terminal) on http://{args.host}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
