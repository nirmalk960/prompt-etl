# Security Policy

Prompt ETL Studio is a self-hosted product. Your data stays on the machine that
runs the container. This document covers how to report issues, what the current
threat model assumes, and the defaults you should keep when exposing the app.

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.2.x   | Yes       |
| < 0.2.0 | No        |

Prefer the latest published tag on
[`ghcr.io/nirmalk960/prompt-etl`](https://github.com/users/nirmalk960/packages/container/package/prompt-etl).
Older tags were removed from the registry when 0.2.1 shipped.

## Reporting a vulnerability

Please **do not** open a public GitHub Issue for security findings.

1. Email a private report to the maintainer through GitHub:
   [Security advisories for this repository](https://github.com/nirmalk960/prompt-etl/security/advisories/new)
   (preferred), or open a private security advisory from the repository
   **Security** tab.
2. Include the affected version or image tag, steps to reproduce, and the
   impact (data access, auth bypass, remote code, denial of service).
3. Allow a reasonable window before public disclosure so a fix or advisory can
   ship. We will acknowledge reports as soon as we can.

If private advisories are unavailable for any reason, contact the repository
owner via their GitHub profile.

## Threat model (current)

0.2.x is a **single-operator** deployment:

- One shared `ACCESS_TOKEN` signs in as `OPERATOR_EMAIL` (default `you@local`).
- There is no multi-user identity, RBAC, or tenant isolation yet.
- Prompt-mode and Custom steps run generated code in-process with sandbox
  guards (AST / SQL checks, restricted modules, sealed frame I/O). That blocks
  common escape attempts; it is not a hard VM boundary.
- Dashboard links use HMAC-signed tokens with a short TTL. They do not require
  the access token, so treat the link as a secret for that dataset.
- Uploads use capability-style multipart URLs. Do not publish those URLs.

The product is a good fit for a laptop or a private network. It is not yet a
multi-tenant SaaS control plane.

## Hardening checklist

Keep these defaults when the host is reachable by more than you:

| Setting | Guidance |
| ------- | -------- |
| `ACCESS_TOKEN` | Leave unset so the all-in-one entrypoint generates `/data/access_token`. Rotate by replacing that file (or the volume) and restarting. |
| `ALLOW_DEV_AUTH` | Leave unset / `false`. `true` accepts `Bearer dev:<email>` and is unsafe on a shared host. |
| `SECRET_KEY` | Leave unset so `/data/secret_key` is generated. Do not use `dev-secret-change-me` outside local development. |
| `APP_ENV` | Prefer `production` when the API is exposed beyond localhost. `local` enables OpenAPI `/docs` and relaxes some local-only checks. |
| Network | Bind to localhost or put the container behind TLS (Caddy, Traefik, nginx). Do not publish `:8080` to the public internet without auth at the edge as well. |
| Volume | Treat `/data` as sensitive: token, secret key, Postgres, and object store live there. Back it up and restrict who can `docker exec`. |
| Ollama | Optional. Prompt/Custom steps talk to the host model; keep Ollama on the host network you trust. |

Read the token after first boot:

```bash
docker exec prompt-etl cat /data/access_token
```

Paste it into **Settings** in the app. Store it like any other server secret.

## What we already guard against

Regression coverage lives in `backend/tests/test_security.py` and related
engine tests. In short:

- Dev-email login is off unless `ALLOW_DEV_AUTH=true`.
- Object keys cannot traverse out of the store root.
- Prompt-generated Python cannot import OS/file helpers or call blocked APIs.
- Prompt-generated SQL cannot `read_csv` / open arbitrary files.
- Upload size is capped; nginx rejects oversized bodies.
- Dashboard signing refuses the default secret when not in local mode.

These are best-effort controls for a trusted operator. Please report anything
that bypasses them.

## Out of scope (for now)

- Multi-tenant auth (OIDC), SSO, and per-workspace quotas
- Hard isolation of untrusted tenant code (gVisor / Firecracker / separate uid)
- Guarantees about models running on the host (Ollama is user-managed)

See `docs/PHASE3_PRODUCTION.md` for the longer production roadmap.
