from typing import Any, Dict, Optional, Union

# from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select
from fastapi.encoders import jsonable_encoder

from app.core.security import get_password_hash, verify_password
from app.crud.base import CRUDBase
from app.models.user import User
from app.schemas.user import UserCreate, UserCreateUuid, UserUpdate
from app import crud, schemas
from app.categories_and_sub import categories_and_sub

async def add_categories_to_db(db, owner_id):
    for category_data in categories_and_sub:
        category_data_in = schemas.CategoryCreate(
            name=category_data["name"],
            color=category_data["color"],
            icon=category_data["icon"],
            owner_id=owner_id,
            is_default=True,
            is_income=True if category_data["name"] == "Ingresos" else False
        )

        category = await crud.category.create_with_owner(db=db, obj_in=category_data_in, owner_id=owner_id)

        if "sub_categories" in category_data:
            for subcategory_data in category_data["sub_categories"]:
                subcategory_data_in = schemas.SubcategoryCreate(
                    name=subcategory_data["name"],
                    icon=subcategory_data["icon"],
                    category_id=category.id,
                    is_default=True
                )
                await crud.subcategory.create_with_owner(db=db, obj_in=subcategory_data_in, owner_id=owner_id)

class CRUDUser(CRUDBase[User, UserCreate, UserUpdate]):
    async def get_by_email(self, db: AsyncSession, *, email: str) -> Optional[User]:
        result = await db.execute(select(User).filter(User.email == email))
        return result.scalars().first()

    async def get_by_uuid(self, db: AsyncSession, *, uuid: str) -> Optional[User]:
        result = await db.execute(select(User).filter(User.uuid == uuid))
        return result.scalars().first()

    async def create(self, db: AsyncSession, *, obj_in: UserCreate | UserCreateUuid ) -> User:
        if isinstance(obj_in, UserCreate):
            db_obj = User(
                email=obj_in.email,
                hashed_password=get_password_hash(obj_in.password),
                name=obj_in.name,
                country=obj_in.country,
                is_superuser=obj_in.is_superuser,
                items=[]
            )
            db.add(db_obj)
            await db.commit()
            await db.refresh(db_obj)
            await add_categories_to_db(db, db_obj.id)
            return db_obj

        #UUID auth
        db_obj = User(
            email=None,
            hashed_password=None,
            uuid=obj_in.uuid,
            name=None,
            country="MXN",
            is_superuser=False,
            items=[]
        )
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        await add_categories_to_db(db, db_obj.id)
        return db_obj

    async def update(
        self, db: AsyncSession, *, db_obj: User, obj_in: Union[UserUpdate, Dict[str, Any]]
    ) -> User:
        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.dict(exclude_unset=True)
        if update_data.get("password", None):
            hashed_password = get_password_hash(update_data["password"])
            del update_data["password"]
            update_data["hashed_password"] = hashed_password
        return await super().update(db, db_obj=db_obj, obj_in=update_data)

    async def authenticate(self, db: AsyncSession, *, email: str, password: str) -> Optional[User]:
        user = await self.get_by_email(db, email=email)
        if not user:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user

    def is_active(self, user: User) -> bool:
        return user.is_active

    def is_superuser(self, user: User) -> bool:
        return user.is_superuser

    async def update_balance(self, db: AsyncSession, *, user_id: int, is_Expense: bool, amount: float) -> User:
        user = await crud.user.get(db, id=user_id)

        user_data = jsonable_encoder(user)
        user_in = UserUpdate(**user_data)

        if is_Expense:
            user_in.balance_total -= amount
            user_in.balance_outcome += amount

        if not is_Expense:
            user_in.balance_total += amount
            user_in.balance_income += amount

        user = await crud.user.update(db, db_obj=user, obj_in=user_in)

        return user



user = CRUDUser(User)
