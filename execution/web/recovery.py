"""MSP AI recovery console — a STANDALONE, terminal-only failover on its own port (default :8091).

Why it exists (D-22): it's an INDEPENDENT process (own systemd unit, runs as root, NOT restarted by the
normal deploy). When a bad update or restart takes the main app (:8090) down, this stays up so you can
log in from a browser and fix the box — instead of opening SSH. It serves ONLY a login page + an
interactive terminal (real PTY over WebSocket, xterm.js) plus the three xterm assets it needs: no
dashboard, no app APIs. Reuses the same users DB + session secret as the main app, so the same admin
login works here too. Stdlib only.

Guardrails: admin-only, every terminal open audited, `MSPAI_ADMIN_TERMINAL=0` kill switch. Full root comes
from this process running as root (systemd unit).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
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
<title>MSP AI — Recovery Console</title>
<link rel="stylesheet" href="/vendor/xterm.css">
<script src="/vendor/xterm.js"></script>
<script src="/vendor/addon-fit.js"></script>
<style>
  :root{color-scheme:dark}*{box-sizing:border-box}
  body{margin:0;height:100vh;display:flex;flex-direction:column;background:#080b13;color:#e2e8f0;
    font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  #login{margin:auto;width:20rem;padding:1.5rem;border:1px solid #202c40;border-radius:14px;background:#0e141f;
    box-shadow:0 24px 60px -20px #000,inset 0 1px 0 #ffffff0a}
  #login h1{font-size:15px;margin:0 0 .25rem;font-family:ui-sans-serif,system-ui,sans-serif}
  #login .lead{display:flex;align-items:center;gap:.5rem;margin:0 0 1rem}
  #login .lead .tag{background:rgba(251,191,36,.15);color:#fcd34d;padding:1px 7px;border-radius:8px;font-size:9px;
    font-weight:700;letter-spacing:.06em;border:1px solid rgba(251,191,36,.25)}
  #login .lead p{margin:0;color:#6e7d96;font-size:11px}
  input{width:100%;margin:.3rem 0;padding:.55rem .7rem;background:#080b13;color:#e2e8f0;border:1px solid #202c40;border-radius:9px;font:inherit}
  input:focus{outline:none;border-color:#6366f1}
  button{cursor:pointer;border:0;border-radius:9px;padding:.55rem .9rem;color:#fff;font:inherit;font-weight:600;background:linear-gradient(135deg,#6d6cf6,#8b5cf6)}
  #err{color:#fb7185;font-size:11px;height:1rem;margin-top:.4rem}
  #app{display:none;flex:1;min-height:0;flex-direction:column}
  /* title bar — amber top edge reinforces the root/recovery danger context */
  #bar{display:flex;align-items:center;gap:.7rem;font-size:11px;color:#6e7d96;
    padding:.5rem .85rem;background:#0b101b;border-bottom:1px solid #1b2336;
    box-shadow:inset 0 2px 0 0 rgba(245,158,11,.55);min-height:38px}
  #bar .brand{font-weight:700;color:#cbd5e1;letter-spacing:.02em}
  #bar .tag{background:rgba(251,191,36,.15);color:#fcd34d;padding:2px 8px;border-radius:9px;font-weight:600;
    letter-spacing:.04em;border:1px solid rgba(251,191,36,.25)}
  #st{display:inline-flex;align-items:center;gap:6px;color:#94a3b8}
  #st .dot{width:8px;height:8px;border-radius:50%;background:#64748b;flex:none}
  #st.live{color:#a7f3d0}#st.live .dot{background:#10b981;animation:pulse 2s infinite}
  #st.dead{color:#fca5a5}#st.dead .dot{background:#ef4444}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(16,185,129,.45)}70%{box-shadow:0 0 0 6px rgba(16,185,129,0)}100%{box-shadow:0 0 0 0 rgba(16,185,129,0)}}
  #bar .sp{margin-left:auto;display:flex;align-items:center;gap:.7rem}
  #bar a{color:#94a3b8;cursor:pointer}#bar a:hover{color:#a5b4fc}
  #bar a#restart{color:#fbbf24}#bar a#restart:hover{color:#fde68a}
  #rov{position:fixed;inset:0;z-index:9;background:rgba(8,11,19,.92);display:none;
    align-items:center;justify-content:center;text-align:center}
  #rov .t{font-weight:600;font-size:14px}#rov .m{color:#94a3b8;font-size:11px;margin-top:6px}
  #hint{color:#34d399;opacity:0;transition:opacity .2s;flex:none}
  #help{color:#475569;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
  #host{flex:1;min-height:0;overflow:hidden;background:#0b0b10;padding:10px 0 0 14px}
</style></head>
<body>
  <form id="login" autocomplete="on">
    <h1>MSP AI — Recovery Console</h1>
    <div class="lead"><span class="tag">ROOT</span><p>Admin login · interactive root terminal</p></div>
    <input id="u" placeholder="username" autocomplete="username" autofocus>
    <input id="p" type="password" placeholder="password" autocomplete="current-password">
    <button type="submit" style="width:100%;margin-top:.5rem">Sign in</button>
    <div id="err"></div>
  </form>
  <div id="app">
    <div id="bar"><span class="brand">MSP AI&nbsp;AI</span><span class="tag">RECOVERY · ROOT · AUDITED</span>
      <span id="st"><span class="dot"></span><span class="lbl">connecting…</span></span>
      <span id="hint">copied</span>
      <span id="help">select to copy · Ctrl+V / right-click to paste · Ctrl+Shift+C/V too</span>
      <span class="sp"><a id="restart" title="systemctl restart msp-ai-recovery — drops this session, reconnects automatically">⟳ restart console</a><a id="logout">log out</a></span></div>
    <div id="host"></div>
  </div>
<script>
const $=s=>document.querySelector(s);
let TERM=null, WS=null;
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(b||{})});let j={};try{j=await r.json()}catch(e){}return{status:r.status,j};}
function setSt(cls,t){const e=$('#st');if(!e)return;e.className=cls;const l=e.querySelector('.lbl');if(l)l.textContent=t;}
let HINTT=null;
function hint(t){const h=$('#hint');if(!h)return;h.textContent=t;h.style.opacity='1';clearTimeout(HINTT);HINTT=setTimeout(()=>h.style.opacity='0',1100);}
// Terminal copy/paste UX (mirror of wireTermClipboard in dashboard/index.html). This console is
// plain HTTP, so the Clipboard API is usually blocked — execCommand('copy') is the working path.
function wireClip(term,host){
  const secure=window.isSecureContext&&navigator.clipboard;
  const execCopy=text=>{const ta=document.createElement('textarea');ta.value=text;
    ta.style.position='fixed';ta.style.top='-9999px';document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy')}catch(e){}document.body.removeChild(ta);try{term.focus()}catch(e){}};
  const writeClip=text=>{if(!text)return;
    if(secure)navigator.clipboard.writeText(text).then(()=>{},()=>execCopy(text));else execCopy(text);};
  const copySel=()=>{const s=term.getSelection();if(s){writeClip(s);hint('copied');return true;}return false;};
  const pasteClip=()=>{if(!secure)return false;
    navigator.clipboard.readText().then(t=>{if(t){term.paste(t);hint('pasted');}}).catch(()=>{});return true;};
  host.addEventListener('mousedown',e=>{if(e.button===0)setTimeout(()=>{try{term.focus()}catch(_){}}, 0);});
  host.addEventListener('mouseup',()=>{const s=term.getSelection();if(s){writeClip(s);hint('copied');}});
  host.addEventListener('paste',()=>hint('pasted'));
  host.addEventListener('auxclick',e=>{if(e.button===1&&pasteClip())e.preventDefault();});
  host.addEventListener('contextmenu',e=>{if(secure){e.preventDefault();pasteClip();}});
  term.attachCustomKeyEventHandler(e=>{
    if(e.type!=='keydown')return true;
    if(e.ctrlKey&&e.shiftKey&&e.code==='KeyC')return copySel()?false:true;
    if(e.ctrlKey&&e.shiftKey&&e.code==='KeyV')return pasteClip()?false:true;
    if(e.ctrlKey&&e.code==='Insert')return copySel()?false:true;
    if(e.shiftKey&&e.code==='Insert')return pasteClip()?false:true;
    return true;});
}
function initTerm(){
  if(TERM)return;
  TERM=new Terminal({fontFamily:'ui-monospace,SFMono-Regular,Menlo,monospace',fontSize:13,cursorBlink:true,allowProposedApi:true,
    theme:{background:'#0b0b10',foreground:'#e6e7ea',cursor:'#8b5cf6',selectionBackground:'#8b5cf640'}});
  const fit=new FitAddon.FitAddon();TERM.loadAddon(fit);TERM.open($('#host'));setTimeout(()=>{try{fit.fit()}catch(e){}},0);
  const proto=location.protocol==='https:'?'wss':'ws';
  WS=new WebSocket(proto+'://'+location.host+'/ws/terminal');
  const rs=()=>{try{fit.fit()}catch(e){}if(WS.readyState===1)WS.send(JSON.stringify({type:'resize',cols:TERM.cols,rows:TERM.rows}));};
  WS.onopen=()=>{setSt('live','live root shell');rs();TERM.focus();};
  WS.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='out')TERM.write(m.data);else if(m.type==='exit'){TERM.write('\\r\\n\\x1b[90m[session ended]\\x1b[0m\\r\\n');setSt('dead','session ended');}};
  WS.onclose=()=>setSt('dead','disconnected');
  WS.onerror=()=>setSt('dead','connection error');
  TERM.onData(d=>{if(WS.readyState===1)WS.send(JSON.stringify({type:'in',data:d}));});
  wireClip(TERM,$('#host'));
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
// Self-restart (mirror of the :8090 Restart button, D-36): fire the detached restart, overlay,
// poll until the new process answers, reload. The session cookie survives the restart.
$('#restart').onclick=async()=>{
  if(!confirm('Restart the recovery console?\\n\\nThe terminal session will drop and this page will reconnect automatically.'))return;
  let ov=$('#rov');
  if(!ov){ov=document.createElement('div');ov.id='rov';document.body.appendChild(ov);}
  ov.innerHTML='<div><div class="t">Restarting recovery console…</div><div class="m" id="rmsg">sending restart command…</div></div>';
  ov.style.display='flex';
  const msg=t=>{const m=$('#rmsg');if(m)m.textContent=t;};
  try{await jpost('/restart',{});}catch(_){}   // connection may drop mid-request — expected
  msg('waiting for the service to come back…');
  const t0=Date.now();
  const tick=async()=>{
    try{const r=await fetch('/whoami',{cache:'no-store',credentials:'same-origin'});
      if(r.status===200||r.status===401){msg('back online — reloading…');setTimeout(()=>location.reload(),500);return;}}catch(_){}
    if(Date.now()-t0>60000)msg('still waiting… if this persists, restart from SSH (systemctl restart msp-ai-recovery).');
    setTimeout(tick,1500);
  };
  setTimeout(tick,2500);   // give systemd a moment to stop the old process before polling
};
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
            if self.path == "/restart":
                return self._restart()
            self._json(404, {"error": "not found"})

        def _restart(self) -> None:
            """Restart THIS service (mirror of the :8090 button, D-36). Audited before it runs — the
            process is about to die. systemd-run hands the restart to PID1 so it survives us; no sudo
            needed (recovery already runs as root)."""
            u = self._admin()
            if not u:
                return self._json(401, {"error": "auth"})
            audit.record(actor=u, tenant_id="*", action="terminal",
                         tool="systemctl", detail="restart msp-ai-recovery (console button)")
            unit = get_config().get("MSPAI_RECOVERY_UNIT") or "msp-ai-recovery"
            cmd = (["systemd-run", "--collect", "systemctl", "restart", unit]
                   if shutil.which("systemd-run")
                   else ["systemctl", "restart", unit])
            try:
                subprocess.Popen(cmd, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:                       # noqa: BLE001
                return self._json(500, {"error": f"could not launch restart: {e}"})
            return self._json(200, {"ok": True, "restarting": True, "unit": unit})

    return H


def create_server(port: int = 8091, host: str = "0.0.0.0", db_path: Optional[Path] = None):
    cfg = get_config()
    auth = AuthStore(db_path)
    audit = AuditStore(db_path)
    signer = SessionSigner()
    ttl = cfg.int("MSPAI_SESSION_TTL_MIN", 720)
    handler = _make_handler(auth, signer, audit, ttl, cfg.bool("MSPAI_COOKIE_SECURE", False))
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="MSP AI recovery console (terminal-only failover)")
    p.add_argument("--port", type=int, default=8091)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args(argv)
    srv = create_server(args.port, args.host)
    print(f"MSP AI recovery console (interactive terminal) on http://{args.host}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
