# Investment Dashboard

A simple web app to demonstrate the Investment Dashboard API.

## Features

- **Dashboard**: Portfolio overview with total value, allocations by class/currency/market
- **Assets**: Add and manage trackable assets (stocks, ETFs, crypto, bonds, etc.)
- **Holdings**: Track your positions with cost basis and gain/loss
- **Transactions**: Record buy/sell/dividend transactions

## Quick Start

### 1. Start the Backend API

```bash
cd backend/app
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2. Run Database Migration

```bash
cd backend/app
alembic upgrade head
```

### 3. Start the Dashboard Server

```bash
cd investment_dashboard
python serve.py
```

Then open http://localhost:3000 in your browser.

## API Endpoints Used

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/investments/portfolio/summary` | Portfolio overview |
| `GET /api/v1/investments/portfolio/allocation/by-class` | Allocation by asset class |
| `GET /api/v1/investments/portfolio/allocation/by-currency` | Allocation by currency |
| `GET /api/v1/investments/portfolio/allocation/by-market` | Allocation by market |
| `GET /api/v1/investments/portfolio/top-holdings` | Top holdings by value |
| `GET /api/v1/investments/assets` | List assets |
| `POST /api/v1/investments/assets` | Create asset |
| `GET /api/v1/investments/assets/{id}/price` | Get asset price |
| `POST /api/v1/investments/assets/refresh-prices` | Refresh all prices |
| `GET /api/v1/investments/holdings` | List holdings |
| `POST /api/v1/investments/holdings` | Create holding |
| `DELETE /api/v1/investments/holdings/{id}` | Delete holding |
| `GET /api/v1/investments/transactions` | List transactions |
| `POST /api/v1/investments/transactions` | Record transaction |

## Authentication

The API requires authentication. For testing, you can either:

1. **Disable auth for development**: Modify the endpoints to not require authentication
2. **Add a login flow**: Update `app.js` to handle authentication

To set a token manually in the browser console:

```javascript
authToken = 'your-jwt-token-here';
loadDashboard();
```

## Example Workflow

1. **Add Assets**:
   - Go to "Assets" tab
   - Click "Add Asset"
   - Add stocks like `AAPL` (Apple), `AMXL.MX` (América Móvil in BMV)
   - Add crypto like `BTC`, `ETH`
   - Add CETES or bonds

2. **Create Holdings**:
   - Go to "Holdings" tab
   - Click "Add Holding"
   - Select an asset and enter quantity/cost basis

3. **Record Transactions**:
   - Go to "Transactions" tab
   - Click "Record Transaction"
   - Record buys/sells to update holdings

4. **View Dashboard**:
   - See portfolio summary
   - View allocations by class, currency, market
   - Refresh prices to update values

## Supported Asset Types

| Asset Class | Types | Price Source |
|-------------|-------|--------------|
| Equities | Stock, ETF | Yahoo Finance |
| Fixed Income | Bond, CETES, Treasury | Manual |
| Crypto | Cryptocurrency | CoinGecko |
| Funds | Mutual Fund, Index Fund | Manual |

## Markets

- **BMV**: Mexican stocks (e.g., `AMXL.MX`, `FEMSAUBD.MX`)
- **NYSE/NASDAQ**: US stocks (e.g., `AAPL`, `MSFT`, `VOO`)
- **CRYPTO**: Cryptocurrencies (e.g., `BTC`, `ETH`)
- **OTC**: Bonds, CETES, mutual funds

