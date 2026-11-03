"""Safe reading of uploaded CSV / XLSX files into a grid of strings.

The upload is never written to permanent storage, served back or executed.
Every check here treats the bytes as hostile: we look at the real content
(not the extension or the browser's content type), refuse anything that looks
like a binary/executable, bound memory and CPU use, and only ever produce
plain strings.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass

from app.schemas.import_mapping import clean_cell

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20_000
MAX_COLUMNS = 50
MAX_CELL_CHARS = 1_000
# XLSX are zip archives: guard against zip bombs before openpyxl touches them.
MAX_XLSX_UNCOMPRESSED_BYTES = 60 * 1024 * 1024
MAX_XLSX_ENTRIES = 2_000
MAX_XLSX_COMPRESSION_RATIO = 100

CSV_EXTENSIONS = (".csv", ".tsv", ".txt")
XLSX_EXTENSIONS = (".xlsx",)

# Signatures of files that are never a spreadsheet export.
_BINARY_SIGNATURES: tuple[bytes, ...] = (
    b"MZ",  # Windows PE / DOS executables
    b"\x7fELF",  # Linux executables
    b"\xca\xfe\xba\xbe",  # Mach-O universal / Java class
    b"\xcf\xfa\xed\xfe",  # Mach-O 64
    b"\xce\xfa\xed\xfe",  # Mach-O 32
    b"%PDF",
    b"\x89PNG",
    b"\xff\xd8\xff",  # JPEG
    b"GIF8",
    b"Rar!",
    b"7z\xbc\xaf",
    b"\x1f\x8b",  # gzip
    b"\xd0\xcf\x11\xe0",  # OLE2 (legacy .xls / .doc with macros)
    b"#!",  # scripts with a shebang
)
_ZIP_SIGNATURE = b"PK\x03\x04"
_TEXT_ENCODINGS = ("utf-8-sig", "utf-16", "cp1252", "latin-1")


class UnsafeFileError(ValueError):
    """The upload was rejected. The message is safe to show to the user."""


@dataclass
class ReadResult:
    rows: list[list[str]]
    file_type: str  # "csv" | "xlsx"
    encoding: str | None
    delimiter: str | None
    truncated_cells: int


def _check_extension(filename: str) -> str:
    lower = (filename or "").lower().strip()
    if lower.endswith(XLSX_EXTENSIONS):
        return "xlsx"
    if lower.endswith(CSV_EXTENSIONS):
        return "csv"
    raise UnsafeFileError("Only .csv and .xlsx files are supported.")


def _decode_text(data: bytes) -> tuple[str, str]:
    if b"\x00" in data and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise UnsafeFileError("The file contains binary data and is not a valid CSV.")
    for encoding in _TEXT_ENCODINGS:
        if encoding == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            raise UnsafeFileError(
                "The file contains binary data and is not a valid CSV."
            )
        return text, encoding
    raise UnsafeFileError("The file encoding is not supported.")


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        first_line = sample.splitlines()[0] if sample else ""
        counts = {d: first_line.count(d) for d in (",", ";", "\t", "|")}
        best = max(counts, key=lambda d: counts[d])
        return best if counts[best] else ","


class _Grid:
    def __init__(self) -> None:
        self.rows: list[list[str]] = []
        self.truncated_cells = 0

    def add(self, raw_row: list[object]) -> None:
        if len(raw_row) > MAX_COLUMNS:
            # Trailing empty cells are common in spreadsheet exports.
            while len(raw_row) > MAX_COLUMNS and raw_row[-1] in (None, ""):
                raw_row = raw_row[:-1]
            if len(raw_row) > MAX_COLUMNS:
                raise UnsafeFileError(f"The file has more than {MAX_COLUMNS} columns.")
        row: list[str] = []
        for cell in raw_row:
            text = clean_cell(cell)
            if len(text) > MAX_CELL_CHARS:
                text = text[:MAX_CELL_CHARS]
                self.truncated_cells += 1
            row.append(text)
        if not any(row):
            return
        # +1 for the header row.
        if len(self.rows) >= MAX_ROWS + 1:
            raise UnsafeFileError(f"The file has more than {MAX_ROWS} rows.")
        self.rows.append(row)


def _read_csv(data: bytes) -> ReadResult:
    text, encoding = _decode_text(data)
    delimiter = _sniff_delimiter(text[:20_000])
    grid = _Grid()
    # csv's default field_size_limit (128 KiB) already bounds a single cell;
    # longer cells are rejected by the reader, shorter ones truncated by _Grid.
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        for raw_row in reader:
            grid.add(list(raw_row))
    except csv.Error as exc:
        raise UnsafeFileError(f"The CSV file is malformed: {exc}") from exc
    return ReadResult(grid.rows, "csv", encoding, delimiter, grid.truncated_cells)


def _check_zip(data: bytes) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnsafeFileError("The .xlsx file is corrupted.") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_XLSX_ENTRIES:
            raise UnsafeFileError("The .xlsx file is not a valid spreadsheet.")
        names = {entry.filename for entry in entries}
        if "xl/workbook.xml" not in names:
            raise UnsafeFileError("The .xlsx file is not a valid spreadsheet.")
        if any(name.lower().endswith("vbaproject.bin") for name in names):
            raise UnsafeFileError("Spreadsheets with macros are not allowed.")
        total = 0
        for entry in entries:
            if entry.flag_bits & 0x1:
                raise UnsafeFileError("Encrypted spreadsheets are not supported.")
            total += entry.file_size
            ratio = entry.file_size / max(entry.compress_size, 1)
            if (
                total > MAX_XLSX_UNCOMPRESSED_BYTES
                or ratio > MAX_XLSX_COMPRESSION_RATIO
            ):
                raise UnsafeFileError("The .xlsx file is too large once unpacked.")


def _read_xlsx(data: bytes) -> ReadResult:
    _check_zip(data)
    # Imported lazily: openpyxl uses defusedxml automatically when installed,
    # which blocks XML entity expansion / external entity attacks.
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(data), read_only=True, data_only=True, keep_links=False
        )
    except Exception as exc:  # openpyxl raises many different error types
        raise UnsafeFileError("The .xlsx file could not be read.") from exc
    grid = _Grid()
    try:
        sheet = next(
            (ws for ws in workbook.worksheets if ws.max_row and ws.max_row > 0),
            workbook.active,
        )
        if sheet is None:
            raise UnsafeFileError("The spreadsheet is empty.")
        # data_only=True returns cached values, never formulas.
        for raw_row in sheet.iter_rows(values_only=True):
            grid.add([_xlsx_cell(value) for value in raw_row])
    finally:
        workbook.close()
    return ReadResult(grid.rows, "xlsx", None, None, grid.truncated_cells)


def _xlsx_cell(value: object) -> object:
    from datetime import date, datetime

    if isinstance(value, datetime):
        if value.time() == datetime.min.time():
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return value


def read_upload(filename: str, data: bytes) -> ReadResult:
    """Validate and parse an upload. Raises UnsafeFileError when rejected."""
    if not data:
        raise UnsafeFileError("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise UnsafeFileError("The file is larger than 10 MB.")

    declared = _check_extension(filename)
    is_zip = data.startswith(_ZIP_SIGNATURE)
    if declared == "xlsx":
        if not is_zip:
            raise UnsafeFileError("The file is not a valid .xlsx spreadsheet.")
        result = _read_xlsx(data)
    else:
        if is_zip or data.startswith(_BINARY_SIGNATURES):
            raise UnsafeFileError("The file is not a valid CSV.")
        result = _read_csv(data)

    if len(result.rows) < 2:
        raise UnsafeFileError("The file needs a header row and at least one row.")
    return result
