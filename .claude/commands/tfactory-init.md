# /tfactory-init

Run the **tfactory-init** skill to scaffold a `.tfactory.yml` plus an empty
`.tfactory/tests-catalog.json` for the current repo. The skill interactively
collects the targets (HTTP / Kubernetes / docker-compose / feature-flag),
auth env-var names (never values), optional `test_data` seed/reset hooks,
and validates the rendered YAML against the `TFactoryConfig` Pydantic
schema before writing.

See `.claude/skills/tfactory-init/SKILL.md` for the full procedure.
