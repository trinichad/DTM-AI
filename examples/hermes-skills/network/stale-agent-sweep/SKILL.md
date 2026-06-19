---
name: stale-agent-sweep
description: Find endpoints that haven't checked in recently across Huntress and Kaseya for a client.
category: network
---

# Stale Agent Sweep

1. `huntress_list_agents` — last_seen_at per agent
2. `kaseya_list_assets` — LastSeenDate per asset
3. cross-reference + flag anything quiet > 7 days

Read-only. Returns a list of stale hosts with last-seen timestamps and source citations.
