# Longer workflows

The safety rules in SKILL.md apply to every workflow here. Resolve ids first, and confirm gated actions only after the user agrees.

## Budgeting by category
1. Ask which month and which categories the user wants to watch, and any target amounts.
2. Call `get_period_summary` and `get_period_charts` with `date_filter_type` = "month" for that month.
3. Call `list_categories` to map names to ids.
4. For each watched category, compare the spent amount to the target. Report the remaining amount and the percentage used in a table.
5. For categories over target, call `search_transactions` with `categories` and the month's `start_date` and `end_date`, and show the largest items.
6. Toston stores no budget targets, so keep the targets in the conversation only and say so.

## Cleaning up duplicate places
1. Call `list_places` and group names that differ only by case, spacing, punctuation or a suffix (for example "Joe's" and "Joes Cafe").
2. Show the proposed groups in a table and ask the user which name to keep for each.
3. For each duplicate, call `search_transactions` with `places` set to its id to find the affected transactions.
4. Re-point them with `update_expense` or `update_income` (`data` with the kept `place_id`). These are confirmation-gated, so show the previews together and confirm each only after the user agrees.
5. Once a duplicate has no transactions left, offer `manage_place` (`action` = "delete"), which is also gated.

## Yearly summary
1. Call `get_period_summary` with `date_filter_type` = "year" and `date` = "YYYY", then `get_period_charts` for the same year.
2. For a month-by-month view, call `get_period_summary` for each month with `date_filter_type` = "month".
3. Present total income, total expenses and net, then a table of months, the top 5 categories and the top 5 places.
4. Compare with the previous year when the user asks.
5. Offer a follow-up on the months or categories that stand out.
