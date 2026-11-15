# Toston tools

| Tool | Purpose | Needs confirmation |
| --- | --- | --- |
| `get_user_profile` | Read the user's name, locale, display currency and default account. | no |
| `list_accounts` | List accounts with their type and current balance. Paged. | no |
| `list_categories` | List categories and subcategories. Paged. | no |
| `list_places` | List places (merchants). Paged. | no |
| `search_transactions` | Search and filter transactions, paged. | no |
| `get_period_summary` | Totals for a day, week, month, quarter, year or range. | no |
| `get_period_charts` | Chart data (breakdowns and trends) for a period. | no |
| `list_balance_adjustments` | List balance adjustments, optionally for one account. Paged. | no |
| `get_portfolio_summary` | Portfolio value, invested amount and gain in USD and MXN, cash balance and net worth. | no |
| `list_holdings` | List investment holdings with value and gain. Paged. | no |
| `list_investment_transactions` | List buys, sells, dividends and other investment transactions. Paged. | no |
| `search_investment_assets` | Find an asset by symbol or name, in Toston or at Yahoo Finance and CoinGecko. | no |
| `record_investment_transaction` | Record a buy, sell, dividend, split or transfer; a first purchase creates the holding (and the asset if new). | yes |
| `export_data` | Start an Excel export of all the user's data; it replaces the previous export file. | yes |
| `get_export_status` | The latest export's status and the app page where the user downloads it. | no |
| `compare_periods` | Compare two periods: income, expenses, net and spending per category. | no |
| `list_recurring` | List recurring series with their schedule and next dates. | no |
| `manage_account` | Create, update or delete an account. | update & delete only |
| `manage_category` | Create, update or delete a category. | update & delete only |
| `manage_subcategory` | Create, update or delete a subcategory. | update & delete only |
| `manage_place` | Create, update or delete a place. | update & delete only |
| `create_expense` | Record one expense. | no |
| `update_expense` | Patch an existing expense. | yes |
| `delete_expense` | Delete an expense. | yes |
| `create_income` | Record one income. | no |
| `update_income` | Patch an existing income. | yes |
| `delete_income` | Delete an income. | yes |
| `create_transfer` | Move money between two of the user's accounts. | yes |
| `update_transfer` | Patch an existing transfer. | yes |
| `delete_transfer` | Delete a transfer. | yes |
| `bulk_create_transactions` | Create up to 100 expenses and incomes at once. | yes |
| `bulk_delete_transactions` | Delete up to 100 expenses and incomes at once. | yes |
| `create_balance_adjustment` | Set an account's balance with an adjustment (not a transaction). | yes |
| `update_profile` | Change the name, display currency or default account. | yes |
| `convert_transaction` | Change a transaction's type (expense, income or transfer); the original is replaced. | yes |
| `create_recurring` | Create a recurring expense, income or transfer. | yes |
| `create_recurring_from_transaction` | Turn an existing transaction into a recurring series. | yes |
| `update_recurring` | Change, pause or resume a recurring series. | yes |
| `skip_recurring_occurrence` | Skip the next date of a recurring series. | yes |
| `delete_recurring` | Delete a recurring series, optionally with the transactions it created. | yes |
| `detect_recurring` | Suggest recurring series found in the history. Creates nothing. | yes |
| `confirm_action` | Execute a pending action by its action id. Only after the user agrees. | n/a (this is the confirmation step) |
| `cancel_action` | Discard a pending action by its action id. | n/a (this is the cancel step) |

Tools that fill in a missing date (`create_expense`, `create_income`, `create_transfer`, `bulk_create_transactions`, `create_balance_adjustment`, `create_recurring`, `create_recurring_from_transaction`) also accept `time_zone`, the user's IANA time zone.

Paged tools return `has_more` and `next_page`; call again with `page` set to `next_page` to get the rest.

The investment tools only appear when the user's Toston has investments enabled.

The server also offers resources (`toston://profile`, `toston://accounts`, `toston://categories`, `toston://places`, `toston://recurring`) and prompts (monthly review, log a receipt, subscriptions review, find and fix).
