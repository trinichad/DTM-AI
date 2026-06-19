#!/usr/bin/env bash
# Create the MSP AI specialist agent team as Hermes profiles, each with its SOUL + a
# kanban-routing description. AtlasOps Manager is the DEFAULT (active) profile — chat flows
# through it and it delegates to the specialists. Idempotent: re-running updates SOULs in place.
#
# Run on the server (as youradmin — needs docker exec + sudo -u msp-ai):
#   bash /opt/msp-ai/deploy/hermes/setup-agents.sh
set -euo pipefail
SOULS=/opt/msp-ai/deploy/hermes/souls
HX="docker exec -u hermes -e HERMES_HOME=/opt/data hermes /opt/hermes/bin/hermes"
DATA=/srv/hermes-data

# specialist profile_name -> kanban description (what the manager routes to it)
names=(tenantsmith patchwright domainforge sentinelops netwarden vaultkeeper deskpilot)
desc_tenantsmith="Microsoft 365 & Entra: identity security, MFA, licensing, mail flow, Intune."
desc_patchwright="Kaseya endpoint operations: patch compliance, agent health, monitoring, automation (LIVE: Kaseya VSA)."
desc_domainforge="Windows Server infrastructure: Active Directory, DNS, DHCP, GPO, server health."
desc_sentinelops="Security operations: Cylance + Huntress detection/response, endpoint protection coverage (LIVE)."
desc_netwarden="Network infrastructure: SonicWall, Ubiquiti, VPN, wireless, routing, switching."
desc_vaultkeeper="Backup & recovery: Datto, Veeam, Synology, RPO/RTO, restore testing."
desc_deskpilot="Service desk: Freshdesk ticket triage, SLA, escalation, client communication."

for name in "${names[@]}"; do
  d="desc_${name}"; descr="${!d}"
  if $HX profile list 2>/dev/null | grep -qw "$name"; then
    echo "• $name exists — updating description + SOUL"
    $HX profile describe "$name" "$descr" >/dev/null 2>&1 || true
  else
    echo "• creating $name"
    $HX profile create "$name" --clone --description "$descr" >/dev/null 2>&1
  fi
  sudo -n -u msp-ai cp "$SOULS/$name.md" "$DATA/profiles/$name/SOUL.md"
done

# AtlasOps Manager = the default profile (the active one chat/api_server serves)
sudo -n -u msp-ai cp "$SOULS/atlasops.md" "$DATA/SOUL.md"
$HX profile describe default "AtlasOps Manager — Virtual IT Operations Director; coordinates and delegates to all specialist agents; owns risk, SLA, and documentation." >/dev/null 2>&1 || true

echo "=== profiles now ==="
$HX profile list 2>&1
