from datetime import datetime
from typing import Any, List
import pandas as pd

from app.synonyms import get_synonyms
from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from fuzzywuzzy import fuzz

from app import crud, schemas
from app.api import deps

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
        for subcategory in category['subcategories']:
            subcategory_name = normalize(subcategory['name'])
            subcategory_score = fuzz.ratio(subcategory_name, new_category_normalized)

            if subcategory_score > best_score and subcategory_score >= threshold:
                best_match = {'category_id': category['id'], 'subcategory_id': subcategory['id']}
                best_score = subcategory_score

    return best_match

async def create_accounts(db: AsyncSession, owner_id:int, accounts: List[str]) -> dict:
    accounts_with_id = {}
    for account in accounts:
        account_data = {
            'name': account,
            'initial_balance': 0,
            'current_balance': 0,
        }
        account_in = schemas.AccountCreate(**account_data)
        new_account = await crud.account.create_with_owner(db=db, obj_in=account_in, owner_id=owner_id)

        accounts_with_id[account] = new_account.id

    return accounts_with_id


# this imports all transactions from:Main Dashboard > Transactions > Printer > Excel(.csv)
@router.post("/bluecoins", )
async def bluecoins(
        *,
        db: AsyncSession = Depends(deps.async_get_db),
        csv_file: UploadFile = File(...),
        # current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Import from bluecoins.
    """
    dataframe = pd.read_csv(csv_file.file)

    # Validate columns
    required_columns = ['Date', 'Title', 'Amount', 'Account', 'Category', 'Notes']

    for column in required_columns:
        if column not in dataframe.columns:
            raise HTTPException(status_code=400, detail=f"La columna '{column}' es requerida. Por favor revisa que el archivo tenga una columna llamada '{column}'.")


    # Create all needed accounts
    try:
        accounts = dataframe['Account'].unique().tolist()
        accounts_with_id = await create_accounts(db=db, owner_id=3, accounts=accounts)
    except Exception as e:
        # TODO: Log error to sentry
        print("🚀 ~ e", e)
        raise HTTPException(status_code=400, detail="Hay un error con las cuentas ('Accounts'). Por favor revisa que el archivo tenga una columna llamada 'Account' y que tenga nombres válidos.")

    # Extrapolate categories
    try:
        categories = dataframe['Category'].unique().tolist()
        user_categories = jsonable_encoder(await crud.category.get_multi_by_owner(db=db, owner_id=3))

        categories_with_id = {}

        for category in categories:
            match = find_best_match(category, user_categories)
            if match:
                categories_with_id[category] = match

    except Exception as e:
        # TODO: Log error to sentry
        print("🚀 ~ e", e)
        raise HTTPException(status_code=400, detail="Hay un error con las categorias ('Accounts'). Por favor revisa que el archivo tenga una columna llamada 'Category' y que tenga nombres válidos.")


    try:
        for index, row in dataframe.iterrows():
            account_id = accounts_with_id.get(row['Account'])
            date = str(datetime.strptime(row['Date'], "%d/%m/%Y %H:%M").date())
            typeIloc = row.iloc[0]
            type = typeIloc if typeIloc in {'Expense', 'Income', 'Transfer'} else ('Expense' if row['Amount'] < 0 else 'Income')
            amount = row['Amount']
            if amount < 0:
                amount = amount * -1
            description = f"{row['Title']} {row['Category']}"

            # Category and subcategory ids
            category = row['Category']
            category_id = None
            subcategory_id = None

            if category:
                match = categories_with_id.get(category)
                if match:
                    category_id = match['category_id']
                    subcategory_id = match['subcategory_id']

            if type == 'Expense':
                expense_in = schemas.ExpenseCreate(
                    account_id=account_id,
                    category_id=category_id,
                    subcategory_id=subcategory_id,
                    date=date,
                    amount=amount,
                    description=description,
                )
                await crud.expense.create_with_owner(db=db, obj_in=expense_in, owner_id=3)

            if type == 'Income':
                income_in = schemas.IncomeCreate(
                    account_id=account_id,
                    date=date,
                    amount=amount,
                    description=description,
                    subcategory_id=subcategory_id
                )
                await crud.income.create_with_owner(db=db, obj_in=income_in, owner_id=3)
    except Exception as e:
        print("🚀 ~ e", e)
        # TODO: Log error to sentry
        raise HTTPException(status_code=400, detail="Hubo un error al importar los datos. Por favor revisa que el archivo tenga el formato correcto.")

    return {"message": "Importación exitosa"}