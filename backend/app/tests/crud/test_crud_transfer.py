"""Tests for CRUD transfer operations."""
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.schemas.transfer import TransferCreate
from tests.utils import create_test_account, create_test_transfer, create_test_user


class TestCRUDTransferCreate:
    """Tests for transfer creation with account updates."""

    async def test_create_with_owner_basic(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="tr_create@example.com")
        from_account = await create_test_account(
            db_session, owner_id=user.id, name="From", initial_balance=2000.0
        )
        to_account = await create_test_account(
            db_session, owner_id=user.id, name="To", initial_balance=500.0
        )

        transfer_in = TransferCreate(
            amount=300.0,
            date="2025-03-01",
            description="Monthly transfer",
            from_acc=from_account.id,
            to_acc=to_account.id,
        )
        transfer = await crud.transfer.create_with_owner(
            db_session, obj_in=transfer_in, owner_id=user.id
        )

        assert transfer is not None
        assert transfer.id is not None
        assert transfer.amount == pytest.approx(300.0)
        assert transfer.date == date(2025, 3, 1)
        assert transfer.description == "Monthly transfer"
        assert transfer.from_acc == from_account.id
        assert transfer.to_acc == to_account.id
        assert transfer.owner_id == user.id

    async def test_create_updates_from_account(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="tr_from@example.com")
        from_acc = await create_test_account(
            db_session, owner_id=user.id, name="Source", initial_balance=1000.0
        )
        to_acc = await create_test_account(
            db_session, owner_id=user.id, name="Dest", initial_balance=0.0
        )

        transfer_in = TransferCreate(
            amount=400.0,
            date="2025-03-01",
            from_acc=from_acc.id,
            to_acc=to_acc.id,
        )
        await crud.transfer.create_with_owner(
            db_session, obj_in=transfer_in, owner_id=user.id
        )

        updated_from = await crud.account.get_by_id(
            db_session, owner_id=user.id, id=from_acc.id
        )
        assert updated_from.current_balance == pytest.approx(600.0)
        assert updated_from.total_transfers_out == pytest.approx(400.0)

    async def test_create_updates_to_account(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="tr_to@example.com")
        from_acc = await create_test_account(
            db_session, owner_id=user.id, name="Source", initial_balance=1000.0
        )
        to_acc = await create_test_account(
            db_session, owner_id=user.id, name="Dest", initial_balance=500.0
        )

        transfer_in = TransferCreate(
            amount=250.0,
            date="2025-03-01",
            from_acc=from_acc.id,
            to_acc=to_acc.id,
        )
        await crud.transfer.create_with_owner(
            db_session, obj_in=transfer_in, owner_id=user.id
        )

        updated_to = await crud.account.get_by_id(
            db_session, owner_id=user.id, id=to_acc.id
        )
        assert updated_to.current_balance == pytest.approx(750.0)
        assert updated_to.total_transfers_in == pytest.approx(250.0)

    async def test_create_with_nonexistent_from_account_returns_none(
        self, db_session: AsyncSession
    ):
        user = await create_test_user(db_session, email="tr_no_from@example.com")
        to_acc = await create_test_account(db_session, owner_id=user.id)

        transfer_in = TransferCreate(
            amount=100.0,
            date="2025-03-01",
            from_acc=999999,
            to_acc=to_acc.id,
        )
        result = await crud.transfer.create_with_owner(
            db_session, obj_in=transfer_in, owner_id=user.id
        )
        assert result is None

    async def test_create_with_nonexistent_to_account_returns_none(
        self, db_session: AsyncSession
    ):
        user = await create_test_user(db_session, email="tr_no_to@example.com")
        from_acc = await create_test_account(
            db_session, owner_id=user.id, initial_balance=1000.0
        )

        transfer_in = TransferCreate(
            amount=100.0,
            date="2025-03-01",
            from_acc=from_acc.id,
            to_acc=999999,
        )
        result = await crud.transfer.create_with_owner(
            db_session, obj_in=transfer_in, owner_id=user.id
        )
        assert result is None

    async def test_create_with_invalid_date(self, db_session: AsyncSession):
        """TransferCreate validates date at the schema level (unlike Expense/Income
        which override date as Optional[str]), so an invalid string is rejected."""
        from pydantic import ValidationError

        user = await create_test_user(db_session, email="tr_baddate@example.com")
        from_acc = await create_test_account(
            db_session, owner_id=user.id, initial_balance=1000.0
        )
        to_acc = await create_test_account(db_session, owner_id=user.id)

        with pytest.raises(ValidationError):
            TransferCreate(
                amount=100.0,
                date="not-a-date",
                from_acc=from_acc.id,
                to_acc=to_acc.id,
            )


class TestCRUDTransferGet:
    """Tests for transfer retrieval operations."""

    async def test_get_multi_by_owner(self, db_session: AsyncSession):
        user1 = await create_test_user(db_session, email="tr_multi1@example.com")
        user2 = await create_test_user(db_session, email="tr_multi2@example.com")

        acc1a = await create_test_account(
            db_session, owner_id=user1.id, name="A1"
        )
        acc1b = await create_test_account(
            db_session, owner_id=user1.id, name="B1"
        )
        acc2a = await create_test_account(
            db_session, owner_id=user2.id, name="A2"
        )
        acc2b = await create_test_account(
            db_session, owner_id=user2.id, name="B2"
        )

        await create_test_transfer(
            db_session, owner_id=user1.id, from_acc=acc1a.id, to_acc=acc1b.id
        )
        await create_test_transfer(
            db_session, owner_id=user1.id, from_acc=acc1b.id, to_acc=acc1a.id
        )
        await create_test_transfer(
            db_session, owner_id=user2.id, from_acc=acc2a.id, to_acc=acc2b.id
        )

        transfers_u1 = await crud.transfer.get_multi_by_owner(
            db_session, owner_id=user1.id
        )
        transfers_u2 = await crud.transfer.get_multi_by_owner(
            db_session, owner_id=user2.id
        )
        assert len(transfers_u1) == 2
        assert len(transfers_u2) == 1

    async def test_get_multi_by_date(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="tr_date@example.com")
        acc_a = await create_test_account(db_session, owner_id=user.id, name="A")
        acc_b = await create_test_account(db_session, owner_id=user.id, name="B")

        await create_test_transfer(
            db_session,
            owner_id=user.id,
            from_acc=acc_a.id,
            to_acc=acc_b.id,
            transfer_date=date(2025, 6, 10),
        )
        await create_test_transfer(
            db_session,
            owner_id=user.id,
            from_acc=acc_b.id,
            to_acc=acc_a.id,
            transfer_date=date(2025, 6, 20),
        )
        await create_test_transfer(
            db_session,
            owner_id=user.id,
            from_acc=acc_a.id,
            to_acc=acc_b.id,
            transfer_date=date(2025, 7, 5),
        )

        june = await crud.transfer.get_multi_by_date(
            db_session,
            owner_id=user.id,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 6, 30),
        )
        assert len(june) == 2

    async def test_get_multi_by_date_ordered(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="tr_order@example.com")
        acc_a = await create_test_account(db_session, owner_id=user.id, name="A")
        acc_b = await create_test_account(db_session, owner_id=user.id, name="B")

        await create_test_transfer(
            db_session,
            owner_id=user.id,
            from_acc=acc_a.id,
            to_acc=acc_b.id,
            transfer_date=date(2025, 6, 25),
        )
        await create_test_transfer(
            db_session,
            owner_id=user.id,
            from_acc=acc_b.id,
            to_acc=acc_a.id,
            transfer_date=date(2025, 6, 5),
        )

        transfers = await crud.transfer.get_multi_by_date(
            db_session,
            owner_id=user.id,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 6, 30),
        )
        assert len(transfers) == 2
        assert transfers[0].date <= transfers[1].date
