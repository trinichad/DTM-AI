---
name: weekly-client-digest
description: Build a plain-English weekly digest for a client — assets, new threats, incidents, and anything learned.
category: reporting
---

# Weekly Client Digest

1. `system_health` + `kaseya_list_assets` — environment snapshot
2. `cylance_list_threats` + `huntress_list_incidents` — week's security events
3. `memory_read` — recall standing context for this client
4. compose digest; save notable items via `memory_note`

Read-only. A reusable composition the team can trigger per client each week.
