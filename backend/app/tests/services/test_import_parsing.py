"""Unit tests for the generic import reader, parser and matcher."""

import io
import zipfile
from datetime import date
from decimal import Decimal

import openpyxl
import pytest
from app.services.imports import matching
from app.services.imports.parsing import (
    ColumnMapping,
    ParseOptions,
    detect_date_format,
    detect_decimal_separator,
    detect_mapping,
    parse_amount,
    parse_rows,
)
from app.services.imports.reader import (
    MAX_COLUMNS,
    MAX_FILE_BYTES,
    UnsafeFileError,
    clean_cell,
    read_upload,
)


def _xlsx(rows: list[list[object]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Reader / security
# ---------------------------------------------------------------------------
class TestReaderSecurity:
    def test_reads_semicolon_latin1_csv(self):
        data = "Fecha;Monto;Categoría\n01/02/2025;-12,50;Café\n".encode("cp1252")
        result = read_upload("banco.csv", data)
        assert result.delimiter == ";"
        assert result.rows[1] == ["01/02/2025", "-12,50", "Café"]

    def test_reads_utf16_csv(self):
        data = "Date,Amount\n2025-01-01,-5\n".encode("utf-16")
        assert read_upload("x.csv", data).rows[1] == ["2025-01-01", "-5"]

    @pytest.mark.parametrize(
        "payload",
        [
            b"MZ\x90\x00\x03\x00\x00\x00",  # Windows executable
            b"\x7fELF\x02\x01\x01",  # Linux executable
            b"%PDF-1.7\n",
            b"#!/bin/sh\nrm -rf /\n",
            b"\x89PNG\r\n\x1a\n",
        ],
    )
    def test_rejects_binaries_renamed_to_csv(self, payload: bytes):
        with pytest.raises(UnsafeFileError):
            read_upload("statement.csv", payload + b"a,b\n1,2\n")

    def test_rejects_nul_bytes(self):
        with pytest.raises(UnsafeFileError):
            read_upload("x.csv", b"Date,Amount\n2025-01-01,\x00-5\n")

    def test_rejects_unknown_extension(self):
        with pytest.raises(UnsafeFileError):
            read_upload("shell.php", b"Date,Amount\n2025-01-01,-5\n")

    def test_php_shell_is_just_text(self):
        # Not rejected as binary, but it is only ever treated as strings.
        result = read_upload("x.csv", b"<?php system($_GET['c']); ?>\nfoo\n")
        assert all(isinstance(cell, str) for row in result.rows for cell in row)
        assert "system" in " ".join(result.rows[0])

    def test_rejects_zip_renamed_to_csv(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("evil.exe", b"MZ")
        with pytest.raises(UnsafeFileError):
            read_upload("x.csv", buffer.getvalue())

    def test_rejects_too_large(self):
        with pytest.raises(UnsafeFileError):
            read_upload("x.csv", b"a" * (MAX_FILE_BYTES + 1))

    def test_rejects_too_many_columns(self):
        header = ",".join(f"c{i}" for i in range(MAX_COLUMNS + 5))
        with pytest.raises(UnsafeFileError):
            read_upload("x.csv", f"{header}\n1\n".encode())

    def test_truncates_long_cells(self):
        result = read_upload("x.csv", f"a,b\n{'x' * 5000},1\n".encode())
        assert len(result.rows[1][0]) == 1000
        assert result.truncated_cells == 1

    def test_strips_control_and_bidi_characters(self):
        assert clean_cell("Uber‮ gpj.exe\x07") == "Uber gpj.exe"

    def test_reads_xlsx_values_not_formulas(self):
        data = _xlsx([["Date", "Amount", "Note"], ["2025-01-02", -10, "=1+1"]])
        rows = read_upload("x.xlsx", data).rows
        # data_only=True: a formula without a cached value comes back empty,
        # it is never evaluated or returned as a formula to run.
        assert rows[1][:2] == ["2025-01-02", "-10"]

    def test_rejects_xlsx_with_macros(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/workbook.xml", "<workbook/>")
            archive.writestr("xl/vbaProject.bin", b"macro")
        with pytest.raises(UnsafeFileError, match="macros"):
            read_upload("x.xlsx", buffer.getvalue())

    def test_rejects_xlsx_zip_bomb(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xl/workbook.xml", "<workbook/>")
            archive.writestr("xl/worksheets/sheet1.xml", b"0" * (20 * 1024 * 1024))
        with pytest.raises(UnsafeFileError):
            read_upload("x.xlsx", buffer.getvalue())

    def test_rejects_fake_xlsx(self):
        with pytest.raises(UnsafeFileError):
            read_upload("x.xlsx", b"Date,Amount\n")


# ---------------------------------------------------------------------------
# Detection / parsing
# ---------------------------------------------------------------------------
class TestDetection:
    def test_bluecoins_headers(self):
        headers = [
            "(1)Type", "(2)Date", "(3)Item or Payee", "(4)Amount", "(5)Currency",
            "(6)ConversionRate", "(7)Parent Category", "(8)Category",
            "(9)Account Type", "(10)Account", "(11)Notes", "(12) Label", "(13) Status",
        ]  # fmt: skip
        mapping = detect_mapping(headers)
        assert mapping.type == 0
        assert mapping.date == 1
        assert mapping.title == 2
        assert mapping.amount == 3
        assert mapping.category == 6
        assert mapping.subcategory == 7
        assert mapping.account == 9
        assert mapping.description == 10

    def test_spanish_bank_headers(self):
        mapping = detect_mapping(["Fecha", "Concepto", "Cargo", "Abono", "Saldo"])
        assert (mapping.date, mapping.title, mapping.debit, mapping.credit) == (
            0,
            1,
            2,
            3,
        )
        assert mapping.amount is None

    def test_date_format_detection(self):
        assert detect_date_format(["2025-01-31"]) == "YYYY-MM-DD"
        assert detect_date_format(["03/04/2025", "25/04/2025"]) == "DD/MM/YYYY"
        assert detect_date_format(["04/25/2025", "03/04/2025"]) == "MM/DD/YYYY"
        assert detect_date_format(["5 ene 2025"]) == "DD MMM YYYY"

    def test_decimal_detection(self):
        assert detect_decimal_separator(["1.234,56", "12,50"]) == ","
        assert detect_decimal_separator(["1,234.56", "12.50"]) == "."

    @pytest.mark.parametrize(
        ("raw", "sep", "expected"),
        [
            ("-12.50", ".", Decimal("-12.50")),
            ("$1,234.56", ".", Decimal("1234.56")),
            ("(45.00)", ".", Decimal("-45.00")),
            ("45.00-", ".", Decimal("-45.00")),
            ("MXN -1.234,5", ",", Decimal("-1234.5")),
            ("−10", ".", Decimal("-10")),
            ("abc", ".", None),
            ("1e9999", ".", None),
        ],
    )
    def test_parse_amount(self, raw, sep, expected):
        assert parse_amount(raw, sep) == expected


class TestParseRows:
    def test_signed_amounts(self):
        grid = [
            ["Date", "Amount", "Title"],
            ["2025-01-01", "-10", "Coffee"],
            ["2025-01-02", "100", "Salary"],
        ]
        result = parse_rows(
            grid, ColumnMapping(date=0, amount=1, title=2), ParseOptions()
        )
        assert [(r.kind, r.amount) for r in result.rows] == [
            ("expense", Decimal("10.00")),
            ("income", Decimal("100.00")),
        ]

    def test_debit_credit_and_invert(self):
        grid = [
            ["Fecha", "Cargo", "Abono"],
            ["01/02/2025", "50", ""],
            ["02/02/2025", "", "70"],
        ]
        mapping = ColumnMapping(date=0, debit=1, credit=2)
        result = parse_rows(grid, mapping, ParseOptions(date_format="DD/MM/YYYY"))
        assert [r.kind for r in result.rows] == ["expense", "income"]
        assert result.rows[0].date == date(2025, 2, 1)
        inverted = parse_rows(grid, mapping, ParseOptions(invert_sign=True))
        assert [r.kind for r in inverted.rows] == ["income", "expense"]

    def test_errors_are_reported_per_row(self):
        grid = [
            ["Date", "Amount"],
            ["nope", "1"],
            ["2025-01-01", "x"],
            ["2025-01-01", "0"],
        ]
        result = parse_rows(grid, ColumnMapping(date=0, amount=1), ParseOptions())
        assert [e.row for e in result.errors] == [2, 3, 4]
        assert result.rows == []

    def test_paired_transfers_bluecoins_style(self):
        grid = [
            ["Type", "Date", "Amount", "Account", "Category"],
            ["Transfer", "2025-01-01", "-100", "Bank", "(Transfer)"],
            ["Expense", "2025-01-01", "-5", "Bank", "Food"],
            ["Transfer", "2025-01-01", "100", "Cash", "(Transfer)"],
            ["Transfer", "2025-01-03", "-7", "Bank", "(Transfer)"],
        ]
        mapping = ColumnMapping(type=0, date=1, amount=2, account=3, category=4)
        result = parse_rows(grid, mapping, ParseOptions())
        transfers = [r for r in result.rows if r.kind == "transfer"]
        assert len(transfers) == 1
        assert (transfers[0].account, transfers[0].to_account) == ("Bank", "Cash")
        assert transfers[0].rows == [2, 4]
        assert [e.row for e in result.errors] == [5]

    def test_single_row_transfer_with_destination(self):
        grid = [
            ["Date", "Amount", "Type", "From", "To account"],
            ["2025-01-01", "30", "Traspaso", "A", "B"],
        ]
        mapping = ColumnMapping(date=0, amount=1, type=2, account=3, to_account=4)
        result = parse_rows(grid, mapping, ParseOptions())
        assert result.rows[0].kind == "transfer"
        assert (result.rows[0].account, result.rows[0].to_account) == ("A", "B")

    def test_category_separator(self):
        grid = [
            ["Date", "Amount", "Category"],
            ["2025-01-01", "-1", "Food > Groceries"],
        ]
        mapping = ColumnMapping(date=0, amount=1, category=2)
        row = parse_rows(grid, mapping, ParseOptions(category_separator=">")).rows[0]
        assert (row.category, row.subcategory) == ("Food", "Groceries")


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
class TestMatching:
    categories = [
        matching.CategoryEntity(
            id=1, name="Comida", is_income=False,
            subcategories=[matching.SubcategoryEntity(10, "Supermercado", 1), matching.SubcategoryEntity(11, "Restaurantes", 1)],
        ),
        matching.CategoryEntity(
            id=2, name="Ingresos", is_income=True,
            subcategories=[matching.SubcategoryEntity(20, "Salario", 2)],
        ),
    ]  # fmt: skip

    def test_exact_account_match_ignores_case_and_accents(self):
        entities = [matching.NamedEntity(1, "Débito BBVA")]
        suggestion = matching.suggest_named("debito bbva", entities, None)
        assert suggestion["action"] == "match" and suggestion["id"] == 1

    def test_unknown_account_suggests_create(self):
        suggestion = matching.suggest_named(
            "Nu", [matching.NamedEntity(1, "BBVA")], None
        )
        assert suggestion["action"] == "create"

    def test_category_and_subcategory(self):
        s = matching.suggest_category(
            "expense", "comida", "supermercado", self.categories, None
        )
        assert (s["action"], s["category_id"], s["subcategory_id"]) == ("match", 1, 10)

    def test_leaf_only_matches_subcategory(self):
        s = matching.suggest_category(
            "expense", "Restaurantes", "", self.categories, None
        )
        assert (s["category_id"], s["subcategory_id"]) == (1, 11)

    def test_unknown_child_of_known_parent_creates_subcategory(self):
        s = matching.suggest_category(
            "expense", "Comida", "Snacks", self.categories, None
        )
        assert s == {
            **s,
            "action": "create_subcategory",
            "category_id": 1,
            "name": "Snacks",
        }

    def test_income_never_matches_expense_category(self):
        s = matching.suggest_category(
            "income", "Supermercado", "", self.categories, None
        )
        assert s["action"] == "create_category"
        assert s["subcategory_name"] == "Supermercado"

    def test_profile_mapping_wins_when_still_valid(self):
        saved = {
            matching.category_key("expense", "food", ""): {
                "action": "match",
                "category_id": 1,
                "subcategory_id": 11,
            }
        }
        s = matching.suggest_category("expense", "Food", "", self.categories, saved)
        assert s["source"] == "profile" and s["subcategory_id"] == 11
        stale = {
            matching.category_key("expense", "food", ""): {
                "action": "match",
                "category_id": 999,
                "subcategory_id": None,
            }
        }
        assert (
            matching.suggest_category("expense", "Food", "", self.categories, stale)[
                "source"
            ]
            == "auto"
        )


def test_export_never_writes_user_text_as_formula():
    from app.services.user_export import _excel_value

    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet()
    cell = _excel_value(ws, "description", '=HYPERLINK("http://evil","x")')
    assert cell.data_type == "s"
    assert _excel_value(ws, "description", "plain") == "plain"
