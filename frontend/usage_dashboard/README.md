# Observability dashboard

Superuser operator view for Toston Chat:

- **Usage & P&L** — AI spend vs catalog subscription revenue per user
- **Activity** — turns, tools, models, cost timeseries, turn drill-down

Revenue on the P&L tab is **catalog list price** (`$4.99` / `$14.99`), pro-rated to the selected window (`window_days / 30`). It is not Polar invoice data.

## Run locally

Backend on `:8888`, then:

```bash
cd frontend/usage_dashboard
npm install
npm run dev
```

Open http://localhost:5174 and sign in with a superuser account. Access is
checked by calling an admin observability endpoint (not an `is_superuser` flag).

## Deploy (Coolify / VPS)

The `usage_dashboard` service in `docker-compose.yml` binds only to
`${TAILSCALE_IP}:5174`, the same way pgAdmin binds `${TAILSCALE_IP}:5050`.
Do **not** add a Coolify public domain or Traefik labels for this service.

On the VPS, open it over Tailscale:

```text
http://<TAILSCALE_IP>:5174
```

Nginx in the container proxies `/api` to the `backend` service on the
internal Docker network, so the browser never talks to the public API host.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/login/access-token` | Login |
| `GET /api/v1/admin/observability/summary` | Access probe + activity summary |
| `GET /api/v1/admin/observability/economics` | P&L summary + tier breakdown |
| `GET /api/v1/admin/observability/users` | Per-user P&L rows |
| `GET /api/v1/admin/observability/cost-timeseries` | Cost / tokens over time |
| `GET /api/v1/admin/observability/tools` | Tool stats |
| `GET /api/v1/admin/observability/models` | Model stats |
| `GET /api/v1/admin/observability/turns` | Recent turns |
| `GET /api/v1/admin/observability/turns/{id}` | Turn detail |
