# DTM AI — Ubuntu Install Guide

From-zero install on a fresh **Ubuntu 22.04/24.04** server. The platform's core + web layer are
**stdlib-only** (no virtualenv, no build step) — install is mostly "copy files, set a service".
Cloud LLMs and the optional Hermes brain are added later from the dashboard.

> Deploying as a new repo or migrating the existing Kaseya AI box? See `../memory/decisions.md` D-14.

## 1. System packages
```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3 git nginx ufw   # Python 3.11+ ; nginx for HTTPS
python3 --version                            # confirm >= 3.11
```

## 2. Service user + location
```bash
sudo useradd --system --create-home --shell /bin/bash dtm-ai
sudo mkdir -p /opt/dtm-ai && sudo chown dtm-ai:dtm-ai /opt/dtm-ai
```

## 3. Get the code
```bash
sudo -u dtm-ai git clone <your DTM-AI repo URL> /opt/dtm-ai
# future updates:  sudo -u dtm-ai git -C /opt/dtm-ai pull && sudo systemctl restart dtm-ai
```

## 4. Configure `.env`
```bash
sudo -u dtm-ai cp /opt/dtm-ai/.env.example /opt/dtm-ai/.env
sudo -u dtm-ai chmod 600 /opt/dtm-ai/.env
sudo -u dtm-ai nano /opt/dtm-ai/.env
```
Minimum: set `DTM_ENV=prod`, point `DTM_OLLAMA_URL` at your local Ollama, set a strong
`DTM_ADMIN_PASSWORD`. Vendor + cloud keys are best added later in the dashboard (Integrations).

## 5. Local LLM (Ollama)
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:14b           # a tool-capable model with >=32k context
```
Set `DTM_LOCAL_MODEL=qwen2.5:14b` in `.env`.

## 6. Run as a service
```bash
sudo cp /opt/dtm-ai/deploy/dtm-ai.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dtm-ai
sudo systemctl status dtm-ai        # should be active; logs: journalctl -u dtm-ai -f
```
On first run it prints a generated admin password (or uses `DTM_ADMIN_PASSWORD`).

## 7. HTTPS (nginx + Let's Encrypt)
```bash
sudo apt -y install certbot python3-certbot-nginx
sudo cp /opt/dtm-ai/deploy/nginx-dtm-ai.conf /etc/nginx/sites-available/dtm-ai
sudo ln -s /etc/nginx/sites-available/dtm-ai /etc/nginx/sites-enabled/
# edit the server_name, then:
sudo certbot --nginx -d dtm-ai.your-domain.com
# set DTM_COOKIE_SECURE=1 in .env, then: sudo systemctl restart dtm-ai
```

## 8. Firewall
```bash
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
```

## 9. Verify
```bash
sudo -u dtm-ai python3 -m execution.cli probe        # integration creds (add via dashboard)
sudo -u dtm-ai python3 -m execution.cli health --tenant '*'
```
Then open `https://dtm-ai.your-domain.com`, log in, and:
1. **Integrations** → add Kaseya / Cylance / Huntress keys → **Test** (goes green).
2. **Capabilities** → confirm read tools enabled (read-only by default).
3. **Chat** → ask about a client.

## 10. (Optional) Hermes brain
Follow `deploy/hermes/SETUP_HERMES.md` to wire Nous Hermes Agent to the MCP server, fenced.

## Backups & rollback
- Back up `/opt/dtm-ai/dtm_ai.db`, `/opt/dtm-ai/.env`, `/opt/dtm-ai/secrets.local`, and
  `/opt/dtm-ai/vault/` (client memory/KB). Everything else is in git.
- Rollback: `git -C /opt/dtm-ai checkout <prev-commit> && sudo systemctl restart dtm-ai`.
- Kill switch: disable any tool in the Capability Console; `sudo systemctl stop dtm-ai` to halt.
