"""DTM AI web layer — stdlib HTTP API + self-contained dashboard.

Deliberately dependency-free (stdlib http.server): runs identically on dev and the
Ubuntu appliance, no build step, hard to break when the owner edits the UI. The API is a
thin, testable layer over the same runtime the CLI uses. Behind nginx/HTTPS in prod.
"""
