# Category and Subcategory Totals

This feature tracks the total amounts for each category and subcategory based on their associated expenses and incomes.

## How It Works

- Each category and subcategory has a `total` field that stores the sum of all expenses and incomes associated with it
- For categories:
  - Expense totals are added directly from expenses linked to the category
  - Income totals are added from incomes linked to subcategories that belong to the category
- For subcategories:
  - Totals include all expenses and incomes directly linked to the subcategory
- The total is updated automatically when:
  - An expense or income is created
  - An expense or income is updated (changing amount, category, or subcategory)
  - An expense or income is deleted

## Updating Totals for Existing Data

If you've just added this feature to an existing database with data, you need to update the totals for all existing records. There are two ways to do this:

### Option 1: Using the Alembic Migration

The migration script `469548e06c7f_add_total_to_category_and_sub.py` automatically updates totals for existing data when you run:

```
alembic upgrade head
```

This is suitable for smaller databases.

### Option 2: Using the Update Totals Script

For larger databases, it's recommended to use the dedicated script:

```
# Inside the Docker container
./scripts/update_totals.sh

# Directly with Python
python -m app.update_totals
```

## Manual Update Using SQL

If needed, you can also update the totals using SQL queries:

```sql
-- Reset all category totals
UPDATE category SET total = 0;

-- Update category totals from expenses
UPDATE category 
SET total = (
    SELECT COALESCE(SUM(amount), 0)
    FROM expense
    WHERE expense.category_id = category.id
);

-- Add income amounts to category totals (via subcategories)
UPDATE category 
SET total = total + (
    SELECT COALESCE(SUM(i.amount), 0)
    FROM income i
    JOIN subcategory s ON i.subcategory_id = s.id
    WHERE s.category_id = category.id
);

-- Reset all subcategory totals
UPDATE subcategory SET total = 0;

-- Update subcategory totals from expenses
UPDATE subcategory 
SET total = (
    SELECT COALESCE(SUM(amount), 0)
    FROM expense
    WHERE expense.subcategory_id = subcategory.id
);

-- Add income amounts to subcategory totals
UPDATE subcategory 
SET total = total + (
    SELECT COALESCE(SUM(amount), 0)
    FROM income
    WHERE income.subcategory_id = subcategory.id
);
```

## Troubleshooting

If totals ever get out of sync, run the update script to recalculate all totals based on the current data:

```
python -m app.update_totals
```

If you encounter the error "column category_id does not exist" when trying to update totals, it's because in this database design, income is only linked to subcategories, not directly to categories.
