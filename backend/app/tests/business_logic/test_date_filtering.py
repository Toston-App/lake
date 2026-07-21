"""
Tests for date filtering logic in the expenses and incomes API endpoints.

All six DateFilterType values are tested:
  - date:    exact date  (YYYY-MM-DD)
  - week:    7-day range starting from date  (YYYY-MM-DD)
  - month:   full calendar month  (YYYY-MM)
  - quarter: full quarter  (YYYY-QX)
  - year:    full year  (YYYY)
  - range:   explicit start:end  (YYYY-MM-DD:YYYY-MM-DD)

These tests go through the API layer (not CRUD directly) to verify the
date-parsing logic that lives in the endpoint handlers.
"""
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import (
    create_test_account,
    create_test_expense,
    create_test_income,
)


class TestExpenseDateFiltering:
    """Test all 6 date filter types on the expenses endpoint."""

    @pytest.fixture(autouse=True)
    async def _setup(self, db_session: AsyncSession, test_user, client: AsyncClient):
        """Seed the database with expenses on known dates."""
        self.client = client
        account = await create_test_account(db_session, owner_id=test_user.id)

        # Spread expenses across 2025
        dates_amounts = [
            (date(2025, 1, 15), 100.0),   # Jan Q1
            (date(2025, 3, 10), 200.0),   # Mar Q1
            (date(2025, 4, 5), 300.0),    # Apr Q2
            (date(2025, 6, 20), 400.0),   # Jun Q2
            (date(2025, 7, 1), 500.0),    # Jul Q3
            (date(2025, 7, 5), 150.0),    # Jul Q3 (same month, within 7d of Jul 1)
            (date(2025, 10, 25), 600.0),  # Oct Q4
            (date(2025, 12, 31), 700.0),  # Dec Q4
        ]
        for d, amount in dates_amounts:
            await create_test_expense(
                db_session, owner_id=test_user.id,
                amount=amount, expense_date=d, account_id=account.id,
            )

    async def test_filter_by_exact_date(self):
        resp = await self.client.get("/api/v1/expenses/date/2025-01-15")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["amount"] == 100.0

    async def test_filter_by_exact_date_no_match(self):
        resp = await self.client.get("/api/v1/expenses/date/2025-02-01")
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    async def test_filter_by_date_invalid_format(self):
        resp = await self.client.get("/api/v1/expenses/date/not-a-date")
        assert resp.status_code == 400

    async def test_filter_by_week(self):
        # Week starting Jul 1 should include Jul 1 and Jul 5
        resp = await self.client.get("/api/v1/expenses/week/2025-07-01")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        amounts = {e["amount"] for e in data}
        assert amounts == {500.0, 150.0}

    async def test_filter_by_week_invalid_format(self):
        resp = await self.client.get("/api/v1/expenses/week/2025-13")
        assert resp.status_code == 400

    async def test_filter_by_month(self):
        # July 2025: two expenses
        resp = await self.client.get("/api/v1/expenses/month/2025-07")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    async def test_filter_by_month_single(self):
        # January 2025: one expense
        resp = await self.client.get("/api/v1/expenses/month/2025-01")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_filter_by_month_invalid_format(self):
        resp = await self.client.get("/api/v1/expenses/month/2025")
        assert resp.status_code == 400

    async def test_filter_by_quarter_q1(self):
        # Q1: Jan 1 - Mar 31 (should get Jan 15 and Mar 10)
        resp = await self.client.get("/api/v1/expenses/quarter/2025-Q1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        amounts = {e["amount"] for e in data}
        assert amounts == {100.0, 200.0}

    async def test_filter_by_quarter_q2(self):
        # Q2: Apr 1 - Jun 30 (should get Apr 5 and Jun 20)
        resp = await self.client.get("/api/v1/expenses/quarter/2025-Q2")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_filter_by_quarter_q4(self):
        # Q4: Oct 1 - Dec 31 (should get Oct 25 and Dec 31)
        resp = await self.client.get("/api/v1/expenses/quarter/2025-Q4")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_filter_by_quarter_invalid(self):
        resp = await self.client.get("/api/v1/expenses/quarter/2025-Q5")
        assert resp.status_code == 400

    async def test_filter_by_quarter_bad_format(self):
        resp = await self.client.get("/api/v1/expenses/quarter/2025")
        assert resp.status_code == 400

    async def test_filter_by_year(self):
        # All 8 expenses are in 2025
        resp = await self.client.get("/api/v1/expenses/year/2025")
        assert resp.status_code == 200
        assert len(resp.json()) == 8

    async def test_filter_by_year_no_match(self):
        resp = await self.client.get("/api/v1/expenses/year/2020")
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    async def test_filter_by_year_invalid(self):
        resp = await self.client.get("/api/v1/expenses/year/abcd")
        assert resp.status_code == 400

    async def test_filter_by_range(self):
        # Range: Jun 1 - Jul 31 (should get Jun 20, Jul 1, Jul 5)
        resp = await self.client.get("/api/v1/expenses/range/2025-06-01:2025-07-31")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    async def test_filter_by_range_single_day(self):
        resp = await self.client.get("/api/v1/expenses/range/2025-01-15:2025-01-15")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_filter_by_range_reversed_dates(self):
        # start > end should return 400
        resp = await self.client.get("/api/v1/expenses/range/2025-12-01:2025-01-01")
        assert resp.status_code == 400

    async def test_filter_by_range_invalid_format(self):
        resp = await self.client.get("/api/v1/expenses/range/2025-01-01")
        assert resp.status_code == 400


class TestIncomeDateFiltering:
    """Test date filters on the incomes endpoint (same logic, different entity)."""

    @pytest.fixture(autouse=True)
    async def _setup(self, db_session: AsyncSession, test_user, client: AsyncClient):
        self.client = client
        account = await create_test_account(db_session, owner_id=test_user.id)

        dates_amounts = [
            (date(2025, 2, 10), 1000.0),
            (date(2025, 2, 20), 1500.0),
            (date(2025, 5, 1), 2000.0),
            (date(2025, 8, 15), 3000.0),
            (date(2025, 11, 30), 500.0),
        ]
        for d, amount in dates_amounts:
            await create_test_income(
                db_session, owner_id=test_user.id,
                amount=amount, income_date=d, account_id=account.id,
            )

    async def test_income_filter_by_date(self):
        resp = await self.client.get("/api/v1/incomes/date/2025-02-10")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_income_filter_by_month(self):
        # Feb 2025: two incomes
        resp = await self.client.get("/api/v1/incomes/month/2025-02")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_income_filter_by_quarter_q1(self):
        # Q1: Feb 10 and Feb 20
        resp = await self.client.get("/api/v1/incomes/quarter/2025-Q1")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_income_filter_by_year(self):
        resp = await self.client.get("/api/v1/incomes/year/2025")
        assert resp.status_code == 200
        assert len(resp.json()) == 5

    async def test_income_filter_by_range(self):
        resp = await self.client.get("/api/v1/incomes/range/2025-01-01:2025-06-30")
        assert resp.status_code == 200
        assert len(resp.json()) == 3  # Feb 10, Feb 20, May 1

    async def test_income_filter_by_week(self):
        # Week starting Feb 10: should include Feb 10, possibly Feb 20 (10+7=17, so no)
        resp = await self.client.get("/api/v1/incomes/week/2025-02-10")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["amount"] == 1000.0

    async def test_income_filter_invalid_date_format(self):
        resp = await self.client.get("/api/v1/incomes/date/invalid")
        assert resp.status_code == 400

    async def test_income_filter_invalid_month_format(self):
        resp = await self.client.get("/api/v1/incomes/month/2025-13-01")
        assert resp.status_code == 400

    async def test_income_filter_range_reversed(self):
        resp = await self.client.get("/api/v1/incomes/range/2025-12-01:2025-01-01")
        assert resp.status_code == 400
