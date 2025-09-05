from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fuzzywuzzy import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.models.imports import ImportService
from app.synonyms import get_synonyms

router = APIRouter()
synonyms = get_synonyms()


def normalize(text):
    return text.strip().lower()


def get_synonym(category):
    # Check if the normalized category exists in synonyms, otherwise return original
    return synonyms.get(normalize(category), category)


def find_best_match(new_category, user_categories, threshold=80):
    # Normalize and check if the new category has a synonym
    new_category_normalized = get_synonym(new_category)

    best_match = None
    best_score = 0

    for category in user_categories:
        # Check subcategories for fuzzy match
        for subcategory in category["subcategories"]:
            subcategory_name = normalize(subcategory["name"])
            subcategory_score = fuzz.ratio(subcategory_name, new_category_normalized)

            if subcategory_score > best_score and subcategory_score >= threshold:
                best_match = {
                    "category_id": category["id"],
                    "subcategory_id": subcategory["id"],
                }
                best_score = subcategory_score

    return best_match


async def create_accounts(
    db: AsyncSession, owner_id: int, accounts: list[str], import_id: str
) -> dict:
    accounts_with_id = {}
    for account in accounts:
        if account == "":
            continue

        account_data = {
            "import_id": import_id,
            "name": account,
            "initial_balance": 0,
            "current_balance": 0,
        }
        account_in = schemas.AccountCreate(**account_data)
        new_account = await crud.account.create_with_owner(
            db=db, obj_in=account_in, owner_id=owner_id
        )

        accounts_with_id[account] = new_account.id

    return accounts_with_id


async def create_sites(
    db: AsyncSession, owner_id: int, sites: list[str], import_id: str
) -> dict:
    sites_with_id = {}
    for site in sites:
        if site == "":
            continue

        site_data = {
            "import_id": import_id,
            "name": site,
            "is_online": False,
        }
        site_in = schemas.PlaceCreate(**site_data)
        new_site = await crud.place.create_with_owner(
            db=db, obj_in=site_in, owner_id=owner_id
        )

        sites_with_id[site] = new_site.id

    return sites_with_id


async def process_csv(
    csv_file: UploadFile,
    column_mapping: dict[str, str],
) -> pd.DataFrame:
    """
    Process the CSV file and return a standardized DataFrame.

    :param csv_file: The uploaded CSV file
    :param column_mapping: A dictionary mapping standard column names to actual CSV column names
    :return: A standardized DataFrame
    """
    try:
        df = pd.read_csv(csv_file.file)

        # Check if all required columns are present
        for standard_col, csv_col in column_mapping.items():
            if csv_col not in df.columns:
                raise ValueError(f"Column '{csv_col}' is missing from the CSV file.")

        # Rename columns to standard names
        df = df.rename(columns={v: k for k, v in column_mapping.items()})

        # Ensure all standard columns are present
        for col in [
            "Date",
            "Amount",
            "Category",
            "Title",
            "Description",
            "Account",
            "Site",
        ]:
            if col not in df.columns:
                df[col] = ""  # Add empty column if not present

        # Standardize the DataFrame
        df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
        df["Amount"] = df["Amount"].astype(float)
        df["Category"] = df["Category"].fillna("")
        df["Title"] = df["Title"].fillna("")
        df["Description"] = df["Description"].fillna("")
        df["Account"] = df["Account"].fillna("")
        df["Site"] = df["Site"].fillna("")

        return df[
            ["Date", "Amount", "Category", "Title", "Description", "Account", "Site"]
        ]
    except Exception as e:
        raise HTTPException(status_code=409, detail=f"Error processing CSV: {str(e)}")


async def import_transactions(
    db: AsyncSession,
    current_user: models.User,
    df: pd.DataFrame,
    accounts_with_id: dict,
    sites_with_id: dict,
    categories_with_id: dict,
    import_id: str,
) -> tuple[int, int, int, int]:
    """
    Import transactions from the standardized DataFrame.
    Returns a tuple of (total_imported, expenses_imported, incomes_imported, unmatched_categories)
    """
    expenses_imported = 0
    incomes_imported = 0
    unmatched_categories = 0

    try:
        for _, row in df.iterrows():
            account_id = accounts_with_id.get(row["Account"])
            site_id = sites_with_id.get(row["Site"])
            date = row["Date"]
            amount = abs(row["Amount"])
            description = f"{row['Title']} {row['Description']}".strip()
            type = "Expense" if row["Amount"] < 0 else "Income"

            category = row["Category"]
            category_id = None
            subcategory_id = None

            if category:
                match = categories_with_id.get(category)
                if match:
                    category_id = match["category_id"]
                    subcategory_id = match["subcategory_id"]
                else:
                    unmatched_categories += 1
                    print(f"🚀🚀🚀🚀🚀 - Unmatched category: {category}")

            if type == "Expense":
                expense_in = schemas.ExpenseCreate(
                    account_id=account_id,
                    category_id=category_id,
                    subcategory_id=subcategory_id,
                    date=date,
                    amount=amount,
                    description=description,
                    place_id=site_id,
                    import_id=import_id,
                )
                await crud.expense.create_with_owner(
                    db=db, obj_in=expense_in, owner_id=current_user.id
                )
                expenses_imported += 1
            else:
                income_in = schemas.IncomeCreate(
                    account_id=account_id,
                    date=date,
                    amount=amount,
                    description=description,
                    subcategory_id=subcategory_id,
                    place_id=site_id,
                    import_id=import_id,
                )
                await crud.income.create_with_owner(
                    db=db, obj_in=income_in, owner_id=current_user.id
                )
                incomes_imported += 1

        total_imported = expenses_imported + incomes_imported
        return total_imported, expenses_imported, incomes_imported, unmatched_categories
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Error importing transactions: {str(e)}"
        )


async def process_import(
    db: AsyncSession,
    current_user: models.User,
    csv_file: UploadFile,
    column_mapping: dict[str, str],
    service: ImportService,
) -> dict[str, Any]:
    """
    Process the import and return detailed results.
    """
    if csv_file.filename == "":
        raise HTTPException(status_code=400, detail="Filename is empty")

    df = await process_csv(csv_file, column_mapping)

    # Create import record first
    import_in = schemas.ImportCreate(
        service=service,
        # this doesn't sound like a good idea after all i think. if somehow the table get leaked, the expenses, accounts, incomes and more data will be accessible thanks to this shitty. im to lazy to remove it lol
        file_content="",
        # TODO: check how to get file size
        file_size=0,
    )
    import_obj = await crud.imports.create_with_owner(
        db=db, obj_in=import_in, owner_id=current_user.id
    )
    import_id = import_obj.id  # Use this ID for related records

    # Create all needed accounts
    accounts = df["Account"].unique().tolist()
    accounts_with_id = await create_accounts(
        db=db, owner_id=current_user.id, accounts=accounts, import_id=import_id
    )

    # Create all needed sites
    sites = df["Site"].unique().tolist()
    sites_with_id = await create_sites(
        db=db, owner_id=current_user.id, sites=sites, import_id=import_id
    )

    # Extrapolate categories
    categories = df["Category"].unique().tolist()
    user_categories = jsonable_encoder(
        await crud.category.get_multi_by_owner(db=db, owner_id=current_user.id)
    )
    categories_with_id = {
        category: find_best_match(category, user_categories) for category in categories
    }

    (
        total_imported,
        expenses_imported,
        incomes_imported,
        unmatched_categories,
    ) = await import_transactions(
        db,
        current_user,
        df,
        accounts_with_id,
        sites_with_id,
        categories_with_id,
        import_id,
    )

    # Update import record with results
    import_update = schemas.ImportUpdate(
        total_rows_processed=len(df),
        total_transactions_imported=total_imported,
        expenses_imported=expenses_imported,
        incomes_imported=incomes_imported,
        accounts_created=len(accounts_with_id),
        sites_created=len(sites_with_id),
        unmatched_categories=unmatched_categories,
        ended_at=datetime.now().isoformat(),
    )
    await crud.imports.update(db=db, db_obj=import_obj, obj_in=import_update)

    return {
        "message": "Importación exitosa",
        "total_transactions_imported": total_imported,
        "expenses_imported": expenses_imported,
        "incomes_imported": incomes_imported,
        "accounts_created": len(accounts_with_id),
        "sites_created": len(sites_with_id),
        "unmatched_categories": unmatched_categories,
        "total_rows_processed": len(df),
    }


@router.post("/bluecoins")
async def bluecoins(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    csv_file: UploadFile = File(...),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Import from bluecoins.
    """
    column_mapping = {
        "Date": "Date",
        "Amount": "Amount",
        "Category": "Category",
        "Title": "Title",
        "Description": "Notes",
        "Account": "Account",
    }
    return await process_import(
        db, current_user, csv_file, column_mapping, ImportService.BLUECOINS
    )


@router.post("/csv")
async def import_csv(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    csv_file: UploadFile = File(...),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Import from generic CSV.
    """
    column_mapping = {
        "Date": "Date",
        "Amount": "Amount",
        "Category": "Category",
        "Title": "Title",
        "Description": "Description",
        "Account": "Account",
        "Site": "Site",
    }
    return await process_import(
        db, current_user, csv_file, column_mapping, ImportService.CSV
    )
