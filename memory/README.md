# memory/ — living deployment state

This directory holds the agent's living, human-readable state for **this deployment**
(`decisions.md`, `progress.md`, `findings.md`, `task_plan.md`). It is **gitignored** because
it accumulates environment-specific details (client names, hosts, people). Each install keeps
its own. Start empty; the agent and owner append over time.
