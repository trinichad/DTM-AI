"""DTM AI recovery console — a STANDALONE, terminal-only failover on its own port (default :8091).

Why it exists (D-22): it's an INDEPENDENT process (own systemd unit, runs as root, NOT restarted by the
normal deploy). When a bad update or restart takes the main app (:8090) down, this stays up so you can
log in from a browser and fix the box — instead of opening SSH. It serves ONLY a login page + a terminal:
no dashboard, no app APIs, nothing else. It reuses the same users DB + session secret as the main app,
so the same admin login works here too. Stdlib only.

Guardrails are the same as the in-app terminal: admin-only, every command audited before it runs,
`DTM_ADMIN_TERMINAL=0` kill switch. Full root comes from this process running as root (systemd unit).
"""
from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from ..core.adminshell import AdminShell, terminal_enabled
from ..core.audit import AuditStore
from ..core.config import get_config
from .api import SESSION_COOKIE
from .auth import AuthStore, SessionSigner

# Self-contained page — NO external assets (no Tailwind/lucide/fonts), so it works even if the main
# app or its /vendor assets are broken. Plain HTML/CSS/vanilla JS. {braces} are safe (not an f-string).
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DTM AI — Recovery Console</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;height:100vh;display:flex;flex-direction:column;background:#080b13;color:#e2e8f0;
    font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  #login{margin:auto;width:20rem;padding:1.5rem;border:1px solid #202c40;border-radius:14px;background:#0e141f}
  #login h1{font-size:15px;margin:0 0 .25rem;font-family:ui-sans-serif,system-ui,sans-serif}
  #login p{margin:0 0 1rem;color:#6e7d96;font-size:11px}
  input{width:100%;margin:.3rem 0;padding:.55rem .7rem;background:#080b13;color:#e2e8f0;
    border:1px solid #202c40;border-radius:9px;font:inherit}
  input:focus{outline:none;border-color:#6366f1}
  button{cursor:pointer;border:0;border-radius:9px;padding:.55rem .9rem;color:#fff;font:inherit;font-weight:600;
    background:linear-gradient(135deg,#6d6cf6,#8b5cf6)}
  button:focus-visible{outline:2px solid #818cf8;outline-offset:2px}
  #err{color:#fb7185;font-size:11px;height:1rem;margin-top:.4rem}
  #app{display:none;flex:1;min-height:0;flex-direction:column;padding:.7rem;gap:.5rem}
  #bar{display:flex;align-items:center;gap:.6rem;font-size:11px;color:#6e7d96;flex-wrap:wrap}
  #bar .tag{background:rgba(251,191,36,.15);color:#fcd34d;padding:2px 8px;border-radius:9px;font-weight:600}
  #bar .sp{margin-left:auto;display:flex;gap:.6rem}
  #bar a{color:#94a3b8;cursor:pointer;text-decoration:none}#bar a:hover{color:#a5b4fc}
  #out{flex:1;min-height:0;overflow:auto;background:#000;border:1px solid #202c40;border-radius:11px;
    padding:.7rem;white-space:pre-wrap;word-break:break-word}
  #row{display:flex;align-items:center;gap:.5rem;background:#000;border:1px solid #202c40;border-radius:11px;padding:.55rem .7rem}
  #row:focus-within{border-color:#6366f1}
  #ps{color:#34d399;white-space:nowrap}
  #cmd{flex:1;min-width:0;background:transparent;border:0;color:#f1f5f9;font:inherit}
  #cmd:focus{outline:none}
  .e{color:#fb7185}.g{color:#6ee7b7}.m{color:#6e7d96}
</style></head>
<body>
  <form id="login" autocomplete="on">
    <h1>DTM AI — Recovery Console</h1>
    <p>Admin login. Terminal-only failover.</p>
    <input id="u" placeholder="username" autocomplete="username" autofocus>
    <input id="p" type="password" placeholder="password" autocomplete="current-password">
    <button type="submit" style="width:100%;margin-top:.5rem">Sign in</button>
    <div id="err"></div>
  </form>
  <div id="app">
    <div id="bar">
      <span class="tag">RECOVERY · ROOT · AUDITED</span>
      <span id="who"></span>
      <span class="sp"><a id="clear">clear</a><a id="logout">log out</a></span>
    </div>
    <div id="out"></div>
    <div id="row"><span id="ps">$</span><input id="cmd" autocomplete="off" autocapitalize="off"
      spellcheck="false" placeholder="command · Enter to run · up/down history"></div>
  </div>
<script>
const $=s=>document.querySelector(s);
let CWD='/', USER='root', HOST='server', HIST=[], HI=0;
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},
  credentials:'same-origin',body:JSON.stringify(b||{})});let j={};try{j=await r.json()}catch(e){}return {status:r.status,j};}
function w(t,c){const s=document.createElement('span');if(c)s.className=c;s.textContent=t;const o=$('#out');o.appendChild(s);o.scrollTop=o.scrollHeight;}
function ps(){$('#ps').textContent=USER+':'+CWD+'$';}
async function boot(){
  const r=await fetch('/whoami',{credentials:'same-origin'});
  if(r.status!==200){$('#login').style.display='';$('#app').style.display='none';$('#u').focus();return;}
  const j=await r.json();USER=j.user||'root';HOST=j.host||'server';CWD=j.cwd||'/';
  $('#login').style.display='none';$('#app').style.display='flex';
  $('#who').textContent=USER+' @ '+HOST;ps();
  if(!$('#out').dataset.init){$('#out').dataset.init='1';
    w('DTM AI recovery console — running as '+USER+'. '+(j.enabled===false?'(terminal disabled: DTM_ADMIN_TERMINAL=0)':'Every command is audited.')+'\\n\\n','m');}
  $('#cmd').focus();
}
$('#login').onsubmit=async e=>{e.preventDefault();$('#err').textContent='';
  const {status,j}=await jpost('/login',{username:$('#u').value,password:$('#p').value});
  if(status===200){$('#p').value='';boot();}
  else $('#err').textContent=(j&&j.error)||'login failed';};
$('#clear').onclick=()=>{$('#out').innerHTML='';$('#cmd').focus();};
$('#logout').onclick=async()=>{await jpost('/logout',{});location.reload();};
$('#cmd').onkeydown=async e=>{
  if(e.key==='ArrowUp'){e.preventDefault();if(HIST.length){HI=Math.max(0,HI-1);$('#cmd').value=HIST[HI]||''}return;}
  if(e.key==='ArrowDown'){e.preventDefault();if(HIST.length){HI=Math.min(HIST.length,HI+1);$('#cmd').value=HIST[HI]||''}return;}
  if(e.key!=='Enter')return;
  const c=$('#cmd').value;if(!c.trim())return;
  HIST.push(c);HI=HIST.length;$('#cmd').value='';
  w(USER+':'+CWD+'$ '+c+'\\n','g');
  $('#cmd').disabled=true;
  const {status,j}=await jpost('/run',{command:c});
  $('#cmd').disabled=false;$('#cmd').focus();
  if(status===401){w('(session expired — reloading)\\n','e');setTimeout(()=>location.reload(),800);return;}
  if(!j){w('(request failed)\\n','e');return;}
  if(j.error){w(j.error+'\\n','e');return;}
  if(j.stdout)w(j.stdout.endsWith('\\n')?j.stdout:j.stdout+'\\n');
  if(j.stderr)w(j.stderr.endsWith('\\n')?j.stderr:j.stderr+'\\n','e');
  if(j.cwd){CWD=j.cwd;ps();}
  if(typeof j.exit_code==='number'&&j.exit_code!==0&&!j.stderr)w('(exit '+j.exit_code+')\\n','e');
};
boot();
</script></body></html>
"""


def _make_handler(auth: AuthStore, signer: SessionSigner, shell: AdminShell,
                  audit: AuditStore, ttl: int, secure_cookie: bool):
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

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/?"):
                return self._html()
            if self.path == "/whoami":
                u = self._admin()
                if not u:
                    return self._json(401, {"error": "auth"})
                import getpass
                import socket
                try:
                    who, host = getpass.getuser(), socket.gethostname()
                except OSError:
                    who, host = "root", "server"
                return self._json(200, {"login": u, "user": who, "host": host,
                                        "cwd": shell.cwd(u), "enabled": terminal_enabled()})
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
            if self.path == "/run":
                u = self._admin()
                if not u:
                    return self._json(401, {"error": "auth"})
                if not terminal_enabled():
                    return self._json(403, {"error": "terminal disabled (DTM_ADMIN_TERMINAL=0)"})
                cmd = (b.get("command") or "").strip()
                if not cmd:
                    return self._json(400, {"error": "command required"})
                audit.record(actor=u, tenant_id="*", action="terminal", detail="[recovery] " + cmd[:480])
                return self._json(200, shell.run(u, cmd))
            self._json(404, {"error": "not found"})

    return H


def create_server(port: int = 8091, host: str = "0.0.0.0", db_path: Optional[Path] = None):
    cfg = get_config()
    auth = AuthStore(db_path)
    signer = SessionSigner()
    shell = AdminShell()
    audit = AuditStore(db_path)
    ttl = cfg.int("DTM_SESSION_TTL_MIN", 720)
    handler = _make_handler(auth, signer, shell, audit, ttl, cfg.bool("DTM_COOKIE_SECURE", False))
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="DTM AI recovery console (terminal-only failover)")
    p.add_argument("--port", type=int, default=8091)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args(argv)
    srv = create_server(args.port, args.host)
    print(f"DTM AI recovery console (terminal-only) on http://{args.host}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
