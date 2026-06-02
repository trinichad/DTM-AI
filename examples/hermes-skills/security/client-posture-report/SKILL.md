---
name: client-posture-report
description: Compile a security posture summary for a client from Cylance threats, Huntress incidents, and MFA gaps.
category: security
---

# Client Security Posture Report

Composition (read-only primitives):
1. `cylance_list_threats` — active threats + classifications
2. `huntress_list_incidents` — open incident reports
3. `kaseya_list_assets` — fleet size for context
4. summarize → `memory_note` (save highlights to the client's notebook)

Output: a prioritized, sourced summary. No writes to client systems.
