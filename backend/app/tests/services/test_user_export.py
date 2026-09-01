from app.models.imports import Import, ImportService
from app.models.user import User
from app.services.user_export import build_workbook
from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import AsyncSession
from tests.utils import create_test_account, create_test_expense, create_test_user


async def test_workbook_only_contains_owner_rows(
    db_session: AsyncSession, test_user: User
):
    account = await create_test_account(db_session, owner_id=test_user.id, name="Mine")
    await create_test_expense(
        db_session,
        owner_id=test_user.id,
        account_id=account.id,
        description="owner lunch",
    )

    other = await create_test_user(db_session, email="export-other@example.com")
    other_account = await create_test_account(db_session, owner_id=other.id, name="Theirs")
    await create_test_expense(
        db_session,
        owner_id=other.id,
        account_id=other_account.id,
        description="secret expense",
    )

    raw_csv = "date,amount\n2024-01-01,10"
    db_session.add(
        Import(
            owner_id=test_user.id,
            service=ImportService.CSV,
            file_content=raw_csv,
            file_size=len(raw_csv),
        )
    )
    await db_session.commit()

    path, counts = await build_workbook(db_session, test_user)
    try:
        wb = load_workbook(path)
        assert "items" not in wb.sheetnames
        assert "expenses" in wb.sheetnames
        assert "_manifest" in wb.sheetnames

        profile = wb["profile"]
        headers = [cell.value for cell in next(profile.iter_rows(min_row=1, max_row=1))]
        assert "hashed_password" not in headers
        assert "email" in headers

        expense_sheet = wb["expenses"]
        expense_headers = [
            cell.value for cell in next(expense_sheet.iter_rows(min_row=1, max_row=1))
        ]
        desc_idx = expense_headers.index("description")
        descriptions = [
            row[desc_idx]
            for row in expense_sheet.iter_rows(min_row=2, values_only=True)
            if row[desc_idx]
        ]
        assert "owner lunch" in descriptions
        assert "secret expense" not in descriptions
        assert counts["expenses"] == 1

        import_sheet = wb["imports"]
        import_headers = [
            cell.value for cell in next(import_sheet.iter_rows(min_row=1, max_row=1))
        ]
        assert "file_content" not in import_headers
        assert raw_csv not in {
            cell.value for row in import_sheet.iter_rows() for cell in row
        }
    finally:
        path.unlink(missing_ok=True)


async def test_empty_user_still_gets_workbook(db_session: AsyncSession, test_user: User):
    path, counts = await build_workbook(db_session, test_user)
    try:
        wb = load_workbook(path)
        assert counts["profile"] == 1
        assert counts["expenses"] == 0
        assert set(wb.sheetnames) >= {
            "profile",
            "accounts",
            "expenses",
            "incomes",
            "_manifest",
        }
    finally:
        path.unlink(missing_ok=True)
