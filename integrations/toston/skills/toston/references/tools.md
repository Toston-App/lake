# Toston tools

| Tool | Purpose | Needs confirmation |
| --- | --- | --- |
| `get_user_profile` | Read the user's name and country. | no |
| `list_accounts` | List accounts with their type and current balance. | no |
| `list_categories` | List categories and subcategories. | no |
| `list_places` | List places (merchants). | no |
| `search_transactions` | Search and filter transactions, paged. | no |
| `get_period_summary` | Totals for a day, week, month, quarter, year or range. | no |
| `get_period_charts` | Chart data (breakdowns and trends) for a period. | no |
| `list_balance_adjustments` | List balance adjustments, optionally for one account. | no |
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
| `confirm_action` | Execute a pending action by its action id. Only after the user agrees. | n/a (this is the confirmation step) |
| `cancel_action` | Discard a pending action by its action id. | n/a (this is the cancel step) |
