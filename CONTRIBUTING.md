# Contributing

Thanks for helping with Prompt ETL Studio. This is the first public release of
the project. Clear, focused changes land faster than large rewrites.

## Ways to help

- **Bug reports** — open an [Issue](https://github.com/nirmalk960/prompt-etl/issues)
  with the image tag or commit, what you did, what you expected, and what
  happened. Logs from `docker logs prompt-etl` help.
- **Feature ideas** — open an Issue first so we can align on scope. The Phase 1
  catalog and self-hosted single-operator model are intentional; please read
  the README and `docs/` before proposing large new surfaces.
- **Pull requests** — fix bugs, add tests, improve docs, or extend an existing
  operation. Prefer small PRs that do one thing.

Security findings belong in a private advisory, not a public Issue. See
[`SECURITY.md`](SECURITY.md).

## Development setup

Prerequisites: Docker 24+, Node 20+, Python 3.11. Roughly 8 GB free RAM for a
full local stack with Ollama.

```bash
git clone https://github.com/nirmalk960/prompt-etl.git
cd prompt-etl
cp .env.example .env
```

Two ways to run:

1. **Dev stack** (hot reload) — `docker compose up` from the repo root. Frontend
   at http://localhost:5173, API behind the compose services.
2. **All-in-one image** — `./scripts/install.sh` or build
   `deploy/Dockerfile.allinone` and run on port 8080. Token:
   `docker exec prompt-etl cat /data/access_token`.

Backend only:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Frontend only:

```bash
cd frontend
npm install
npm run dev
npm run typecheck
```

Never commit `.env`, tokens, or anything under `/data`. `.env.example` is the
template.

## Project map

| Area | Path |
| ---- | ---- |
| API, workers, engines, ops catalog | `backend/prompt_etl/` |
| Canvas UI | `frontend/src/` |
| All-in-one image | `deploy/` |
| Helm skeleton | `charts/prompt-etl/` |
| Design notes | `docs/` |
| Demo boards | `examples/` |

Operations are catalog-driven (`backend/prompt_etl/ops/`). New boxes should
register there so the palette, inspector, and preflight stay in sync. Prompt /
Custom paths must keep the sandbox and `agent_guard` checks intact — do not
weaken them to make a demo pass.

## Pull request checklist

- [ ] One clear purpose; description says *why*
- [ ] Tests for the change when it touches auth, sandbox, object keys, or ops
- [ ] `cd backend && pytest` passes locally
- [ ] Frontend typecheck passes if you touched `frontend/`
- [ ] No secrets, real datasets, or personal tokens in the diff
- [ ] Docs updated when behaviour or env vars change

## Testing tips

```bash
# unit suite (ops, engines, security, dashboards)
cd backend && pytest

# end-to-end matrix against a running instance
python .cursor/skills/testing-prompt-etl/scripts/run_matrix.py --api http://localhost:8080/api
```

If you add a security-sensitive path, extend `backend/tests/test_security.py`.

## Code style

- Match the file you edit: existing naming, imports, and error style.
- Prefer small, readable functions over clever abstractions.
- Keep configure-mode the default for new operations. Prompt mode is an
  escape hatch, not the only way to use a box.

## License

By contributing, you agree that your contributions are licensed under the
same [MIT License](LICENSE) as the rest of the project.
