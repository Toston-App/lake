# Endpoint Logging Guide

This guide shows you what's being logged for each endpoint and example queries you can run in Axiom.

## 📊 Expenses Endpoints (`/api/v1/expenses`)

### `GET /getAll` - List All Expenses

**What's Logged:**
- User context (ID, email, superuser status)
- Query parameters (skip, limit)
- Database operation timing
- Result count

**Example Queries:**

```apl
# Find slow expense list queries
['cleverbill']
| where http.path contains "/expenses/getAll"
| where database.duration_ms > 500
| project _time, user.email, query.skip, query.limit, database.results_count, database.duration_ms
```

```apl
# Average list query performance by user
['cleverbill']
| where query.type == "list_expenses"
| summarize 
    avg_duration = avg(database.duration_ms),
    queries = count()
    by user.email
| sort by avg_duration desc
```

---

### `GET /{date_filter_type}/{date}` - Filter Expenses by Date

**What's Logged:**
- User context
- Filter type (date, week, month, quarter, year, range)
- Date range (start, end, days)
- Database operation timing
- Result count

**Example Queries:**

```apl
# Most common filter types
['cleverbill']
| where query.type == "filter_expenses_by_date"
| summarize count() by query.date_filter_type
| render piechart
```

```apl
# Slow date filters
['cleverbill']
| where query.type == "filter_expenses_by_date"
| where database.duration_ms > 1000
| project 
    _time,
    user.email,
    query.date_filter_type,
    date_range.days,
    database.results_count,
    database.duration_ms
| sort by database.duration_ms desc
```

```apl
# Performance by date range size
['cleverbill']
| where date_range.days != null
| summarize 
    p99 = percentile(database.duration_ms, 99),
    p50 = percentile(database.duration_ms, 50)
    by bin(date_range.days, 30)
```

---

### `POST /` - Create Expense

**What's Logged:**
- User context
- Operation source (WhatsApp, API, etc.)
- Transaction details (amount, has_category, has_subcategory, has_place, has_account)
- Database operation timing
- Success/failure

**Example Queries:**

```apl
# Failed expense creations
['cleverbill']
| where operation.type == "create_expense"
| where database.success == false
| project _time, user.email, transaction.amount, error.message
```

```apl
# Expense creation by source
['cleverbill']
| where operation.type == "create_expense"
| summarize 
    count = count(),
    total_amount = sum(transaction.amount)
    by operation.source
| render columnchart
```

```apl
# Average expense amount by user
['cleverbill']
| where transaction.type == "expense"
| summarize 
    avg_expense = avg(transaction.amount),
    total_expenses = count()
    by user.email
| where total_expenses > 10
| sort by avg_expense desc
```

---

### `POST /bulk` - Bulk Create Expenses

**What's Logged:**
- User context
- Bulk operation metrics (item count, total amount)
- Database timing (total and per-item average)
- Success count

**Example Queries:**

```apl
# Bulk creation performance
['cleverbill']
| where operation.type == "bulk_create_expenses"
| project 
    _time,
    user.email,
    bulk.item_count,
    bulk.total_amount,
    database.duration_ms,
    database.avg_duration_per_item_ms
```

```apl
# Largest bulk operations
['cleverbill']
| where bulk.item_count != null
| where operation.type == "bulk_create_expenses"
| top 20 by bulk.item_count desc
| project _time, user.email, bulk.item_count, bulk.total_amount
```

---

### `PUT /{id}` - Update Expense

**What's Logged:**
- User context
- Expense ID being updated
- Changes made (amount, account, category)
- Number of fields changed
- Database timing

**Example Queries:**

```apl
# Most frequently updated expenses
['cleverbill']
| where operation.type == "update_expense"
| summarize updates = count() by user.email, operation.expense_id
| where updates > 3
| sort by updates desc
```

```apl
# What fields are being changed most often
['cleverbill']
| where transaction.changes != null
| project _time, user.email, changes = todynamic(transaction.changes)
| extend change_keys = bag_keys(changes)
| mv-expand change_keys
| summarize count() by tostring(change_keys)
```

---

### `DELETE /{id}` - Delete Expense

**What's Logged:**
- User context
- Expense ID
- Amount of deleted expense
- Whether it had account/category
- Database timing

**Example Queries:**

```apl
# Deleted expenses over time
['cleverbill']
| where operation.type == "delete_expense"
| summarize 
    count = count(),
    total_amount = sum(transaction.amount)
    by bin(_time, 1d)
| render timechart
```

---

### `DELETE /bulk/{ids}` - Bulk Delete Expenses

**What's Logged:**
- User context
- Bulk metrics (requested count, deleted count, total amount deleted)
- Success rate
- Database timing

**Example Queries:**

```apl
# Bulk deletion stats
['cleverbill']
| where operation.type == "bulk_delete_expenses"
| project 
    _time,
    user.email,
    bulk.requested_count,
    bulk.deleted_count,
    bulk.total_amount_deleted,
    bulk.success_rate
```

---

## 📊 Dashboard Data Endpoint (`/api/v2/data`)

### `GET /{date_filter_type}/{date}` - Get All Dashboard Data

This is your **most complex and critical endpoint** - it retrieves all data for the dashboard including expenses, incomes, transfers, accounts, categories, and generates 4 charts.

**What's Logged:**
- User context (ID, email, country, balance)
- Query type and date filter
- Database query timing
- Processing timing (chart generation)
- Response metrics:
  - Count of incomes, expenses, transfers, accounts, places, categories
  - Total income/expenses/net
  - Whether data was found
  - Number of charts generated
- Date range details
- Total operation duration

**Example Queries:**

```apl
# Dashboard performance overview
['cleverbill']
| where query.type == "dashboard_data"
| summarize 
    p99 = percentile(performance.total_duration_ms, 99),
    p50 = percentile(performance.total_duration_ms, 50),
    avg_duration = avg(performance.total_duration_ms)
    by query.date_filter_type
```

```apl
# Slow dashboard requests (>2 seconds)
['cleverbill']
| where query.type == "dashboard_data"
| where performance.total_duration_ms > 2000
| project 
    _time,
    user.email,
    query.date_filter_type,
    date_range.days,
    response.expenses_count,
    response.incomes_count,
    database.query_duration_ms,
    performance.processing_duration_ms,
    performance.total_duration_ms
| sort by performance.total_duration_ms desc
```

```apl
# Query vs Processing time breakdown
['cleverbill']
| where query.type == "dashboard_data"
| project 
    query_time = database.query_duration_ms,
    processing_time = performance.processing_duration_ms,
    total_time = performance.total_duration_ms
| summarize 
    avg_query = avg(query_time),
    avg_processing = avg(processing_time),
    avg_total = avg(total_time)
```

```apl
# Dashboard usage by filter type
['cleverbill']
| where query.type == "dashboard_data"
| summarize requests = count() by query.date_filter_type
| render piechart
```

```apl
# Empty dashboard responses (no data)
['cleverbill']
| where query.type == "dashboard_data"
| where response.has_data == false
| summarize count() by user.email, query.date_filter_type
```

```apl
# User activity patterns (most active users)
['cleverbill']
| where query.type == "dashboard_data"
| summarize 
    requests = count(),
    avg_expenses = avg(response.expenses_count),
    avg_incomes = avg(response.incomes_count)
    by user.email
| sort by requests desc
| take 20
```

```apl
# Financial insights from logs
['cleverbill']
| where response.total_income != null
| summarize 
    total_tracked_income = sum(response.total_income),
    total_tracked_expenses = sum(response.total_expenses),
    total_tracked_net = sum(response.net)
    by user.email
| project 
    user.email,
    total_tracked_income,
    total_tracked_expenses,
    total_tracked_net,
    savings_rate = round((total_tracked_net / total_tracked_income) * 100, 2)
| sort by total_tracked_income desc
```

```apl
# Performance correlation with data size
['cleverbill']
| where query.type == "dashboard_data"
| where response.has_data == true
| extend total_records = response.expenses_count + response.incomes_count
| summarize 
    avg_duration = avg(performance.total_duration_ms)
    by bin(total_records, 100)
| render scatterchart
```

---

## 🎯 Common Cross-Endpoint Queries

### User Activity Overview

```apl
['cleverbill']
| where user.email != null
| summarize 
    total_requests = count(),
    expense_creates = countif(operation.type == "create_expense"),
    dashboard_views = countif(query.type == "dashboard_data"),
    avg_duration = avg(duration_ms)
    by user.email
| sort by total_requests desc
```

### Error Rate by Endpoint

```apl
['cleverbill']
| where http.path contains "expenses" or http.path contains "data"
| summarize 
    total = count(),
    errors = countif(outcome == "error"),
    error_rate = round((countif(outcome == "error") * 100.0) / count(), 2)
    by http.path
| sort by error_rate desc
```

### Slowest Operations

```apl
['cleverbill']
| where database.operation != null
| summarize p99 = percentile(database.duration_ms, 99) by database.operation
| where p99 > 100
| sort by p99 desc
```

### API Usage Heatmap

```apl
['cleverbill']
| where http.path contains "expenses" or http.path contains "data"
| summarize requests = count() by 
    hour = bin(_time, 1h),
    endpoint = http.path
| render columnchart
```

### Transaction Volume by Source

```apl
['cleverbill']
| where transaction.type == "expense"
| summarize 
    count = count(),
    total_amount = sum(transaction.amount)
    by operation.source
```

---

## 🚀 Setting Up Dashboards

### Dashboard 1: Performance Monitoring

**Widgets:**
1. P99 latency by endpoint (line chart)
2. Error rate (gauge)
3. Requests per minute (area chart)
4. Slowest operations (table)

### Dashboard 2: User Activity

**Widgets:**
1. Most active users (bar chart)
2. Expense creation by source (pie chart)
3. Dashboard views by filter type (bar chart)
4. User growth over time (line chart)

### Dashboard 3: Business Metrics

**Widgets:**
1. Total expenses tracked (number)
2. Average expense amount (number)
3. Expenses by category (pie chart)
4. Income vs Expenses trend (line chart)

---

## 🔔 Recommended Alerts

### 1. Slow Dashboard Performance

```apl
['cleverbill']
| where query.type == "dashboard_data"
| where performance.total_duration_ms > 3000
```

**Action**: Investigate database queries or processing logic

### 2. High Error Rate

```apl
['cleverbill']
| where outcome == "error"
| where http.path contains "expenses"
| summarize count() by bin(_time, 5m)
| where count > 10
```

**Action**: Check error logs and investigate root cause

### 3. Failed Expense Creations

```apl
['cleverbill']
| where operation.type == "create_expense"
| where database.success == false
```

**Action**: Alert on each failed creation (critical for UX)

### 4. Large Bulk Operations

```apl
['cleverbill']
| where bulk.item_count > 100
```

**Action**: Monitor for performance impact

---

## 💡 Pro Tips

1. **Use time ranges**: Add `| where _time > ago(24h)` to focus on recent data
2. **Sample data**: Use `| sample 1000` for faster queries on large datasets
3. **Save queries**: Star your most-used queries in Axiom for quick access
4. **Create monitors**: Set up automatic alerts for anomalies
5. **Export data**: Use Axiom's export feature for deeper analysis in tools like Excel

---

## 📚 Next Steps

1. **Explore your data**: Run these queries against your dataset
2. **Create dashboards**: Build visual dashboards for monitoring
3. **Set up alerts**: Get notified of issues in real-time
4. **Optimize**: Use insights to improve performance
5. **Add more context**: Enrich other endpoints with `enrich_event()`

For more query examples and APL syntax, see: https://axiom.co/docs/apl/introduction

