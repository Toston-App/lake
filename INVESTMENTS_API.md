# Investments API Documentation

This document provides comprehensive documentation for the Investments API, designed to help you build a frontend application that manages investment portfolios, tracks holdings, records transactions, and displays portfolio analytics.

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Enums & Types Reference](#enums--types-reference)
4. [API Endpoints](#api-endpoints)
   - [Assets](#assets-endpoints)
   - [Holdings](#holdings-endpoints)
   - [Transactions](#transactions-endpoints)
   - [Portfolio Analytics](#portfolio-analytics-endpoints)
5. [Complete User Workflow](#complete-user-workflow)
6. [External Asset Search Workflow](#external-asset-search-workflow)
7. [Error Handling](#error-handling)
8. [Quick Reference Table](#quick-reference-table)

---

## Overview

### Base URL

```
{API_BASE_URL}/api/v1/investments
```

Replace `{API_BASE_URL}` with your backend server URL (e.g., `http://localhost:8000` for local development).

### Content Type

All requests must include:

```
Content-Type: application/json
```

### Key Features

- **Multi-currency support**: USD and MXN (Mexican Peso)
- **Multi-market support**: NYSE, NASDAQ, BMV (Mexican Stock Exchange), Crypto, OTC
- **Asset classes**: Equities, Fixed Income, Crypto, Funds
- **Real-time pricing**: Integration with Yahoo Finance and CoinGecko
- **Automatic currency conversion**: USD/MXN exchange rates
- **Portfolio analytics**: Allocations by class, currency, market, type, and country

---

## Authentication

All investment endpoints require JWT authentication. Include the token in the `Authorization` header.

### Obtaining a Token

**Endpoint:** `POST {API_BASE_URL}/api/v1/login/access-token`

**Request:**

```javascript
const response = await fetch(`${API_BASE_URL}/api/v1/login/access-token`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/x-www-form-urlencoded',
  },
  body: new URLSearchParams({
    username: 'user@example.com',
    password: 'yourpassword',
  }),
});

const data = await response.json();
const token = data.access_token;
```

**Response:**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### Using the Token

Include the token in all subsequent requests:

```javascript
const headers = {
  'Content-Type': 'application/json',
  'Authorization': `Bearer ${token}`,
};
```

### Investment Data Security Rules

All investment endpoints are protected by an environment-controlled feature gate:

```env
INVESTMENTS_ENABLED=true
INVESTMENTS_ALLOWED_USER_IDS=1,42
INVESTMENTS_ALLOWED_USER_UUIDS=user_abc,user_xyz
```

The feature is disabled by default. When enabled, active superusers and active users whose
database ID or UUID appears in the corresponding comma-separated allowlist can access
`/api/v1/investments/**`. Other authenticated users receive `403 Forbidden`.

- Asset records are global and shared by all portfolios. Only superusers may create,
  update, or deactivate them directly. Regular users can still add assets through the
  holdings and `transactions/with-asset` flows, which verify the identity with the
  configured external provider.
- Holdings and transactions are owner-scoped. Account, holding, and transaction IDs
  cannot be used to access or mutate another user's portfolio.
- Valuations, totals, currencies, and exchange rates are calculated by the server.
  Requests containing server-owned fields are rejected.
- Position-changing transactions are serialized and committed atomically. Sells and
  transfers that exceed the available quantity are rejected without creating a ledger row.
- Shared asset creation is performed only after account ownership succeeds and is committed
  atomically with the resulting holding and transaction.
- Forced price refresh requires an existing holding in the asset or superuser privileges;
  cached global prices remain readable by authenticated users.
- Investment transactions are immutable. Record a correcting transaction instead of
  deleting historical activity.
- Collection limits are capped at 100, external search terms at 100 characters, and
  external search/refresh operations are throttled.

### Helper Function

Use this helper for all authenticated API calls:

```javascript
const API_BASE_URL = '{API_BASE_URL}';
let authToken = null; // Set after login

async function apiCall(endpoint, options = {}) {
  const url = `${API_BASE_URL}/api/v1/investments${endpoint}`;
  
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${authToken}`,
      ...options.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'API request failed');
  }

  return response.json();
}
```

---

## Enums & Types Reference

### AssetClass

Broad categories for organizing investments.

| Value | Description |
|-------|-------------|
| `equities` | Stocks and equity-based investments |
| `fixed_income` | Bonds, CETES, treasury securities |
| `crypto` | Cryptocurrencies |
| `funds` | Mutual funds, ETFs, index funds |

### AssetType

Specific investment instrument types.

| Value | Description | Auto-assigned Class |
|-------|-------------|---------------------|
| `stock` | Individual company stocks | `equities` |
| `etf` | Exchange-traded funds | `funds` |
| `bond` | Corporate or government bonds | `fixed_income` |
| `cetes` | Mexican treasury certificates | `fixed_income` |
| `treasury` | US Treasury securities | `fixed_income` |
| `cryptocurrency` | Bitcoin, Ethereum, etc. | `crypto` |
| `mutual_fund` | Actively managed funds | `funds` |
| `index_fund` | Passively managed index funds | `funds` |

### Currency

Supported currencies for transactions and valuations.

| Value | Description |
|-------|-------------|
| `USD` | United States Dollar |
| `MXN` | Mexican Peso |

### Market

Stock exchanges and trading venues.

| Value | Description |
|-------|-------------|
| `NYSE` | New York Stock Exchange |
| `NASDAQ` | NASDAQ Stock Exchange |
| `BMV` | Bolsa Mexicana de Valores (Mexican Stock Exchange) |
| `CRYPTO` | Cryptocurrency exchanges |
| `OTC` | Over-the-counter markets |

### TransactionType

Types of investment transactions.

| Value | Description |
|-------|-------------|
| `buy` | Purchase of assets |
| `sell` | Sale of assets |
| `dividend` | Dividend payment received |
| `split` | Stock split adjustment |
| `transfer_in` | Transfer of assets into the account |
| `transfer_out` | Transfer of assets out of the account |

---

## API Endpoints

### Assets Endpoints

Assets represent trackable financial instruments (stocks, bonds, crypto, etc.).

---

#### 1. List Assets

Retrieve all tracked assets with optional filtering.

**Endpoint:** `GET /assets`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `skip` | integer | 0 | Pagination offset |
| `limit` | integer | 100 | Number of results (max 100) |
| `asset_class` | string | - | Filter by: `equities`, `fixed_income`, `crypto`, `funds` |
| `asset_type` | string | - | Filter by: `stock`, `etf`, `bond`, `cetes`, `treasury`, `cryptocurrency`, `mutual_fund`, `index_fund` |
| `currency` | string | - | Filter by: `USD`, `MXN` |
| `market` | string | - | Filter by: `NYSE`, `NASDAQ`, `BMV`, `CRYPTO`, `OTC` |
| `is_active` | boolean | true | Filter by active status |

**Response Schema:**

```typescript
interface Asset {
  id: number;
  symbol: string;
  name: string;
  asset_class: AssetClass;
  asset_type: AssetType;
  currency: Currency;
  market: Market;
  sector: string | null;
  country: string | null;
  is_active: boolean;
}
```

**Example:**

```javascript
// List all US equities
const assets = await apiCall('/assets?asset_class=equities&market=NYSE');

// List all cryptocurrencies
const cryptoAssets = await apiCall('/assets?asset_class=crypto');

// List all Mexican assets
const mxnAssets = await apiCall('/assets?currency=MXN&market=BMV');
```

**Response:**

```json
[
  {
    "id": 1,
    "symbol": "AAPL",
    "name": "Apple Inc.",
    "asset_class": "equities",
    "asset_type": "stock",
    "currency": "USD",
    "market": "NASDAQ",
    "sector": "Technology",
    "country": "US",
    "is_active": true
  },
  {
    "id": 2,
    "symbol": "BTC",
    "name": "Bitcoin",
    "asset_class": "crypto",
    "asset_type": "cryptocurrency",
    "currency": "USD",
    "market": "CRYPTO",
    "sector": null,
    "country": null,
    "is_active": true
  }
]
```

---

#### 2. Create Asset

Create a new asset to track.

**Permission:** Superuser only. Regular users should use `POST /holdings` or
`POST /transactions/with-asset` with a provider and external ID.

**Endpoint:** `POST /assets`

**Request Schema:**

```typescript
interface AssetCreate {
  symbol: string;           // Required, will be uppercased
  name: string;             // Required
  asset_type: AssetType;    // Required
  asset_class?: AssetClass; // Optional, auto-inferred from asset_type
  currency?: Currency;      // Default: "USD"
  market?: Market;          // Default: "NYSE"
  sector?: string;          // Optional
  country?: string;         // Default: "US"
  is_active?: boolean;      // Default: true
}
```

**Example:**

```javascript
// Create a stock
const newStock = await apiCall('/assets', {
  method: 'POST',
  body: JSON.stringify({
    symbol: 'NVDA',
    name: 'NVIDIA Corporation',
    asset_type: 'stock',
    currency: 'USD',
    market: 'NASDAQ',
    sector: 'Technology',
    country: 'US',
  }),
});

// Create a Mexican CETE
const newCete = await apiCall('/assets', {
  method: 'POST',
  body: JSON.stringify({
    symbol: 'CETE-240307',
    name: 'CETES 28 días Mar 2024',
    asset_type: 'cetes',
    currency: 'MXN',
    market: 'BMV',
    country: 'MX',
  }),
});

// Create a cryptocurrency
const newCrypto = await apiCall('/assets', {
  method: 'POST',
  body: JSON.stringify({
    symbol: 'ETH',
    name: 'Ethereum',
    asset_type: 'cryptocurrency',
    currency: 'USD',
    market: 'CRYPTO',
  }),
});
```

**Response:** Returns the created `Asset` object.

---

#### 3. Search Internal Assets

Search assets already in your database by symbol or name.

**Endpoint:** `GET /assets/search`

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `q` | string | Yes | Search query (symbol or name) |

**Example:**

```javascript
// Search for Apple
const results = await apiCall('/assets/search?q=AAPL');

// Search by name
const techStocks = await apiCall('/assets/search?q=tech');
```

---

#### 4. Search External Assets

Search external sources (Yahoo Finance) for assets not yet in your database. This is the primary way to discover new assets to track.

**Endpoint:** `GET /assets/search-external`

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `q` | string | Yes | Search query (symbol or company name) |

**Response Schema:**

```typescript
interface ExternalAssetSearchResult {
  symbol: string;
  name: string;
  asset_type: AssetType;
  market: Market;
  currency: Currency;
  country: string;
  exchange: string | null;
}
```

**Example:**

```javascript
// Search for Tesla
const results = await apiCall('/assets/search-external?q=TSLA');

// Search for Mexican stocks
const mxResults = await apiCall('/assets/search-external?q=AMXL');
```

**Response:**

```json
[
  {
    "symbol": "TSLA",
    "name": "Tesla, Inc.",
    "asset_type": "stock",
    "market": "NASDAQ",
    "currency": "USD",
    "country": "US",
    "exchange": "NASDAQ"
  },
  {
    "symbol": "TSLA.MX",
    "name": "Tesla Inc",
    "asset_type": "stock",
    "market": "BMV",
    "currency": "MXN",
    "country": "MX",
    "exchange": "BMV"
  }
]
```

---

#### 5. Search Cryptocurrencies

Search for cryptocurrencies from CoinGecko. This endpoint allows you to discover and add crypto assets to your portfolio.

**Endpoint:** `GET /assets/search-crypto`

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `q` | string | Yes | Search query (symbol or cryptocurrency name) |

**Response Schema:**

```typescript
interface ExternalCryptoSearchResult {
  symbol: string;
  name: string;
  asset_type: AssetType;  // Always "cryptocurrency"
  market: Market;         // Always "CRYPTO"
  currency: Currency;     // Always "USD"
  coingecko_id: string;   // CoinGecko ID for price fetching
  market_cap_rank: number | null;  // Market cap rank (1 = highest)
}
```

**Example:**

```javascript
// Search for Bitcoin
const results = await apiCall('/assets/search-crypto?q=bitcoin');

// Search by symbol
const ethResults = await apiCall('/assets/search-crypto?q=ETH');

// Search for Dogecoin
const dogeResults = await apiCall('/assets/search-crypto?q=doge');
```

**Response:**

```json
[
  {
    "symbol": "BTC",
    "name": "Bitcoin",
    "asset_type": "cryptocurrency",
    "market": "CRYPTO",
    "currency": "USD",
    "coingecko_id": "bitcoin",
    "market_cap_rank": 1
  },
  {
    "symbol": "ETH",
    "name": "Ethereum",
    "asset_type": "cryptocurrency",
    "market": "CRYPTO",
    "currency": "USD",
    "coingecko_id": "ethereum",
    "market_cap_rank": 2
  }
]
```

---

#### 5. Get Asset by ID

Retrieve a specific asset with its current price.

**Endpoint:** `GET /assets/{asset_id}`

**Response Schema:**

```typescript
interface AssetWithPrice extends Asset {
  current_price: number | null;
  price_currency: Currency | null;
  price_change: number | null;
  price_change_percent: number | null;
  price_updated_at: string | null; // ISO datetime
}
```

**Example:**

```javascript
const asset = await apiCall('/assets/1');
```

**Response:**

```json
{
  "id": 1,
  "symbol": "AAPL",
  "name": "Apple Inc.",
  "asset_class": "equities",
  "asset_type": "stock",
  "currency": "USD",
  "market": "NASDAQ",
  "sector": "Technology",
  "country": "US",
  "is_active": true,
  "current_price": 178.52,
  "price_currency": "USD",
  "price_change": 2.35,
  "price_change_percent": 1.33,
  "price_updated_at": "2024-01-15T16:00:00Z"
}
```

---

#### 6. Update Asset

Update an existing asset's details.

**Permission:** Superuser only.

**Endpoint:** `PUT /assets/{asset_id}`

**Request Schema:**

```typescript
interface AssetUpdate {
  symbol?: string;
  name?: string;
  asset_type?: AssetType;
  asset_class?: AssetClass;
  currency?: Currency;
  market?: Market;
  sector?: string;
  country?: string;
  is_active?: boolean;
}
```

**Example:**

```javascript
const updatedAsset = await apiCall('/assets/1', {
  method: 'PUT',
  body: JSON.stringify({
    sector: 'Consumer Electronics',
    is_active: true,
  }),
});
```

---

#### 7. Delete Asset

Deactivate an asset.

**Endpoint:** `DELETE /assets/{asset_id}`

**Permission:** Superuser only. Deactivation is used to preserve references from existing
portfolios and price history.

**Example:**

```javascript
await apiCall('/assets/1', { method: 'DELETE' });
```

---

#### 8. Get Asset Price

Fetch the current market price for an asset.

**Endpoint:** `GET /assets/{asset_id}/price`

Reading cached prices requires authentication. Setting `refresh=true` requires the caller
to hold the asset or be a superuser. A price fetched within the previous minute is reused
instead of contacting the upstream provider again.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `refresh` | boolean | false | Force refresh from external API |

**Response Schema:**

```typescript
interface CurrentPrice {
  symbol: string;
  price: number;
  currency: Currency;
  price_usd: number;
  price_mxn: number;
  change: number | null;
  change_percent: number | null;
  fetched_at: string; // ISO datetime
}
```

**Example:**

```javascript
// Get cached price
const price = await apiCall('/assets/1/price');

// Force refresh from Yahoo Finance/CoinGecko
const freshPrice = await apiCall('/assets/1/price?refresh=true');
```

**Response:**

```json
{
  "symbol": "AAPL",
  "price": 178.52,
  "currency": "USD",
  "price_usd": 178.52,
  "price_mxn": 3052.45,
  "change": 2.35,
  "change_percent": 1.33,
  "fetched_at": "2024-01-15T16:00:00Z"
}
```

---

#### 9. Refresh All Prices

Bulk refresh prices for tracked assets.

**Endpoint:** `POST /assets/refresh-prices`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `only_my_holdings` | boolean | true | Only refresh assets in user's portfolio |

**Response Schema:**

```typescript
interface PriceRefreshResponse {
  message: string;
  updated_count: number;
  failed_symbols: string[];
}
```

**Example:**

```javascript
// Refresh prices for assets in my portfolio
const result = await apiCall('/assets/refresh-prices', { method: 'POST' });

// Refresh all tracked assets (superuser)
const allResult = await apiCall('/assets/refresh-prices?only_my_holdings=false', { 
  method: 'POST' 
});
```

**Response:**

```json
{
  "message": "Price refresh completed",
  "updated_count": 15,
  "failed_symbols": ["OBSCURE.MX"]
}
```

---

### Holdings Endpoints

Holdings represent the user's current positions in assets.

---

#### 1. List Holdings

Retrieve all holdings for the current user.

**Endpoint:** `GET /holdings`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `skip` | integer | 0 | Pagination offset |
| `limit` | integer | 100 | Number of results |
| `asset_class` | string | - | Filter by asset class |
| `currency` | string | - | Filter by asset currency |

**Response Schema:**

```typescript
interface HoldingWithAsset {
  id: number;
  owner_id: number;
  asset_id: number;
  quantity: number;
  avg_cost_basis: number;
  cost_currency: Currency;
  total_invested: number;
  current_value: number;
  current_value_mxn: number;
  current_value_usd: number;
  unrealized_gain_loss: number;
  unrealized_gain_loss_pct: number;
  // Asset details
  symbol: string | null;
  asset_name: string | null;
  asset_class: AssetClass | null;
  asset_type: AssetType | null;
  asset_currency: Currency | null;
  market: Market | null;
  sector: string | null;
  country: string | null;
  current_price: number | null;
  price_change: number | null;
  price_change_percent: number | null;
}
```

**Example:**

```javascript
// Get all holdings
const holdings = await apiCall('/holdings');

// Get only equity holdings
const equityHoldings = await apiCall('/holdings?asset_class=equities');

// Get USD holdings
const usdHoldings = await apiCall('/holdings?currency=USD');
```

**Response:**

```json
[
  {
    "id": 1,
    "owner_id": 1,
    "asset_id": 1,
    "quantity": 50,
    "avg_cost_basis": 150.00,
    "cost_currency": "USD",
    "total_invested": 7500.00,
    "current_value": 8926.00,
    "current_value_mxn": 152534.50,
    "current_value_usd": 8926.00,
    "unrealized_gain_loss": 1426.00,
    "unrealized_gain_loss_pct": 19.01,
    "symbol": "AAPL",
    "asset_name": "Apple Inc.",
    "asset_class": "equities",
    "asset_type": "stock",
    "asset_currency": "USD",
    "market": "NASDAQ",
    "sector": "Technology",
    "country": "US",
    "current_price": 178.52,
    "price_change": 2.35,
    "price_change_percent": 1.33
  }
]
```

---

#### 2. Create Holding

Create a new holding position.

**Endpoint:** `POST /holdings`

**Request Schema:**

```typescript
interface HoldingCreate {
  asset_id?: number;        // Existing tracked asset ID
  // Or provide trusted external identifier (asset auto-created if missing)
  provider?: 'yahoo' | 'coingecko';
  external_id?: string;      // Yahoo ticker (e.g., "AAPL", "AMXL.MX") or CoinGecko ID (e.g., "bitcoin")
  quantity: number;         // Required - Number of units held
  avg_cost_basis: number;   // Required - Average purchase price per unit
  cost_currency?: Currency; // Default: "USD"
}
```

**Example:**

```javascript
const newHolding = await apiCall('/holdings', {
  method: 'POST',
  body: JSON.stringify({
    asset_id: 1,
    quantity: 50,
    avg_cost_basis: 150.00,
    cost_currency: 'USD',
  }),
});

// Or create from external search result (asset auto-created if needed)
const newHoldingFromSearch = await apiCall('/holdings', {
  method: 'POST',
  body: JSON.stringify({
    provider: 'yahoo',
    external_id: 'NVDA',
    quantity: 3,
    avg_cost_basis: 910.50,
    cost_currency: 'USD',
  }),
});
```

---

#### 3. Get Holding by ID

Retrieve a specific holding.

**Endpoint:** `GET /holdings/{holding_id}`

**Example:**

```javascript
const holding = await apiCall('/holdings/1');
```

---

#### 4. Update Holding

Update a holding's quantity or cost basis.

Valuation, gain/loss, total-invested, ownership, account, and asset fields are server-owned
and are rejected if supplied.

**Endpoint:** `PUT /holdings/{holding_id}`

**Request Schema:**

```typescript
interface HoldingUpdate {
  quantity?: number;
  avg_cost_basis?: number;
  cost_currency?: Currency;
}
```

**Example:**

```javascript
const updatedHolding = await apiCall('/holdings/1', {
  method: 'PUT',
  body: JSON.stringify({
    quantity: 75,
    avg_cost_basis: 155.00,
  }),
});
```

---

#### 5. Delete Holding

Delete a holding and all its associated transactions.

**Endpoint:** `DELETE /holdings/{holding_id}`

**Example:**

```javascript
await apiCall('/holdings/1', { method: 'DELETE' });
```

---

### Transactions Endpoints

Transactions record buy/sell/dividend events for holdings.

---

#### 1. List Transactions

Retrieve all transactions for the current user.

**Endpoint:** `GET /transactions`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `skip` | integer | 0 | Pagination offset |
| `limit` | integer | 100 | Number of results |
| `holding_id` | integer | - | Filter by holding |
| `transaction_type` | string | - | Filter by: `buy`, `sell`, `dividend`, `split`, `transfer_in`, `transfer_out` |

**Response Schema:**

```typescript
interface InvestmentTransactionWithAsset {
  id: number;
  owner_id: number;
  holding_id: number;
  transaction_type: TransactionType;
  quantity: number;
  price_per_unit: number;
  currency: Currency;
  total_amount: number; // quantity * price_per_unit
  fees: number;
  affects_cash_balance: boolean;
  exchange_rate_to_usd: number | null;
  exchange_rate_to_mxn: number | null;
  notes: string | null;
  broker: string | null;
  executed_at: string; // ISO datetime
  // Asset details
  symbol: string | null;
  asset_name: string | null;
}
```

**Example:**

```javascript
// Get all transactions
const transactions = await apiCall('/transactions');

// Get only buy transactions
const buys = await apiCall('/transactions?transaction_type=buy');

// Get transactions for a specific holding
const holdingTxns = await apiCall('/transactions?holding_id=1');
```

**Response:**

```json
[
  {
    "id": 1,
    "owner_id": 1,
    "holding_id": 1,
    "transaction_type": "buy",
    "quantity": 25,
    "price_per_unit": 145.00,
    "currency": "USD",
    "total_amount": 3625.00,
    "fees": 4.95,
    "affects_cash_balance": true,
    "exchange_rate_to_usd": 1.0,
    "exchange_rate_to_mxn": 17.15,
    "notes": "First purchase",
    "broker": "Fidelity",
    "executed_at": "2024-01-10T14:30:00Z",
    "symbol": "AAPL",
    "asset_name": "Apple Inc."
  }
]
```

---

#### 2. Create Transaction

Record a new transaction for an existing holding.

**Endpoint:** `POST /transactions`

**Request Schema:**

```typescript
interface InvestmentTransactionCreate {
  holding_id: number;             // Required
  account_id: number;             // Required
  transaction_type: TransactionType; // Required
  quantity: number;               // Required, must be positive
  price_per_unit: number;         // Required, must be non-negative
  executed_at: string;            // Required, ISO datetime
  currency?: Currency;            // Default: "USD"
  fees?: number;                  // Default: 0.0
  affects_cash_balance?: boolean; // Default: false; buy/sell only
  exchange_rate_to_usd?: number;  // Auto-calculated if not provided
  exchange_rate_to_mxn?: number;  // Auto-calculated if not provided
  notes?: string;
  broker?: string;
}
```

**Example:**

```javascript
// Record a buy transaction
const buyTxn = await apiCall('/transactions', {
  method: 'POST',
  body: JSON.stringify({
    holding_id: 1,
    account_id: 1,
    transaction_type: 'buy',
    quantity: 10,
    price_per_unit: 175.50,
    executed_at: new Date().toISOString(),
    currency: 'USD',
    fees: 0,
    affects_cash_balance: true,
    broker: 'Fidelity',
    notes: 'Adding to position',
  }),
});

// Record a sell transaction
const sellTxn = await apiCall('/transactions', {
  method: 'POST',
  body: JSON.stringify({
    holding_id: 1,
    account_id: 1,
    transaction_type: 'sell',
    quantity: 5,
    price_per_unit: 180.00,
    executed_at: new Date().toISOString(),
    currency: 'USD',
    fees: 4.95,
    affects_cash_balance: true,
  }),
});

// Record a dividend
const dividendTxn = await apiCall('/transactions', {
  method: 'POST',
  body: JSON.stringify({
    holding_id: 1,
    transaction_type: 'dividend',
    quantity: 1, // Use 1 for dividends
    price_per_unit: 24.00, // Total dividend amount
    executed_at: new Date().toISOString(),
    currency: 'USD',
    notes: 'Q4 2023 dividend',
  }),
});
```

---

#### 3. Create Transaction with Asset (Convenience Endpoint)

**This is the recommended endpoint for recording new investments.** It creates the asset (if new), the holding (if new), and the transaction in a single call.

**Endpoint:** `POST /transactions/with-asset`

**Request Schema:**

```typescript
interface TransactionWithAssetCreate {
  // Asset identity (one required)
  asset_id?: number;            // Existing tracked asset ID
  provider?: 'yahoo' | 'coingecko';
  external_id?: string;         // Yahoo ticker or CoinGecko ID
  
  // Transaction details
  transaction_type: TransactionType; // Required
  quantity: number;             // Required
  price_per_unit: number;       // Required
  executed_at: string;          // Required, ISO datetime
  fees?: number;                // Default: 0.0
  affects_cash_balance?: boolean; // Default: false; buy/sell only
  broker?: string;
  notes?: string;
  exchange_rate_to_usd?: number;
  exchange_rate_to_mxn?: number;
}
```

**Response Schema:**

```typescript
interface TransactionWithAssetResponse {
  transaction: InvestmentTransaction;
  asset_created: boolean;
  holding_created: boolean;
  asset_id: number;
  holding_id: number;
}
```

**Example:**

```javascript
// Record a new stock purchase (creates asset + holding + transaction)
const result = await apiCall('/transactions/with-asset', {
  method: 'POST',
  body: JSON.stringify({
    // Asset identity
    provider: 'yahoo',
    external_id: 'GOOGL',
    
    // Transaction details
    transaction_type: 'buy',
    quantity: 10,
    price_per_unit: 140.50,
    executed_at: new Date().toISOString(),
    fees: 0,
    broker: 'Fidelity',
    notes: 'Initial position',
  }),
});

// Record a Mexican stock purchase
const mxResult = await apiCall('/transactions/with-asset', {
  method: 'POST',
  body: JSON.stringify({
    provider: 'yahoo',
    external_id: 'AMXL.MX',
    
    transaction_type: 'buy',
    quantity: 100,
    price_per_unit: 17.25,
    executed_at: new Date().toISOString(),
    broker: 'GBM+',
  }),
});

// Record a crypto purchase
const cryptoResult = await apiCall('/transactions/with-asset', {
  method: 'POST',
  body: JSON.stringify({
    provider: 'coingecko',
    external_id: 'bitcoin',
    
    transaction_type: 'buy',
    quantity: 0.5,
    price_per_unit: 42000.00,
    executed_at: new Date().toISOString(),
    broker: 'Coinbase',
  }),
});
```

**Response:**

```json
{
  "transaction": {
    "id": 5,
    "owner_id": 1,
    "holding_id": 3,
    "transaction_type": "buy",
    "quantity": 10,
    "price_per_unit": 140.50,
    "currency": "USD",
    "total_amount": 1405.00,
    "fees": 0,
    "exchange_rate_to_usd": 1.0,
    "exchange_rate_to_mxn": 17.15,
    "notes": "Initial position",
    "broker": "Fidelity",
    "executed_at": "2024-01-15T10:30:00Z"
  },
  "asset_created": true,
  "holding_created": true,
  "asset_id": 5,
  "holding_id": 3
}
```

---

#### 4. Get Transaction by ID

Retrieve a specific transaction.

**Endpoint:** `GET /transactions/{transaction_id}`

**Example:**

```javascript
const transaction = await apiCall('/transactions/1');
```

---

#### 5. Delete Transaction

Investment transactions cannot be deleted because doing so would silently corrupt the
holding's quantity and cost basis. This endpoint returns `409 Conflict`; record a correcting
transaction instead.

**Endpoint:** `DELETE /transactions/{transaction_id}`

**Example:**

```javascript
await apiCall('/transactions/1', { method: 'DELETE' });
```

---

### Portfolio Analytics Endpoints

Analytics endpoints provide aggregated portfolio data for dashboards and reports.

---

#### 1. Portfolio Summary

Get overall portfolio summary with total values and gains/losses.

**Endpoint:** `GET /portfolio/summary`

**Response Schema:**

```typescript
interface PortfolioSummary {
  total_value_usd: number;
  total_value_mxn: number;
  total_invested: number;
  total_invested_currency: Currency;
  total_gain_loss: number;
  total_gain_loss_pct: number;
  day_change: number | null;
  day_change_pct: number | null;
  total_holdings: number;
  total_assets: number;
  last_updated: string; // ISO datetime
}
```

**Example:**

```javascript
const summary = await apiCall('/portfolio/summary');
```

**Response:**

```json
{
  "total_value_usd": 125430.50,
  "total_value_mxn": 2143611.05,
  "total_invested": 98500.00,
  "total_invested_currency": "USD",
  "total_gain_loss": 26930.50,
  "total_gain_loss_pct": 27.34,
  "day_change": 1250.75,
  "day_change_pct": 1.01,
  "total_holdings": 15,
  "total_assets": 12,
  "last_updated": "2024-01-15T16:00:00Z"
}
```

---

#### 2. Allocation by Asset Class

Get portfolio allocation broken down by asset class.

**Endpoint:** `GET /portfolio/allocation/by-class`

**Response Schema:**

```typescript
interface AllocationItem {
  name: string;
  value: string;
  total_value_usd: number;
  total_value_mxn: number;
  percentage: number;
  holdings_count: number;
}

interface AllocationByClass {
  total_value_usd: number;
  total_value_mxn: number;
  allocations: AllocationItem[];
  equities: AllocationItem | null;
  fixed_income: AllocationItem | null;
  crypto: AllocationItem | null;
  funds: AllocationItem | null;
}
```

**Example:**

```javascript
const allocation = await apiCall('/portfolio/allocation/by-class');
```

**Response:**

```json
{
  "total_value_usd": 125430.50,
  "total_value_mxn": 2143611.05,
  "allocations": [
    {
      "name": "Equities",
      "value": "equities",
      "total_value_usd": 75258.30,
      "total_value_mxn": 1286165.63,
      "percentage": 60.0,
      "holdings_count": 8
    },
    {
      "name": "Crypto",
      "value": "crypto",
      "total_value_usd": 25086.10,
      "total_value_mxn": 428721.41,
      "percentage": 20.0,
      "holdings_count": 3
    },
    {
      "name": "Fixed Income",
      "value": "fixed_income",
      "total_value_usd": 18814.58,
      "total_value_mxn": 321541.01,
      "percentage": 15.0,
      "holdings_count": 2
    },
    {
      "name": "Funds",
      "value": "funds",
      "total_value_usd": 6271.52,
      "total_value_mxn": 107183.00,
      "percentage": 5.0,
      "holdings_count": 2
    }
  ],
  "equities": { /* same as above */ },
  "fixed_income": { /* same as above */ },
  "crypto": { /* same as above */ },
  "funds": { /* same as above */ }
}
```

---

#### 3. Allocation by Currency

Get portfolio allocation by currency exposure.

**Endpoint:** `GET /portfolio/allocation/by-currency`

**Example:**

```javascript
const currencyAllocation = await apiCall('/portfolio/allocation/by-currency');
```

**Response:**

```json
{
  "total_value_usd": 125430.50,
  "total_value_mxn": 2143611.05,
  "allocations": [
    {
      "name": "USD",
      "value": "USD",
      "total_value_usd": 100344.40,
      "total_value_mxn": 1714888.84,
      "percentage": 80.0,
      "holdings_count": 10
    },
    {
      "name": "MXN",
      "value": "MXN",
      "total_value_usd": 25086.10,
      "total_value_mxn": 428722.21,
      "percentage": 20.0,
      "holdings_count": 5
    }
  ]
}
```

---

#### 4. Allocation by Market

Get portfolio allocation by stock exchange/market.

**Endpoint:** `GET /portfolio/allocation/by-market`

**Example:**

```javascript
const marketAllocation = await apiCall('/portfolio/allocation/by-market');
```

**Response:**

```json
{
  "total_value_usd": 125430.50,
  "total_value_mxn": 2143611.05,
  "allocations": [
    {
      "name": "NASDAQ",
      "value": "NASDAQ",
      "total_value_usd": 50172.20,
      "total_value_mxn": 857444.42,
      "percentage": 40.0,
      "holdings_count": 5
    },
    {
      "name": "NYSE",
      "value": "NYSE",
      "total_value_usd": 37629.15,
      "total_value_mxn": 643083.31,
      "percentage": 30.0,
      "holdings_count": 4
    },
    {
      "name": "CRYPTO",
      "value": "CRYPTO",
      "total_value_usd": 25086.10,
      "total_value_mxn": 428721.41,
      "percentage": 20.0,
      "holdings_count": 3
    },
    {
      "name": "BMV",
      "value": "BMV",
      "total_value_usd": 12543.05,
      "total_value_mxn": 214361.91,
      "percentage": 10.0,
      "holdings_count": 3
    }
  ]
}
```

---

#### 5. Allocation by Asset Type

Get portfolio allocation by specific asset type.

**Endpoint:** `GET /portfolio/allocation/by-type`

**Example:**

```javascript
const typeAllocation = await apiCall('/portfolio/allocation/by-type');
```

---

#### 6. Allocation by Country

Get portfolio allocation by country.

**Endpoint:** `GET /portfolio/allocation/by-country`

**Example:**

```javascript
const countryAllocation = await apiCall('/portfolio/allocation/by-country');
```

**Response:**

```json
{
  "total_value_usd": 125430.50,
  "total_value_mxn": 2143611.05,
  "allocations": [
    {
      "name": "United States",
      "value": "US",
      "total_value_usd": 100344.40,
      "total_value_mxn": 1714888.84,
      "percentage": 80.0,
      "holdings_count": 10
    },
    {
      "name": "Mexico",
      "value": "MX",
      "total_value_usd": 25086.10,
      "total_value_mxn": 428722.21,
      "percentage": 20.0,
      "holdings_count": 5
    }
  ]
}
```

---

#### 7. Top Holdings

Get the top holdings by portfolio value.

**Endpoint:** `GET /portfolio/top-holdings`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 10 | Number of holdings (1-50) |

**Response Schema:**

```typescript
interface TopHolding {
  symbol: string;
  name: string;
  asset_class: AssetClass;
  asset_type: AssetType;
  quantity: number;
  current_value_usd: number;
  current_value_mxn: number;
  percentage_of_portfolio: number;
  gain_loss: number;
  gain_loss_pct: number;
}

interface TopHoldingsResponse {
  holdings: TopHolding[];
  total_shown: number;
  total_holdings: number;
}
```

**Example:**

```javascript
// Get top 5 holdings
const topHoldings = await apiCall('/portfolio/top-holdings?limit=5');

// Get top 10 holdings (default)
const top10 = await apiCall('/portfolio/top-holdings');
```

**Response:**

```json
{
  "holdings": [
    {
      "symbol": "AAPL",
      "name": "Apple Inc.",
      "asset_class": "equities",
      "asset_type": "stock",
      "quantity": 100,
      "current_value_usd": 17852.00,
      "current_value_mxn": 305154.70,
      "percentage_of_portfolio": 14.23,
      "gain_loss": 2852.00,
      "gain_loss_pct": 19.01
    },
    {
      "symbol": "BTC",
      "name": "Bitcoin",
      "asset_class": "crypto",
      "asset_type": "cryptocurrency",
      "quantity": 0.5,
      "current_value_usd": 21500.00,
      "current_value_mxn": 367637.50,
      "percentage_of_portfolio": 17.14,
      "gain_loss": 500.00,
      "gain_loss_pct": 2.38
    }
  ],
  "total_shown": 2,
  "total_holdings": 15
}
```

---

## Complete User Workflow

This section describes the typical user journey from initial setup to portfolio management.

### Step 1: Authentication

First, obtain an authentication token.

```javascript
// Login and get token
async function login(email, password) {
  const response = await fetch(`${API_BASE_URL}/api/v1/login/access-token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ username: email, password }),
  });
  
  const data = await response.json();
  authToken = data.access_token;
  return authToken;
}

await login('user@example.com', 'password123');
```

### Step 2: Record Your First Investment

Use the convenience endpoint to record your first purchase. This automatically creates the asset and holding.

```javascript
// Record first stock purchase
const result = await apiCall('/transactions/with-asset', {
  method: 'POST',
  body: JSON.stringify({
    symbol: 'AAPL',
    asset_name: 'Apple Inc.',
    asset_type: 'stock',
    market: 'NASDAQ',
    currency: 'USD',
    country: 'US',
    sector: 'Technology',
    
    transaction_type: 'buy',
    quantity: 50,
    price_per_unit: 150.00,
    executed_at: '2024-01-10T10:00:00Z',
    fees: 0,
    broker: 'Fidelity',
    notes: 'Initial investment',
  }),
});

console.log(`Asset created: ${result.asset_created}`);
console.log(`Holding created: ${result.holding_created}`);
console.log(`Transaction ID: ${result.transaction.id}`);
```

### Step 3: Add More Investments

Continue adding different types of investments.

```javascript
// Add Mexican stock
await apiCall('/transactions/with-asset', {
  method: 'POST',
  body: JSON.stringify({
    symbol: 'AMXL.MX',
    asset_name: 'America Movil',
    asset_type: 'stock',
    market: 'BMV',
    currency: 'MXN',
    country: 'MX',
    
    transaction_type: 'buy',
    quantity: 500,
    price_per_unit: 17.50,
    executed_at: new Date().toISOString(),
    broker: 'GBM+',
  }),
});

// Add cryptocurrency
await apiCall('/transactions/with-asset', {
  method: 'POST',
  body: JSON.stringify({
    symbol: 'ETH',
    asset_name: 'Ethereum',
    asset_type: 'cryptocurrency',
    market: 'CRYPTO',
    currency: 'USD',
    
    transaction_type: 'buy',
    quantity: 2.5,
    price_per_unit: 2500.00,
    executed_at: new Date().toISOString(),
    broker: 'Coinbase',
  }),
});

// Add ETF
await apiCall('/transactions/with-asset', {
  method: 'POST',
  body: JSON.stringify({
    symbol: 'VOO',
    asset_name: 'Vanguard S&P 500 ETF',
    asset_type: 'etf',
    market: 'NYSE',
    currency: 'USD',
    country: 'US',
    
    transaction_type: 'buy',
    quantity: 20,
    price_per_unit: 420.00,
    executed_at: new Date().toISOString(),
    broker: 'Vanguard',
  }),
});
```

### Step 4: View Portfolio Dashboard

Fetch and display portfolio summary and allocations.

```javascript
// Fetch all dashboard data in parallel
const [summary, classAllocation, currencyAllocation, marketAllocation, topHoldings] = 
  await Promise.all([
    apiCall('/portfolio/summary'),
    apiCall('/portfolio/allocation/by-class'),
    apiCall('/portfolio/allocation/by-currency'),
    apiCall('/portfolio/allocation/by-market'),
    apiCall('/portfolio/top-holdings?limit=5'),
  ]);

// Display summary
console.log('=== Portfolio Summary ===');
console.log(`Total Value: $${summary.total_value_usd.toLocaleString()} USD`);
console.log(`Total Value: $${summary.total_value_mxn.toLocaleString()} MXN`);
console.log(`Total Invested: $${summary.total_invested.toLocaleString()}`);
console.log(`Gain/Loss: $${summary.total_gain_loss.toLocaleString()} (${summary.total_gain_loss_pct.toFixed(2)}%)`);
console.log(`Holdings: ${summary.total_holdings}`);

// Display allocations
console.log('\n=== Allocation by Class ===');
classAllocation.allocations.forEach(a => {
  console.log(`${a.name}: ${a.percentage.toFixed(1)}% ($${a.total_value_usd.toLocaleString()})`);
});

// Display top holdings
console.log('\n=== Top Holdings ===');
topHoldings.holdings.forEach((h, i) => {
  console.log(`${i + 1}. ${h.symbol}: $${h.current_value_usd.toLocaleString()} (${h.percentage_of_portfolio.toFixed(1)}%)`);
});
```

### Step 5: Refresh Prices

Update prices for all holdings.

```javascript
// Refresh prices for portfolio holdings
const refreshResult = await apiCall('/assets/refresh-prices', { method: 'POST' });

console.log(`Updated ${refreshResult.updated_count} assets`);
if (refreshResult.failed_symbols.length > 0) {
  console.log(`Failed to update: ${refreshResult.failed_symbols.join(', ')}`);
}

// Fetch updated summary
const updatedSummary = await apiCall('/portfolio/summary');
console.log(`New portfolio value: $${updatedSummary.total_value_usd.toLocaleString()}`);
```

### Step 6: Record Additional Transactions

Add more buys, sells, or dividends to existing holdings.

```javascript
// First, get existing holdings
const holdings = await apiCall('/holdings');
const appleHolding = holdings.find(h => h.symbol === 'AAPL');

// Record another buy for existing holding
await apiCall('/transactions', {
  method: 'POST',
  body: JSON.stringify({
    holding_id: appleHolding.id,
    transaction_type: 'buy',
    quantity: 25,
    price_per_unit: 175.00,
    executed_at: new Date().toISOString(),
    currency: 'USD',
    fees: 0,
    broker: 'Fidelity',
    notes: 'Adding to position on dip',
  }),
});

// Record a sell
await apiCall('/transactions', {
  method: 'POST',
  body: JSON.stringify({
    holding_id: appleHolding.id,
    transaction_type: 'sell',
    quantity: 10,
    price_per_unit: 185.00,
    executed_at: new Date().toISOString(),
    currency: 'USD',
    fees: 0,
    notes: 'Taking some profits',
  }),
});

// Record a dividend
await apiCall('/transactions', {
  method: 'POST',
  body: JSON.stringify({
    holding_id: appleHolding.id,
    transaction_type: 'dividend',
    quantity: 1,
    price_per_unit: 24.00, // Total dividend amount
    executed_at: new Date().toISOString(),
    currency: 'USD',
    notes: 'Q4 2023 dividend',
  }),
});
```

### Step 7: View Holdings Performance

Check how individual holdings are performing.

```javascript
const holdings = await apiCall('/holdings');

console.log('=== Holdings Performance ===');
holdings.forEach(h => {
  const gainSign = h.unrealized_gain_loss >= 0 ? '+' : '';
  console.log(`${h.symbol}: ${h.quantity} shares @ $${h.avg_cost_basis.toFixed(2)}`);
  console.log(`  Current: $${h.current_value_usd.toLocaleString()}`);
  console.log(`  Gain/Loss: ${gainSign}$${h.unrealized_gain_loss.toFixed(2)} (${gainSign}${h.unrealized_gain_loss_pct.toFixed(2)}%)`);
  console.log('');
});
```

### Step 8: View Transaction History

Review all transactions.

```javascript
const transactions = await apiCall('/transactions?limit=20');

console.log('=== Recent Transactions ===');
transactions.forEach(t => {
  const date = new Date(t.executed_at).toLocaleDateString();
  console.log(`${date} | ${t.transaction_type.toUpperCase()} | ${t.symbol}`);
  console.log(`  ${t.quantity} @ $${t.price_per_unit} = $${t.total_amount.toFixed(2)}`);
  if (t.fees > 0) console.log(`  Fees: $${t.fees.toFixed(2)}`);
  console.log('');
});
```

---

## External Asset Search Workflow

The external asset search feature allows users to discover and add assets from external sources (Yahoo Finance for stocks/ETFs, CoinGecko for crypto).

### How It Works

1. User types a search query (symbol or company name)
2. API searches Yahoo Finance for matching assets
3. Results include symbol, name, market, currency, and country
4. User selects an asset from results
5. Selected asset data is used to create a transaction

### Implementation Example

```javascript
// Debounced search function for type-ahead
let searchTimeout;

function searchAssets(query) {
  clearTimeout(searchTimeout);
  
  searchTimeout = setTimeout(async () => {
    if (query.length < 2) return;
    
    try {
      const results = await apiCall(`/assets/search-external?q=${encodeURIComponent(query)}`);
      displaySearchResults(results);
    } catch (error) {
      console.error('Search failed:', error);
    }
  }, 300); // 300ms debounce
}

function displaySearchResults(results) {
  // Display results in dropdown/list
  results.forEach(asset => {
    console.log(`${asset.symbol} - ${asset.name} (${asset.market})`);
  });
}

// Example: Search for Tesla
searchAssets('TSLA');
// Results:
// TSLA - Tesla, Inc. (NASDAQ)
// TSLA.MX - Tesla Inc (BMV)
```

### Using Search Results

```javascript
// User selects an asset from search results
const selectedAsset = {
  symbol: 'TSLA',
  name: 'Tesla, Inc.',
  asset_type: 'stock',
  market: 'NASDAQ',
  currency: 'USD',
  country: 'US',
};

// Use selected asset to create transaction
const transaction = await apiCall('/transactions/with-asset', {
  method: 'POST',
  body: JSON.stringify({
    // From search result
    symbol: selectedAsset.symbol,
    asset_name: selectedAsset.name,
    asset_type: selectedAsset.asset_type,
    market: selectedAsset.market,
    currency: selectedAsset.currency,
    country: selectedAsset.country,
    
    // User input
    transaction_type: 'buy',
    quantity: 5,
    price_per_unit: 250.00,
    executed_at: new Date().toISOString(),
    broker: 'Robinhood',
  }),
});
```

### Supported Markets in External Search

| Market | Description | Example Symbols |
|--------|-------------|-----------------|
| NASDAQ | US tech stocks | AAPL, GOOGL, MSFT |
| NYSE | US stocks | JPM, WMT, KO |
| BMV | Mexican stocks | AMXL.MX, WALMEX.MX |
| CRYPTO | Cryptocurrencies | BTC, ETH, SOL |

---

## Error Handling

### HTTP Status Codes

| Code | Meaning | Description |
|------|---------|-------------|
| 200 | OK | Request successful |
| 201 | Created | Resource created successfully |
| 400 | Bad Request | Invalid request data |
| 401 | Unauthorized | Missing or invalid token |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Resource not found |
| 422 | Validation Error | Request data failed validation |
| 500 | Server Error | Internal server error |

### Error Response Format

```typescript
interface ErrorResponse {
  detail: string | ValidationError[];
}

interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
}
```

### Example Error Responses

**401 Unauthorized:**
```json
{
  "detail": "Not authenticated"
}
```

**403 Forbidden:**
```json
{
  "detail": "Not enough permissions"
}
```

**404 Not Found:**
```json
{
  "detail": "Asset not found"
}
```

**422 Validation Error:**
```json
{
  "detail": [
    {
      "loc": ["body", "quantity"],
      "msg": "ensure this value is greater than 0",
      "type": "value_error.number.not_gt"
    }
  ]
}
```

### Error Handling Example

```javascript
async function apiCall(endpoint, options = {}) {
  const url = `${API_BASE_URL}/api/v1/investments${endpoint}`;
  
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${authToken}`,
      ...options.headers,
    },
  });

  const data = await response.json();

  if (!response.ok) {
    // Handle specific error codes
    switch (response.status) {
      case 401:
        // Redirect to login
        throw new Error('Session expired. Please login again.');
      case 403:
        throw new Error('You do not have permission to perform this action.');
      case 404:
        throw new Error('The requested resource was not found.');
      case 422:
        // Format validation errors
        if (Array.isArray(data.detail)) {
          const messages = data.detail.map(e => `${e.loc.join('.')}: ${e.msg}`);
          throw new Error(`Validation failed:\n${messages.join('\n')}`);
        }
        throw new Error(data.detail || 'Validation failed');
      default:
        throw new Error(data.detail || 'An unexpected error occurred');
    }
  }

  return data;
}
```

---

## Quick Reference Table

### All Endpoints at a Glance

| Method | Endpoint | Description |
|--------|----------|-------------|
| **Assets** | | |
| GET | `/assets` | List all assets (with filtering) |
| POST | `/assets` | Create new asset |
| GET | `/assets/search` | Search internal assets |
| GET | `/assets/search-external` | Search Yahoo Finance |
| GET | `/assets/{id}` | Get asset by ID |
| PUT | `/assets/{id}` | Update asset |
| DELETE | `/assets/{id}` | Delete asset |
| GET | `/assets/{id}/price` | Get current price |
| POST | `/assets/refresh-prices` | Bulk refresh prices |
| **Holdings** | | |
| GET | `/holdings` | List user holdings |
| POST | `/holdings` | Create holding |
| GET | `/holdings/{id}` | Get holding by ID |
| PUT | `/holdings/{id}` | Update holding |
| DELETE | `/holdings/{id}` | Delete holding |
| **Transactions** | | |
| GET | `/transactions` | List transactions |
| POST | `/transactions` | Create transaction |
| POST | `/transactions/with-asset` | Create transaction + asset + holding |
| GET | `/transactions/{id}` | Get transaction by ID |
| DELETE | `/transactions/{id}` | Delete transaction |
| **Portfolio** | | |
| GET | `/portfolio/summary` | Portfolio summary |
| GET | `/portfolio/allocation/by-class` | Allocation by asset class |
| GET | `/portfolio/allocation/by-currency` | Allocation by currency |
| GET | `/portfolio/allocation/by-market` | Allocation by market |
| GET | `/portfolio/allocation/by-type` | Allocation by asset type |
| GET | `/portfolio/allocation/by-country` | Allocation by country |
| GET | `/portfolio/top-holdings` | Top holdings by value |

---

## Summary

This API provides a complete investment portfolio management system with:

1. **Asset Management**: Track stocks, bonds, ETFs, crypto, and more
2. **Holdings**: Monitor current positions with cost basis and performance
3. **Transactions**: Record buys, sells, dividends, and transfers
4. **Analytics**: View portfolio allocations and performance metrics
5. **Real-time Pricing**: Integration with Yahoo Finance and CoinGecko
6. **Multi-currency**: Support for USD and MXN with automatic conversion
7. **Multi-market**: US (NYSE/NASDAQ), Mexico (BMV), and Crypto markets

The recommended workflow for building a frontend:

1. Implement authentication flow
2. Build the transaction recording form with external asset search
3. Create a dashboard showing portfolio summary and allocations
4. Add holdings list with performance indicators
5. Implement transaction history view
6. Add price refresh functionality

For questions or issues, refer to the backend source code or API documentation.
