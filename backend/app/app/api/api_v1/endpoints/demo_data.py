#!/usr/bin/env python
import datetime
import random
from typing import Any, Optional

from faker import Faker
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.api.api_v1.endpoints.accounts import create_account, read_accounts
from app.api.api_v1.endpoints.categories import read_categories
from app.api.api_v1.endpoints.expenses import create_expenses_bulk
from app.api.api_v1.endpoints.incomes import create_incomes_bulk
from app.api.api_v1.endpoints.places import create_place, read_places
from app.api.api_v1.endpoints.transfers import create_transfer
from app.models.account import AccountType  # For creating accounts with specific types

router = APIRouter()
fake = Faker()

async def _get_or_create_base_data_orm(db: AsyncSession, from_user: models.User) -> dict[str, list]:
    """
    Fetches existing base data (accounts, categories, subcategories, places) for the user.
    Creates minimal default data if necessary (e.g., ensuring at least 4 accounts,
    default categories/subcategories if none usable exist, default places if none exist).
    Returns a dictionary containing lists of the ORM objects to be used.
    """
    fetched_data = {
        "accounts": [],
        "categories": [],
        "places": []
    }

    # 1. Accounts
    existing_accounts = await read_accounts(db=db, current_user=from_user)
    fetched_data["accounts"].extend(existing_accounts)

    if len(fetched_data["accounts"]) < 4:
        num_accounts_to_create = 4 - len(fetched_data["accounts"])
        acc_types_enum_map = {
            "Checking Account": AccountType.CHECKING,
            "Savings Account": AccountType.SAVINGS,
            "Cash & Wallet": AccountType.CASH,
            "Credit Card": AccountType.CREDIT_CARDS,
        }
        acc_type_names = list(acc_types_enum_map.keys())
        acc_colors = ["#168FFF", "#34C759", "#FF9500", "#AF52DE"]

        for i in range(num_accounts_to_create):
            account_type_name = acc_type_names[i % len(acc_type_names)]
            account_type_enum = acc_types_enum_map.get(account_type_name, AccountType.MISCELLANEOUS)
            initial_balance = round(random.uniform(100, 3000), 2)

            account_in = schemas.AccountCreate(
                name=f"{account_type_name} {len(fetched_data['accounts']) + 1}",
                type=account_type_enum,
                color=acc_colors[i % len(acc_colors)],
                initial_balance=initial_balance
            )

            account = await create_account(db=db, account_in=account_in, current_user=from_user)
            fetched_data["accounts"].append(account)

    # 2. Categories & Subcategories
    fetched_data["categories"] = await read_categories(db=db, current_user=from_user)
    # this must exist in the DB

    # 3. Places
    existing_places = await read_places(db=db, current_user=from_user)

    if not existing_places:
        place_names = ["Supermarket", "Online Retailer", "Local Cafe", "Gas Station", "Utility Company"]

        for name in place_names:
            place_in = schemas.PlaceCreate(name=name)
            place = await create_place(db=db, place_in=place_in, current_user=from_user)
            fetched_data["places"].append(place)
    else:
        fetched_data["places"].extend(existing_places)

    return fetched_data


@router.post("/generate-demo-data", response_model=schemas.Msg, status_code=201)
async def generate_fake_user_data(
    db: AsyncSession = Depends(deps.async_get_db),
    user_id: int = Body(..., embed=True, description="ID of the user to generate data for."),
    num_transactions: int = Body(50, ge=1, le=5000, embed=True, description="Number of transactions to generate."),
    start_date_str: Optional[str] = Body(None, embed=True, description="Start date for transactions (YYYY-MM-DD). Defaults to 1 year ago."),
    end_date_str: Optional[str] = Body(None, embed=True, description="End date for transactions (YYYY-MM-DD). Defaults to today."),
    current_superuser: models.User = Depends(deps.get_current_active_superuser) # needed to access this endpoint
) -> Any:
    """
    Generate fake financial data (transactions) for a specified user, using existing or default accounts, categories, and places.
    Only accessible by superusers.
    """
    user = await crud.user.get(db, id=user_id)

    if not user:
        raise HTTPException(status_code=404, detail=f"User with id {user_id} not found.")

    try:
        start_date = (datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
                      if start_date_str else datetime.date.today() - datetime.timedelta(days=365))
        end_date = (datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
                    if end_date_str else datetime.date.today())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {e}. Use YYYY-MM-DD.")

    if start_date > end_date:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date.")

    base_data_map = await _get_or_create_base_data_orm(db=db, from_user=user)
    usable_accounts: list[models.Account] = base_data_map["accounts"]
    all_categories: list[models.Category] = base_data_map["categories"]
    usable_places: list[models.Place] = base_data_map["places"]

    if not usable_accounts or len(usable_accounts) < 4:
        raise HTTPException(status_code=500, detail="Failed to ensure at least 5 accounts for transaction generation.")
    if not all_categories:
        raise HTTPException(status_code=500, detail="Failed to ensure categories for transaction generation.")

    income_subcategories = [subcat for c in all_categories if c.is_income for subcat in c.subcategories]
    expense_subcategories = [subcat for c in all_categories if not c.is_income for subcat in c.subcategories]

    if not income_subcategories:
        raise HTTPException(status_code=500, detail="No income subcategories available/created for transaction generation.")

    incomes_to_create_bulk: list[schemas.IncomeCreate] = []
    expenses_to_create_bulk: list[schemas.ExpenseCreate] = []
    transfers_created_count = 0
    transactions_attempted = 0

    for _ in range(num_transactions):
        transactions_attempted +=1
        transaction_type = random.choices(["income", "expense", "transfer"], weights=[0.3, 0.5, 0.2], k=1)[0]
        transaction_date = str(fake.date_between(start_date=start_date, end_date=end_date))
        description = fake.sentence(nb_words=random.randint(3,6)).replace(".","")
        amount = round(random.uniform(5.0, 750.0), 2)
        made_from = "Web"

        if transaction_type == "income" and income_subcategories:
            target_account_orm = random.choice(usable_accounts)
            subcat_orm = random.choice(income_subcategories)
            place_orm = random.choice(usable_places) if usable_places and random.random() > 0.4 else None

            income_in = schemas.IncomeCreate(
                amount=amount, date=transaction_date, description=description,
                account_id=target_account_orm.id, subcategory_id=subcat_orm.id,
                place_id=place_orm.id if place_orm else None, made_from=made_from
            )
            incomes_to_create_bulk.append(income_in)

        elif transaction_type == "expense" and expense_subcategories:
            source_account_orm = random.choice(usable_accounts)
            subcat_orm = random.choice(expense_subcategories)
            place_orm = random.choice(usable_places) if usable_places and random.random() > 0.2 else None
            category_id_for_expense = getattr(subcat_orm, 'category_id_for_expense', subcat_orm.category_id)

            expense_in = schemas.ExpenseCreate(
                amount=amount, date=transaction_date, description=description,
                account_id=source_account_orm.id, category_id=category_id_for_expense,
                subcategory_id=subcat_orm.id, place_id=place_orm.id if place_orm else None,
                made_from=made_from
            )
            expenses_to_create_bulk.append(expense_in)

        elif transaction_type == "transfer" and len(usable_accounts) >= 2:
            from_acc_orm, to_acc_orm = random.sample(usable_accounts, 2)
            transfer_in = schemas.TransferCreate(
                amount=amount, date=transaction_date, description=f"Transfer to {to_acc_orm.name}",
                from_acc=from_acc_orm.id, to_acc=to_acc_orm.id
            )
            # Transfers are still created one-by-one
            await create_transfer(db=db, transfer_in=transfer_in, current_user=user)
            transfers_created_count += 1

    # --- Create Incomes and Expenses in Bulk ---
    if incomes_to_create_bulk:
        await create_incomes_bulk(db=db, incomes_in=incomes_to_create_bulk, current_user=user)
    if expenses_to_create_bulk:
        await create_expenses_bulk(db=db, expenses_in=expenses_to_create_bulk, current_user=user)

    total_generated = len(incomes_to_create_bulk) + len(expenses_to_create_bulk) + transfers_created_count

    return schemas.Msg(msg=f"Successfully generated {total_generated} transactions ({len(incomes_to_create_bulk)} incomes, {len(expenses_to_create_bulk)} expenses, {transfers_created_count} transfers) for user {user_id}. Attempted to generate {transactions_attempted} based on num_transactions param.")
