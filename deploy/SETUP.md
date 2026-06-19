# MSP AI — Ubuntu Install Guide

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
sudo useradd --system --create-home --shell /bin/bash msp-ai
sudo mkdir -p /opt/msp-ai && sudo chown msp-ai:msp-ai /opt/msp-ai
```

## 3. Get the code
```bash
sudo -u msp-ai git clone <your MSP-AI repo URL> /opt/msp-ai
# future updates:  sudo -u msp-ai git -C /opt/msp-ai pull && sudo systemctl restart msp-ai
```

## 4. Configure `.env`
```bash
sudo -u msp-ai cp /opt/msp-ai/.env.example /opt/msp-ai/.env
sudo -u msp-ai chmod 600 /opt/msp-ai/.env
sudo -u msp-ai nano /opt/msp-ai/.env
```
Minimum: set `MSPAI_ENV=prod`, point `MSPAI_OLLAMA_URL` at your local Ollama, set a strong
`MSPAI_ADMIN_PASSWORD`. Vendor + cloud keys are best added later in the dashboard (Integrations).

## 5. Local LLM (Ollama)
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:14b           # a tool-capable model with >=32k context
```
Set `MSPAI_LOCAL_MODEL=qwen2.5:14b` in `.env`.

## 6. Run as a service
```bash
sudo cp /opt/msp-ai/deploy/msp-ai.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now msp-ai
sudo systemctl status msp-ai        # should be active; logs: journalctl -u msp-ai -f
```
On first run it prints a generated admin password (or uses `MSPAI_ADMIN_PASSWORD`).

## 7. HTTPS (nginx + Let's Encrypt)
```bash
sudo apt -y install certbot python3-certbot-nginx
sudo cp /opt/msp-ai/deploy/nginx-msp-ai.conf /etc/nginx/sites-available/msp-ai
sudo ln -s /etc/nginx/sites-available/msp-ai /etc/nginx/sites-enabled/
# edit the server_name, then:
sudo certbot --nginx -d msp-ai.your-domain.com
# set MSPAI_COOKIE_SECURE=1 in .env, then: sudo systemctl restart msp-ai
```

## 8. Firewall
```bash
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
```

## 9. Verify
```bash
sudo -u msp-ai python3 -m execution.cli probe        # integration creds (add via dashboard)
sudo -u msp-ai python3 -m execution.cli health --tenant '*'
```
Then open `https://msp-ai.your-domain.com`, log in, and:
1. **Integrations** → add Kaseya / Cylance / Huntress keys → **Test** (goes green).
2. **Capabilities** → confirm read tools enabled (read-only by default).
3. **Chat** → ask about a client.

## 10. (Optional) Hermes brain
Follow `deploy/hermes/SETUP_HERMES.md` to wire Nous Hermes Agent to the MCP server, fenced.

## Backups & rollback
- Back up `/opt/msp-ai/msp_ai.db`, `/opt/msp-ai/.env`, `/opt/msp-ai/secrets.local`, and
  `/opt/msp-ai/vault/` (client memory/KB). Everything else is in git.
- Rollback: `git -C /opt/msp-ai checkout <prev-commit> && sudo systemctl restart msp-ai`.
- Kill switch: disable any tool in the Capability Console; `sudo systemctl stop msp-ai` to halt.
