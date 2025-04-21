from fastapi import APIRouter

from app.api.api_v1.endpoints import (
    accounts,
    ai,
    categories,
    expenses,
    imports,
    incomes,
    items,
    login,
    places,
    subcategories,
    transfers,
    users,
    utils,
    whatsapp,
    waha
)

api_router = APIRouter()
api_router.include_router(login.router, tags=["login"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(utils.router, prefix="/utils", tags=["utils"])
api_router.include_router(items.router, prefix="/items", tags=["items"])
api_router.include_router(places.router, prefix="/places", tags=["places"])
api_router.include_router(expenses.router, prefix="/expenses", tags=["expenses"])
api_router.include_router(incomes.router, prefix="/incomes", tags=["incomes"])
api_router.include_router(transfers.router, prefix="/transfers", tags=["transfers"])
api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
api_router.include_router(categories.router, prefix="/categories", tags=["categories"])
api_router.include_router(
    subcategories.router, prefix="/subcategories", tags=["subcategories"]
)
api_router.include_router(imports.router, prefix="/import", tags=["imports"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(whatsapp.router, prefix="/whatsapp", tags=["whatsapp"])
api_router.include_router(waha.router, prefix="/waha", tags=["whatsapp", "waha"])
