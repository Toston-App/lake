import json

from app import crud, schemas


async def db_seeder(db, owner_id):
    with open("app/seed/accounts.json") as f:
        accounts = json.load(f)
        for account in accounts:
            account_in = schemas.AccountCreate(
                name=account["name"],
                initial_balance=account["initial_balance"],
                owner_id=owner_id,
            )
            await crud.account.create_with_owner(
                db=db, obj_in=account_in, owner_id=owner_id
            )

    with open("app/seed/places.json") as f:
        places = json.load(f)
        for place in places:
            place_in = schemas.PlaceCreate(
                name=place["name"], is_online=place["is_online"], owner_id=owner_id
            )
            await crud.place.create_with_owner(
                db=db, obj_in=place_in, owner_id=owner_id
            )

    with open("app/seed/expenses.json") as f:
        expenses = json.load(f)
        for expense in expenses:
            print(
                "🚀 ~ file: seeder.py:18 ~ expense:",
                expense,
                type(expense["account_id"]),
            )
            expense_in = schemas.ExpenseCreate(
                description=expense["description"],
                amount=expense["amount"],
                date=expense["date"],
                account_id=expense["account_id"],
                category_id=expense["category_id"],
                subcategory_id=expense["subcategory_id"],
                place_id=expense["place_id"],
            )
            await crud.expense.create_with_owner(
                db=db, obj_in=expense_in, owner_id=owner_id
            )

    with open("app/seed/incomes.json") as f:
        incomes = json.load(f)
        for income in incomes:
            income_in = schemas.IncomeCreate(
                description=income["description"],
                amount=income["amount"],
                date=income["date"],
                account_id=income["account_id"],
                subcategory_id=income["subcategory_id"],
                place_id=income["place_id"],
            )
            await crud.income.create_with_owner(
                db=db, obj_in=income_in, owner_id=owner_id
            )

    with open("app/seed/transfers.json") as f:
        transfers = json.load(f)
        for transfer in transfers:
            transfer_in = schemas.TransferCreate(
                description=transfer["description"],
                amount=transfer["amount"],
                from_acc=transfer["from_acc"],
                to_acc=transfer["to_acc"],
            )
            await crud.transfer.create_with_owner(
                db=db, obj_in=transfer_in, owner_id=owner_id
            )
