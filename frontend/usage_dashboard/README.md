# Observability dashboard

Superuser operator view for Toston Chat:

- **Usage & P&L** — AI spend vs catalog subscription revenue per user
- **Activity** — turns, tools, models, cost timeseries, turn drill-down

Revenue on the P&L tab is **catalog list price** (`$4.99` / `$14.99`), pro-rated to the selected window (`window_days / 30`). It is not Polar invoice data.

## Run

Backend on `:8888`, then:

```bash
cd frontend/usage_dashboard
npm install
npm run dev
```

Open http://localhost:5174 and sign in with an operator account. Access is
checked by calling an admin observability endpoint (not an `is_superuser` flag).

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
