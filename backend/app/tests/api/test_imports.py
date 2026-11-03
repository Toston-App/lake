"""API tests for the generic import flow (/api/v1/import)."""

from datetime import date
from unittest.mock import AsyncMock

import pytest
from app.models import Account, Category, Expense, Import, Income, Subcategory, Transfer
from app.models.imports import ImportStatus
from app.models.user import User
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import (
    create_test_account,
    create_test_category,
    create_test_expense,
    create_test_subcategory,
    create_test_user,
)

PREFIX = "/api/v1/import"

CSV = (
    "Type,Date,Item or Payee,Amount,Parent Category,Category,Account,Notes\n"
    "Expense,2025-01-05,Walmart,-120.50,Comida,Supermercado,BBVA,weekly\n"
    "Expense,2025-01-06,Tacos,-80,Comida,Antojitos,BBVA,\n"
    "Income,2025-01-15,Empresa,15000,Ingresos,Salario,BBVA,\n"
    "Transfer,2025-01-16,,-1000,(Transfer),(Transfer),BBVA,\n"
    "Transfer,2025-01-16,,1000,(Transfer),(Transfer),Efectivo,\n"
    "Expense,not-a-date,Broken,-1,Comida,Supermercado,BBVA,\n"
)


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    # The shared test ENCRYPTION_KEY is not a valid Fernet key.
    fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr("app.services.imports.service.get_fernet_key", lambda: fernet)
    monkeypatch.setattr(
        "app.api.api_v1.endpoints.imports.enforce_import_rate_limit",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.api.api_v1.endpoints.imports.invalidate_user_cache",
        AsyncMock(return_value=True),
    )


@pytest.fixture
async def setup_data(db_session: AsyncSession, test_user: User) -> dict:
    bbva = await create_test_account(
        db_session, owner_id=test_user.id, name="BBVA", initial_balance=1000
    )
    food = await create_test_category(db_session, owner_id=test_user.id, name="Comida")
    market = await create_test_subcategory(
        db_session, owner_id=test_user.id, category_id=food.id, name="Supermercado"
    )
    income = await create_test_category(
        db_session, owner_id=test_user.id, name="Ingresos", is_income=True
    )
    salary = await create_test_subcategory(
        db_session, owner_id=test_user.id, category_id=income.id, name="Salario"
    )
    return {
        "bbva": bbva,
        "food": food,
        "market": market,
        "income": income,
        "salary": salary,
    }


async def _upload(
    client: AsyncClient, content: str | bytes = CSV, name: str = "bluecoins.csv"
):
    body = content.encode() if isinstance(content, str) else content
    return await client.post(
        f"{PREFIX}/sessions", files={"file": (name, body, "text/csv")}
    )


async def _map(client: AsyncClient, session: dict, **options):
    return await client.put(
        f"{PREFIX}/sessions/{session['id']}/mapping",
        json={
            "mapping": session["mapping"],
            "options": {**session["options"], **options},
        },
    )


def _accept_suggestions(mapped: dict) -> dict:
    def clean(s: dict) -> dict:
        return {k: v for k, v in s.items() if k not in ("score", "source")}

    return {
        "accounts": {e["key"]: clean(e["suggestion"]) for e in mapped["accounts"]},
        "places": {e["key"]: clean(e["suggestion"]) for e in mapped["places"]},
        "categories": {e["key"]: clean(e["suggestion"]) for e in mapped["categories"]},
    }


async def _count(db: AsyncSession, model, owner_id: int) -> int:
    result = await db.execute(
        select(func.count()).select_from(model).where(model.owner_id == owner_id)
    )
    return result.scalar()


class TestFullFlow:
    async def test_upload_detects_columns(self, client: AsyncClient, setup_data):
        resp = await _upload(client)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["file_type"] == "csv"
        assert data["total_rows"] == 6
        assert data["mapping"]["date"] == 1
        assert data["mapping"]["category"] == 4
        assert data["mapping"]["subcategory"] == 5
        assert data["options"]["date_format"] == "YYYY-MM-DD"

    async def test_mapping_returns_values_to_match(
        self, client: AsyncClient, setup_data
    ):
        session = (await _upload(client)).json()
        resp = await _map(client, session)
        assert resp.status_code == 200, resp.text
        mapped = resp.json()
        assert mapped["summary"]["expenses"] == 2
        assert mapped["summary"]["incomes"] == 1
        assert mapped["summary"]["transfers"] == 1
        assert [e["row"] for e in mapped["errors"]] == [7]

        accounts = {e["value"]: e["suggestion"] for e in mapped["accounts"]}
        assert accounts["BBVA"] == {
            **accounts["BBVA"],
            "action": "match",
            "id": setup_data["bbva"].id,
        }
        assert accounts["Efectivo"]["action"] == "create"

        cats = {
            (e["category"], e["subcategory"]): e["suggestion"]
            for e in mapped["categories"]
        }
        assert (
            cats[("Comida", "Supermercado")]["subcategory_id"]
            == setup_data["market"].id
        )
        assert cats[("Comida", "Antojitos")]["action"] == "create_subcategory"
        assert (
            cats[("Ingresos", "Salario")]["subcategory_id"] == setup_data["salary"].id
        )

    async def test_commit_and_undo(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User, setup_data
    ):
        session = (await _upload(client)).json()
        mapped = (await _map(client, session)).json()
        decisions = _accept_suggestions(mapped)

        preview = await client.post(
            f"{PREFIX}/sessions/{session['id']}/preview", json=decisions
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["accounts_to_create"] == 1
        assert preview.json()["subcategories_to_create"] == 1

        resp = await client.post(
            f"{PREFIX}/sessions/{session['id']}/commit", json=decisions
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["expenses_imported"] == 2
        assert result["incomes_imported"] == 1
        assert result["transfers_imported"] == 1
        assert result["accounts_created"] == 1
        assert result["subcategories_created"] == 1
        assert result["rows_failed"] == 1

        bbva = await db_session.get(Account, setup_data["bbva"].id)
        await db_session.refresh(bbva)
        assert bbva.total_expenses == pytest.approx(200.5)
        assert bbva.total_incomes == pytest.approx(15000)
        assert bbva.total_transfers_out == pytest.approx(1000)
        assert bbva.current_balance == pytest.approx(1000 - 200.5 + 15000 - 1000)
        cash = (
            await db_session.execute(select(Account).where(Account.name == "Efectivo"))
        ).scalar_one()
        assert cash.current_balance == pytest.approx(1000)
        food = await db_session.get(Category, setup_data["food"].id)
        await db_session.refresh(food)
        assert food.total == pytest.approx(200.5)
        income_cat = await db_session.get(Category, setup_data["income"].id)
        await db_session.refresh(income_cat)
        assert income_cat.total == pytest.approx(15000)
        await db_session.refresh(test_user)
        assert test_user.balance_total == pytest.approx(15000 - 200.5)

        # Undo puts everything back and removes what the import created.
        undo = await client.post(f"{PREFIX}/{result['import_id']}/undo")
        assert undo.status_code == 200, undo.text
        assert undo.json()["status"] == "reverted"
        for model in (Expense, Income, Transfer):
            assert await _count(db_session, model, test_user.id) == 0
        await db_session.refresh(bbva)
        assert bbva.current_balance == pytest.approx(1000)
        assert bbva.total_expenses == pytest.approx(0)
        await db_session.refresh(food)
        assert food.total == pytest.approx(0)
        await db_session.refresh(test_user)
        assert test_user.balance_total == pytest.approx(0)
        assert (
            await db_session.execute(select(Account).where(Account.name == "Efectivo"))
        ).first() is None
        assert (
            await db_session.execute(
                select(Subcategory).where(Subcategory.name == "Antojitos")
            )
        ).first() is None
        assert (await db_session.get(Account, setup_data["bbva"].id)) is not None

        again = await client.post(f"{PREFIX}/{result['import_id']}/undo")
        assert again.status_code == 409

    async def test_create_category_with_subcategory(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User, setup_data
    ):
        session = (await _upload(client)).json()
        mapped = (await _map(client, session)).json()
        decisions = _accept_suggestions(mapped)
        key = next(
            e["key"] for e in mapped["categories"] if e["subcategory"] == "Antojitos"
        )
        decisions["categories"][key] = {
            "action": "create_category",
            "name": "Snacks",
            "color": "#FFAA00",
            "subcategory_name": "Antojitos",
        }
        resp = await client.post(
            f"{PREFIX}/sessions/{session['id']}/commit", json=decisions
        )
        assert resp.status_code == 200, resp.text
        snacks = (
            await db_session.execute(select(Category).where(Category.name == "Snacks"))
        ).scalar_one()
        assert snacks.total == pytest.approx(80)
        assert snacks.is_income is False

    async def test_duplicates_are_skipped_on_reimport(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User, setup_data
    ):
        for expected_dups in (0, 4):
            session = (await _upload(client)).json()
            decisions = _accept_suggestions((await _map(client, session)).json())
            if expected_dups:
                # Second time, "Efectivo" already exists, so match it.
                cash = (
                    await db_session.execute(
                        select(Account).where(Account.name == "Efectivo")
                    )
                ).scalar_one()
                key = next(k for k in decisions["accounts"] if k == "efectivo")
                decisions["accounts"][key] = {"action": "match", "id": cash.id}
            resp = await client.post(
                f"{PREFIX}/sessions/{session['id']}/commit", json=decisions
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["duplicates_skipped"] == expected_dups
        assert await _count(db_session, Expense, test_user.id) == 2

    async def test_upload_warns_when_file_was_imported(
        self, client: AsyncClient, setup_data
    ):
        session = (await _upload(client)).json()
        decisions = _accept_suggestions((await _map(client, session)).json())
        await client.post(f"{PREFIX}/sessions/{session['id']}/commit", json=decisions)
        again = (await _upload(client)).json()
        assert again["previously_imported_at"] is not None

    async def test_saved_profile_maps_next_upload(
        self, client: AsyncClient, db_session: AsyncSession, setup_data
    ):
        session = (await _upload(client)).json()
        mapping = {**session["mapping"], "title": None}
        await client.put(
            f"{PREFIX}/sessions/{session['id']}/mapping",
            json={"mapping": mapping, "options": session["options"]},
        )
        mapped = (
            await client.put(
                f"{PREFIX}/sessions/{session['id']}/mapping",
                json={"mapping": mapping, "options": session["options"]},
            )
        ).json()
        decisions = {
            **_accept_suggestions(mapped),
            "save_profile": True,
            "profile_name": "Bluecoins",
        }
        assert (
            await client.post(
                f"{PREFIX}/sessions/{session['id']}/commit", json=decisions
            )
        ).status_code == 200

        profiles = (await client.get(f"{PREFIX}/profiles")).json()
        assert [p["name"] for p in profiles] == ["Bluecoins"]
        second = (await _upload(client)).json()
        assert second["profile_name"] == "Bluecoins"
        assert second["mapping"]["title"] is None
        mapped2 = (await _map(client, second)).json()
        accounts = {e["value"]: e["suggestion"] for e in mapped2["accounts"]}
        assert accounts["Efectivo"]["action"] == "match"
        assert accounts["Efectivo"]["source"] == "profile"

    async def test_xlsx_upload(self, client: AsyncClient, setup_data):
        import io

        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Fecha", "Concepto", "Monto", "Cuenta"])
        ws.append([date(2025, 3, 1), "Oxxo", -35.5, "BBVA"])
        buffer = io.BytesIO()
        wb.save(buffer)
        resp = await _upload(client, buffer.getvalue(), "movimientos.xlsx")
        assert resp.status_code == 200, resp.text
        mapped = (await _map(client, resp.json())).json()
        assert mapped["preview"][0]["date"] == "2025-03-01"
        assert mapped["preview"][0]["amount"] == 35.5

    async def test_list_and_cancel(
        self, client: AsyncClient, db_session: AsyncSession, setup_data
    ):
        session = (await _upload(client)).json()
        assert (
            await client.delete(f"{PREFIX}/sessions/{session['id']}")
        ).status_code == 204
        assert (await _map(client, session)).status_code == 404
        assert (await client.get(PREFIX)).json() == []


class TestValidation:
    async def test_rejects_executable(self, client: AsyncClient):
        resp = await _upload(client, b"MZ\x90\x00 this is not a csv", "invoice.csv")
        assert resp.status_code == 422

    async def test_rejects_wrong_extension(self, client: AsyncClient):
        resp = await _upload(client, CSV, "shell.php")
        assert resp.status_code == 422

    async def test_mapping_requires_date_and_amount(self, client: AsyncClient):
        session = (await _upload(client)).json()
        resp = await client.put(
            f"{PREFIX}/sessions/{session['id']}/mapping",
            json={"mapping": {"title": 2}, "options": {}},
        )
        assert resp.status_code == 422

    async def test_commit_requires_mapping_first(self, client: AsyncClient):
        session = (await _upload(client)).json()
        resp = await client.post(f"{PREFIX}/sessions/{session['id']}/commit", json={})
        assert resp.status_code == 422

    async def test_income_needs_subcategory(self, client: AsyncClient, setup_data):
        session = (await _upload(client)).json()
        mapped = (await _map(client, session)).json()
        decisions = _accept_suggestions(mapped)
        key = next(e["key"] for e in mapped["categories"] if e["kind"] == "income")
        decisions["categories"][key] = {
            "action": "match",
            "category_id": setup_data["income"].id,
        }
        resp = await client.post(
            f"{PREFIX}/sessions/{session['id']}/commit", json=decisions
        )
        assert resp.status_code == 422

    async def test_failed_commit_writes_nothing(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User, setup_data
    ):
        session = (await _upload(client)).json()
        mapped = (await _map(client, session)).json()
        decisions = _accept_suggestions(mapped)
        key = next(e["key"] for e in mapped["categories"] if e["kind"] == "expense")
        decisions["categories"][key] = {
            "action": "match",
            "category_id": setup_data["income"].id,
        }
        resp = await client.post(
            f"{PREFIX}/sessions/{session['id']}/commit", json=decisions
        )
        assert resp.status_code == 422
        assert await _count(db_session, Expense, test_user.id) == 0
        assert await _count(db_session, Account, test_user.id) == 1


class TestOwnership:
    async def test_cannot_use_other_users_session(
        self, client: AsyncClient, db_session: AsyncSession, setup_data
    ):
        other = await create_test_user(db_session, email="other@example.com")
        draft = Import(
            owner_id=other.id,
            service="csv",
            status=ImportStatus.draft,
            staged_payload=b"x",
        )
        db_session.add(draft)
        await db_session.commit()
        resp = await client.put(
            f"{PREFIX}/sessions/{draft.id}/mapping",
            json={"mapping": {"date": 0, "amount": 1}, "options": {}},
        )
        assert resp.status_code == 404
        assert (
            await client.post(f"{PREFIX}/sessions/{draft.id}/commit", json={})
        ).status_code == 404
        assert (await client.delete(f"{PREFIX}/sessions/{draft.id}")).status_code == 404

    async def test_cannot_map_to_other_users_entities(
        self, client: AsyncClient, db_session: AsyncSession, setup_data
    ):
        other = await create_test_user(db_session, email="other@example.com")
        foreign_account = await create_test_account(
            db_session, owner_id=other.id, name="BBVA"
        )
        foreign_cat = await create_test_category(
            db_session, owner_id=other.id, name="Comida"
        )
        session = (await _upload(client)).json()
        mapped = (await _map(client, session)).json()

        decisions = _accept_suggestions(mapped)
        decisions["accounts"]["bbva"] = {"action": "match", "id": foreign_account.id}
        resp = await client.post(
            f"{PREFIX}/sessions/{session['id']}/commit", json=decisions
        )
        assert resp.status_code == 422

        decisions = _accept_suggestions(mapped)
        key = next(e["key"] for e in mapped["categories"] if e["kind"] == "expense")
        decisions["categories"][key] = {
            "action": "create_subcategory",
            "category_id": foreign_cat.id,
            "name": "x",
        }
        resp = await client.post(
            f"{PREFIX}/sessions/{session['id']}/commit", json=decisions
        )
        assert resp.status_code == 422

        resp = await _map(client, session, default_account_id=foreign_account.id)
        assert resp.status_code == 422

    async def test_cannot_undo_other_users_import(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        other = await create_test_user(db_session, email="other@example.com")
        done = Import(owner_id=other.id, service="csv", status=ImportStatus.completed)
        db_session.add(done)
        await db_session.commit()
        assert (await client.post(f"{PREFIX}/{done.id}/undo")).status_code == 404
        assert (await client.get(f"{PREFIX}/{done.id}")).status_code == 404

    async def test_undo_keeps_accounts_used_elsewhere(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User, setup_data
    ):
        session = (await _upload(client)).json()
        decisions = _accept_suggestions((await _map(client, session)).json())
        result = (
            await client.post(
                f"{PREFIX}/sessions/{session['id']}/commit", json=decisions
            )
        ).json()
        cash = (
            await db_session.execute(select(Account).where(Account.name == "Efectivo"))
        ).scalar_one()
        await create_test_expense(
            db_session, owner_id=test_user.id, account_id=cash.id, amount=5
        )
        assert (
            await client.post(f"{PREFIX}/{result['import_id']}/undo")
        ).status_code == 200
        assert (
            await db_session.execute(select(Account).where(Account.id == cash.id))
        ).first() is not None
