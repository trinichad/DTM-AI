#!/usr/bin/env bash
# Launcher for the DTM AI MCP server, for use as a Hermes `mcp_servers` command.
# Guarantees the right working dir + import path regardless of how Hermes invokes it.
#
# Hermes config (~/.hermes/config.yaml):
#   mcp_servers:
#     dtm_acme:
#       command: /opt/dtm-ai/deploy/hermes/dtm-ai-mcp.sh
#       args: ["--tenant", "acme"]
#
# Set DTM_AI_HOME if the project lives somewhere other than /opt/dtm-ai.
set -euo pipefail
DTM_AI_HOME="${DTM_AI_HOME:-/opt/dtm-ai}"
cd "$DTM_AI_HOME"
exec python3 -m execution.mcp_server "$@"
