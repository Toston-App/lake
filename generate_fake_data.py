#!/usr/bin/env python

# Generate 50 transactions for user ID 1 (defaults):
    # python generate_fake_data.py | cat
# 2. Generate 100 transactions for user ID 5, over the last 90 days:

# (Note: Date command syntax might vary slightly on Linux vs. macOS)
#     python generate_fake_data.py --user-id 5 --num-transactions 100 --start-date $(date -v-90d +%Y-%m-%d) --end-date $(date +%Y-%m-%d) | cat

import random
import datetime
from faker import Faker
import argparse

# --- Configuration ---
DEFAULT_USER_ID = 1
DEFAULT_NUM_TRANSACTIONS = 50
DEFAULT_START_DATE = datetime.date.today() - datetime.timedelta(days=365)
DEFAULT_END_DATE = datetime.date.today()
DEFAULT_COUNTRY = 'US' # Used for Faker localization if needed, and User country

# --- Initialization ---
fake = Faker() # You might want to localize, e.g., Faker('en_US')

# --- Data Storage ---
accounts = []
categories = []
subcategories = []
places = []
sql_statements = []

# --- Helper Functions ---
def generate_sql_insert(table_name, data):
    columns = ", ".join(data.keys())
    values_placeholders = ", ".join([f"${i+1}" for i in range(len(data))]) # Use $ placeholders for psycopg2/asyncpg
    values = list(data.values())
    # Escape single quotes in string values for literal SQL generation
    formatted_values = []
    for v in values:
        if isinstance(v, str):
            formatted_values.append(f"'{v.replace("'", "''")}'")
        elif v is None:
            formatted_values.append("NULL")
        elif isinstance(v, (datetime.date, datetime.datetime)):
             formatted_values.append(f"'{v.isoformat()}'")
        else:
            formatted_values.append(str(v))

    literal_values = ", ".join(formatted_values)
    # We'll generate literal SQL for easy copy-pasting, though parameterized queries are safer in applications
    sql = f"INSERT INTO \"{table_name}\" ({columns}) VALUES ({literal_values}) RETURNING id;"
    # For simplicity in copy-pasting, we won't capture RETURNING id here directly in the output script.
    # We'll assume sequential IDs starting from 1 for generated supporting data.
    sql_statements.append(f"INSERT INTO \"{table_name}\" ({columns}) VALUES ({literal_values});")
    # Return the *next* assumed ID for linking
    if table_name == "account": return len(accounts) + 1
    if table_name == "category": return len(categories) + 1
    if table_name == "subcategory": return len(subcategories) + 1
    if table_name == "place": return len(places) + 1
    return None # For transactions


def generate_sql_update(table_name, data, where_clause):
    set_clauses = ", ".join([f"{k} = {v}" for k, v in data.items()])
    sql = f"UPDATE \"{table_name}\" SET {set_clauses} WHERE {where_clause};"
    sql_statements.append(sql)

# --- Data Generation Functions ---

def create_base_data(user_id):
    global accounts, categories, subcategories, places
    print(f"Generating base data for user_id: {user_id}...")

    # 1. Accounts (min 2 needed for transfers)
    acc_types = ["Checking Accounts", "Savings Accounts", "Credit Cards", "Cash & Wallet"]
    acc_colors = ["#168FFF", "#34C759", "#FF9500", "#AF52DE"]
    for i in range(max(2, random.randint(2,4))): # Ensure at least 2 accounts
        initial_balance = round(random.uniform(0 if acc_types[i % len(acc_types)] == "Credit Cards" else 50, 5000), 2)
        acc_data = {
            "name": f"{acc_types[i % len(acc_types)]} {i+1}",
            "type": acc_types[i % len(acc_types)],
            "color": acc_colors[i % len(acc_colors)],
            "initial_balance": initial_balance,
            "current_balance": initial_balance, # Start with current = initial
            "total_expenses": 0.0,
            "total_incomes": 0.0,
            "total_transfers_in": 0.0,
            "total_transfers_out": 0.0,
            "created_at": datetime.datetime.now(),
            "owner_id": user_id,
            "import_id": None # Assuming no import link for generated data
        }
        # Assume sequential IDs starting from 1 for generated accounts
        acc_id = len(accounts) + 1
        accounts.append({"id": acc_id, **acc_data})
        generate_sql_insert("account", acc_data)

    # 2. Categories & Subcategories
    cat_subcat = {
        "Income": ["Salary", "Freelance", "Bonus", "Investment"],
        "Food & Dining": ["Groceries", "Restaurants", "Coffee Shops"],
        "Transportation": ["Fuel", "Public Transport", "Taxi/Rideshare"],
        "Utilities": ["Electricity", "Water", "Internet", "Phone"],
        "Housing": ["Rent/Mortgage", "Maintenance"],
        "Personal Care": ["Haircut", "Toiletries"],
        "Entertainment": ["Movies", "Concerts", "Streaming"],
        "Shopping": ["Clothing", "Electronics", "Gifts"],
    }
    for cat_name, sub_list in cat_subcat.items():
        is_income_category = (cat_name == "Income")
        cat_data = {
            "name": cat_name,
            "owner_id": user_id,
            "is_income": is_income_category,
            "is_expense": not is_income_category,
            "created_at": datetime.datetime.now()
        }
        # Assume sequential IDs
        cat_id = len(categories) + 1
        categories.append({"id": cat_id, **cat_data})
        generate_sql_insert("category", cat_data)

        for sub_name in sub_list:
            sub_data = {
                "name": sub_name,
                "owner_id": user_id,
                "category_id": cat_id,
                "created_at": datetime.datetime.now()
            }
            # Assume sequential IDs
            sub_id = len(subcategories) + 1
            subcategories.append({"id": sub_id, **sub_data})
            generate_sql_insert("subcategory", sub_data)

    # 3. Places
    place_names = ["Workplace", "SuperMart", "Gas Station XYZ", "Cafe Central", "Online Store", "Electric Co.", "Water Dept.", "Internet Provider", "Cinema Complex", "Local Restaurant"]
    for name in place_names:
        place_data = {
            "name": name,
            "owner_id": user_id,
            "created_at": datetime.datetime.now()
        }
        # Assume sequential IDs
        place_id = len(places) + 1
        places.append({"id": place_id, **place_data})
        generate_sql_insert("place", place_data)

    print(f"Generated {len(accounts)} accounts, {len(categories)} categories, {len(subcategories)} subcategories, {len(places)} places.")
    print("-" * 20)

def generate_transactions(num_transactions, user_id, start_date, end_date):
    print(f"Generating {num_transactions} transactions for user_id: {user_id}...")
    if not accounts:
        print("Error: No accounts generated. Cannot create transactions.")
        return
    if not categories or not subcategories:
        print("Error: No categories/subcategories generated. Cannot create transactions.")
        return

    account_balances = {acc['id']: {'initial': acc['initial_balance'], 'current': acc['initial_balance'],
                                     'incomes': 0.0, 'expenses': 0.0, 'transfers_in': 0.0, 'transfers_out': 0.0}
                        for acc in accounts}
    user_balances = {'income': 0.0, 'outcome': 0.0}

    income_subcategories = [s for s in subcategories if any(c['id'] == s['category_id'] and c['is_income'] for c in categories)]
    expense_subcategories = [s for s in subcategories if any(c['id'] == s['category_id'] and c['is_expense'] for c in categories)]

    for _ in range(num_transactions):
        transaction_type = random.choices(["income", "expense", "transfer"], weights=[0.2, 0.6, 0.2], k=1)[0]
        transaction_date = fake.date_between(start_date=start_date, end_date=end_date)
        description = fake.sentence(nb_words=4)
        amount = round(random.uniform(5.0, 500.0), 2)
        made_from = random.choice(["Web", "WhatsApp", "OCR"])

        if transaction_type == "income" and income_subcategories:
            target_account = random.choice(accounts)
            subcat = random.choice(income_subcategories)
            place = random.choice(places) if places and random.random() > 0.3 else None
            income_data = {
                "amount": amount,
                "date": transaction_date,
                "description": description,
                "owner_id": user_id,
                "account_id": target_account['id'],
                "subcategory_id": subcat['id'],
                "place_id": place['id'] if place else None,
                "created_at": datetime.datetime.now(),
                "import_id": None,
                "made_from": made_from
            }
            generate_sql_insert("income", income_data)
            # Update tracking balances
            account_balances[target_account['id']]['incomes'] += amount
            account_balances[target_account['id']]['current'] += amount
            user_balances['income'] += amount

        elif transaction_type == "expense" and expense_subcategories:
            source_account = random.choice(accounts)
            subcat = random.choice(expense_subcategories)
            cat_id = subcat['category_id'] # Need category_id for expense table
            place = random.choice(places) if places and random.random() > 0.1 else None
            expense_data = {
                "amount": amount,
                "date": transaction_date,
                "description": description,
                "owner_id": user_id,
                "account_id": source_account['id'],
                "category_id": cat_id,
                "subcategory_id": subcat['id'],
                "place_id": place['id'] if place else None,
                "created_at": datetime.datetime.now(),
                "import_id": None,
                "made_from": made_from
            }
            generate_sql_insert("expense", expense_data)
            # Update tracking balances
            account_balances[source_account['id']]['expenses'] += amount
            account_balances[source_account['id']]['current'] -= amount
            user_balances['outcome'] += amount

        elif transaction_type == "transfer" and len(accounts) >= 2:
            from_acc, to_acc = random.sample(accounts, 2)
            transfer_data = {
                "amount": amount,
                "date": transaction_date,
                "description": f"Transfer to {to_acc['name']}",
                "from_acc": from_acc['id'],
                "to_acc": to_acc['id'],
                "owner_id": user_id,
                "created_at": datetime.datetime.now()
            }
            generate_sql_insert("transfer", transfer_data)
            # Update tracking balances
            account_balances[from_acc['id']]['transfers_out'] += amount
            account_balances[from_acc['id']]['current'] -= amount
            account_balances[to_acc['id']]['transfers_in'] += amount
            account_balances[to_acc['id']]['current'] += amount
            # Transfers don't affect overall user income/outcome totals directly

    print(f"Generated {num_transactions} transaction INSERT statements.")
    print("-" * 20)

    # Generate Account UPDATE statements
    print("Generating Account UPDATE statements...")
    for acc_id, balances in account_balances.items():
        if balances['incomes'] > 0 or balances['expenses'] > 0 or balances['transfers_in'] > 0 or balances['transfers_out'] > 0:
             update_data = {
                 "current_balance": round(balances['current'], 2),
                 "total_incomes": round(balances['incomes'], 2),
                 "total_expenses": round(balances['expenses'], 2),
                 "total_transfers_in": round(balances['transfers_in'], 2),
                 "total_transfers_out": round(balances['transfers_out'], 2),
                 "updated_at": f"'{datetime.datetime.now().isoformat()}'" # Needs quoting for direct SQL
             }
             generate_sql_update("account", update_data, f"id = {acc_id} AND owner_id = {user_id}")
    print("-" * 20)

    # Generate User UPDATE statements (relative update)
    # Note: Assumes user exists and country is set elsewhere or default is ok.
    # Requires user 'country' column exists and is NOT NULL.
    print("Generating User UPDATE statements...")
    if user_balances['income'] > 0 or user_balances['outcome'] > 0:
        total_change = round(user_balances['income'] - user_balances['outcome'], 2)
        update_data = {
            # Update relatively: balance_total = balance_total + total_change
            "balance_total": f"balance_total + {total_change}",
            "balance_income": f"balance_income + {round(user_balances['income'], 2)}",
            "balance_outcome": f"balance_outcome + {round(user_balances['outcome'], 2)}",
            "updated_at": f"'{datetime.datetime.now().isoformat()}'" # Needs quoting for direct SQL
        }
        generate_sql_update("user", update_data, f"id = {user_id}")
    print("-" * 20)

# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate fake financial data SQL for a user.')
    parser.add_argument('--user-id', type=int, default=DEFAULT_USER_ID, help=f'ID of the user to generate data for (default: {DEFAULT_USER_ID})')
    parser.add_argument('--num-transactions', type=int, default=DEFAULT_NUM_TRANSACTIONS, help=f'Number of transactions to generate (default: {DEFAULT_NUM_TRANSACTIONS})')
    parser.add_argument('--start-date', type=datetime.date.fromisoformat, default=DEFAULT_START_DATE, help=f'Start date for transactions (YYYY-MM-DD) (default: {DEFAULT_START_DATE})')
    parser.add_argument('--end-date', type=datetime.date.fromisoformat, default=DEFAULT_END_DATE, help=f'End date for transactions (YYYY-MM-DD) (default: {DEFAULT_END_DATE})')
    parser.add_argument('--country', type=str, default=DEFAULT_COUNTRY, help=f'Country code for the user (required, affects User model) (default: {DEFAULT_COUNTRY})')

    args = parser.parse_args()

    # --- Initial User Setup ---
    # Add a check/update for the user's country if it's not set
    # This assumes the 'country' column exists and is nullable or you handle setting it
    sql_statements.append(f"-- Ensure user {args.user_id} exists and country is set (run manually if needed)")
    # Example: sql_statements.append(f"UPDATE "user" SET country = '{args.country}' WHERE id = {args.user_id} AND country IS NULL;")
    # For this script, we'll assume the user exists and country might be set already.
    # The UPDATE statement for balances will fail if the user doesn't exist.

    # --- Generation ---
    create_base_data(args.user_id)
    generate_transactions(args.num_transactions, args.user_id, args.start_date, args.end_date)

    # --- Output ---
    print("--- SQL Statements ---")
    print("-- Base Data (Accounts, Categories, Subcategories, Places)")
    # Find the split point
    split_index = -1
    for i, stmt in enumerate(sql_statements):
        if 'INSERT INTO "income"' in stmt or 'INSERT INTO "expense"' in stmt or 'INSERT INTO "transfer"' in stmt:
            split_index = i
            break

    if split_index != -1:
        print("\n".join(sql_statements[:split_index]))
        print("\n-- Transactions (Incomes, Expenses, Transfers)")
        update_start_index = -1
        # Find the first UPDATE statement for accounts or user
        for i, stmt in enumerate(sql_statements[split_index:], start=split_index):
             if 'UPDATE "account"' in stmt or 'UPDATE "user"' in stmt:
                  update_start_index = i
                  break
        
        if update_start_index != -1:
            # Print transactions up to the first update
            print("\n".join(sql_statements[split_index:update_start_index]))
            # Print all updates (accounts and user)
            print("\n-- Balance Updates (Accounts, User)")
            print("\n".join(sql_statements[update_start_index:]))
        else: # Only base data and transactions generated, no updates needed/generated
             print("\n".join(sql_statements[split_index:]))

    else: # Only base data generated
        print("\n".join(sql_statements))


    print("\n--- End SQL Statements ---")
    print(f"Total SQL statements generated: {len(sql_statements)}")
    print(f"Run these statements against your PostgreSQL database.")
    print(f"Remember to ensure user with ID {args.user_id} exists before running.") 