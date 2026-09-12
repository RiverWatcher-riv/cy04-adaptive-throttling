# Local hosting

One command, from the repo root:

```bash
docker compose -f deploy/docker-compose.yml up --build
```

Then open http://localhost:8501.

`src/` and `demo/` are bind-mounted into the container, so editing either
during rehearsal picks up on save (Streamlit auto-reloads) without a
rebuild. Rebuild (`--build`) only when `requirements.txt`,
`demo/requirements.txt`, or `pyproject.toml` change.

No cloud dependency, no external network call on the critical path —
deliberate, same reasoning as the rest of the project's local-first stance
(see the Technical Requirements note in the project's Obsidian vault).
