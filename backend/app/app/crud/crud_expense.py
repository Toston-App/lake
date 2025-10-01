# TODO: refactor bulk to only update balance once and not for each expense
from datetime import datetime

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Date, and_, asc, cast
from sqlalchemy import update as updateDb
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app import crud
from app.crud.base import CRUDBase
from app.models.expense import Expense
from app.schemas.expense import ExpenseCreate, ExpenseUpdate


class CRUDExpense(CRUDBase[Expense, ExpenseCreate, ExpenseUpdate]):
    async def create_with_owner(
        self, db: AsyncSession, *, obj_in: ExpenseCreate, owner_id: int
    ) -> Expense:
        obj_in_data = jsonable_encoder(obj_in)

        # Convert date string to datetime object
        date_str = obj_in_data["date"]
        if date_str:
            try:
                obj_in_data["date"] = datetime.strptime(date_str, "%Y-%m-%d").date()
            except:
                obj_in_data["date"] = None

        # Update account balance
        if obj_in_data["account_id"]:
            update = await crud.account.update_by_id_and_field(
                db=db,
                owner_id=owner_id,
                id=obj_in_data["account_id"],
                column="total_expenses",
                amount=obj_in_data["amount"],
            )

            if update == None:
                obj_in_data["account_id"] = None

        if obj_in_data["category_id"]:
            category = await crud.category.get(db=db, id=obj_in_data["category_id"])

            if not category or category.owner_id != owner_id:
                obj_in_data["category_id"] = None
            else:
                await db.execute(
                    updateDb(category.__class__)
                    .where(category.__class__.id == category.id)
                    .values(total=category.total + obj_in_data["amount"])
                    .execution_options(synchronize_session="fetch")
                )

        if obj_in_data["subcategory_id"]:
            subcategory = await crud.subcategory.get(
                db=db, id=obj_in_data["subcategory_id"]
            )

            if not subcategory or subcategory.category_id != obj_in_data["category_id"] or subcategory.owner_id != owner_id:
                obj_in_data["subcategory_id"] = None
            else:
                # Update subcategory total
                await db.execute(
                    updateDb(subcategory.__class__)
                    .where(subcategory.__class__.id == subcategory.id)
                    .values(total=subcategory.total + obj_in_data["amount"])
                    .execution_options(synchronize_session="fetch")
                )

        if obj_in_data["place_id"]:
            place = await crud.place.get(db=db, id=obj_in_data["place_id"])

            if not place:
                obj_in_data["place_id"] = None

        # Update User balance and total outcomes
        await crud.user.update_balance(
            db=db, user_id=owner_id, is_Expense=True, amount=obj_in_data["amount"]
        )

        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def create_multi_with_owner(
        self, db: AsyncSession, *, obj_list: list[ExpenseCreate], owner_id: int
    ) -> list[Expense]:
        if not obj_list:
            return []
        
        # Pre-fetch and validate all referenced entities
        account_ids = [obj.account_id for obj in obj_list if obj.account_id]
        category_ids = [obj.category_id for obj in obj_list if obj.category_id]
        subcategory_ids = [obj.subcategory_id for obj in obj_list if obj.subcategory_id]
        place_ids = [obj.place_id for obj in obj_list if obj.place_id]
        
        # Batch fetch entities
        accounts = {}
        categories = {}
        subcategories = {}
        places = {}
        
        if account_ids:
            account_results = await db.execute(
                select(crud.account.model).filter(crud.account.model.id.in_(account_ids))
            )
            accounts = {acc.id: acc for acc in account_results.scalars().all()}
        
        if category_ids:
            category_results = await db.execute(
                select(crud.category.model).filter(crud.category.model.id.in_(category_ids))
            )
            categories = {cat.id: cat for cat in category_results.scalars().all()}
            
        if subcategory_ids:
            subcategory_results = await db.execute(
                select(crud.subcategory.model).filter(crud.subcategory.model.id.in_(subcategory_ids))
            )
            subcategories = {sub.id: sub for sub in subcategory_results.scalars().all()}
            
        if place_ids:
            place_results = await db.execute(
                select(crud.place.model).filter(crud.place.model.id.in_(place_ids))
            )
            places = {place.id: place for place in place_results.scalars().all()}
        
        # Process expenses and calculate totals
        created_expenses = []
        account_updates = {}
        category_updates = {}
        subcategory_updates = {}
        total_user_expense = 0
        
        for obj_in in obj_list:
            obj_in_data = jsonable_encoder(obj_in)

            # Convert date string to datetime object
            date_str = obj_in_data["date"]
            if date_str:
                try:
                    obj_in_data["date"] = datetime.strptime(date_str, "%Y-%m-%d").date()
                except:
                    obj_in_data["date"] = None

            # Validate and accumulate account updates
            if obj_in_data["account_id"] and obj_in_data["account_id"] in accounts:
                account = accounts[obj_in_data["account_id"]]
                if account.owner_id == owner_id:
                    account_updates[obj_in_data["account_id"]] = account_updates.get(obj_in_data["account_id"], 0) + obj_in_data["amount"]
                else:
                    obj_in_data["account_id"] = None
            else:
                obj_in_data["account_id"] = None

            # Validate and accumulate category updates
            if obj_in_data["category_id"] and obj_in_data["category_id"] in categories:
                category = categories[obj_in_data["category_id"]]
                if category.owner_id == owner_id:
                    category_updates[obj_in_data["category_id"]] = category_updates.get(obj_in_data["category_id"], 0) + obj_in_data["amount"]
                else:
                    obj_in_data["category_id"] = None
            else:
                obj_in_data["category_id"] = None

            # Validate and accumulate subcategory updates  
            if obj_in_data["subcategory_id"] and obj_in_data["subcategory_id"] in subcategories:
                subcategory = subcategories[obj_in_data["subcategory_id"]]
                if (subcategory.category_id == obj_in_data["category_id"] and 
                    subcategory.owner_id == owner_id):
                    subcategory_updates[obj_in_data["subcategory_id"]] = subcategory_updates.get(obj_in_data["subcategory_id"], 0) + obj_in_data["amount"]
                else:
                    obj_in_data["subcategory_id"] = None
            else:
                obj_in_data["subcategory_id"] = None

            # Validate place
            if obj_in_data["place_id"] and obj_in_data["place_id"] not in places:
                obj_in_data["place_id"] = None

            total_user_expense += obj_in_data["amount"]
            
            # Create expense object
            db_obj = self.model(**obj_in_data, owner_id=owner_id)
            db.add(db_obj)
            created_expenses.append(db_obj)

        # Batch update accounts
        for account_id, amount in account_updates.items():
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=owner_id,
                id=account_id,
                column="total_expenses", 
                amount=amount,
            )

        # Batch update categories
        for category_id, amount in category_updates.items():
            category = categories[category_id]
            await db.execute(
                updateDb(category.__class__)
                .where(category.__class__.id == category_id)
                .values(total=category.total + amount)
                .execution_options(synchronize_session="fetch")
            )

        # Batch update subcategories
        for subcategory_id, amount in subcategory_updates.items():
            subcategory = subcategories[subcategory_id]
            await db.execute(
                updateDb(subcategory.__class__)
                .where(subcategory.__class__.id == subcategory_id)
                .values(total=subcategory.total + amount)
                .execution_options(synchronize_session="fetch")
            )

        # Update user balance once
        await crud.user.update_balance(
            db=db, user_id=owner_id, is_Expense=True, amount=total_user_expense
        )
        
        # Single commit for all operations
        await db.commit()
        
        # Refresh all created expenses
        for expense in created_expenses:
            await db.refresh(expense)
            
        return created_expenses

    async def remove_multi(self, db: AsyncSession, *, ids: list[int]) -> list[Expense]:
        removed_expenses = []
        for id in ids:
            expense = await self.remove(db, id=id)
            if expense:
                removed_expenses.append(expense)
        return removed_expenses

    async def get_multi_by_owner(
        self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> list[Expense]:
        result = await db.execute(
            select(self.model)
            .filter(Expense.owner_id == owner_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_multi_by_date(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        start_date: Date = None,
        end_date: str = None,
    ) -> list[Expense]:
        query = select(self.model)

        query = query.where(
            and_(
                self.model.owner_id == owner_id,
                cast(self.model.date, Date) >= start_date,
                cast(self.model.date, Date) <= end_date,
            )
        ).order_by(asc(self.model.date))

        result = await db.execute(query)

        return result.scalars().all()


expense = CRUDExpense(Expense)
