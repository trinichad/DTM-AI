# Example Hermes skills (illustrative)

These are SAMPLE learned skills that demonstrate the format the dashboard's **Skills** page reads.
On a real server, Hermes' actual skills live in `~/.hermes/skills/` and these examples are NOT used.

The dev preview (`.claude/launch.json`) points the server at this folder via
`--hermes-skills-dir examples/hermes-skills` so the Skills page renders with content.

Format: each skill is a folder containing `SKILL.md` with `name` / `description` frontmatter,
nested under a category folder — exactly how Hermes organizes `~/.hermes/skills/`.
