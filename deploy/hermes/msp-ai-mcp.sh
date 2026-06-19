#!/usr/bin/env bash
# Launcher for the MSP AI MCP server, for use as a Hermes `mcp_servers` command.
# Guarantees the right working dir + import path regardless of how Hermes invokes it.
#
# Hermes config (~/.hermes/config.yaml):
#   mcp_servers:
#     mspai_acme:
#       command: /opt/msp-ai/deploy/hermes/msp-ai-mcp.sh
#       args: ["--tenant", "acme"]
#
# Set MSPAI_AI_HOME if the project lives somewhere other than /opt/msp-ai.
set -euo pipefail
MSPAI_AI_HOME="${MSPAI_AI_HOME:-/opt/msp-ai}"
cd "$MSPAI_AI_HOME"
exec python3 -m execution.mcp_server "$@"
