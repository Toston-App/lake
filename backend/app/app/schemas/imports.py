import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.models.account import AccountType
from app.models.imports import ImportService, ImportStatus
from app.schemas.import_mapping import ColumnMapping, ParseOptions, clean_cell

_HEX_COLOR = re.compile(r"^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$")


def _clean_name(value: str) -> str:
    value = clean_cell(value)
    if not value:
        raise ValueError("Name cannot be empty")
    return value


Name = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(_clean_name)]


# ---------------------------------------------------------------------------
# Import record
# ---------------------------------------------------------------------------
class ImportBase(BaseModel):
    service: ImportService | None = None
    file_content: str | None = None
    file_size: int | None = None
    total_transactions_imported: int | None = None
    expenses_imported: int | None = None
    incomes_imported: int | None = None
    accounts_created: int | None = None
    sites_created: int | None = None
    unmatched_categories: int | None = None
    total_rows_processed: int | None = None
    ended_at: datetime | None = None


class ImportCreate(ImportBase):
    pass


class ImportUpdate(ImportBase):
    pass


class ImportInDBBase(ImportBase):
    id: int
    owner_id: int

    class Config:
        from_attributes = True


class Import(ImportInDBBase):
    pass


class ImportInDB(ImportInDBBase):
    date: datetime | None = None


class DeletionResponse(BaseModel):
    message: str


class ImportSummary(BaseModel):
    """An import as shown in the history list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: ImportStatus
    filename: str | None = None
    created_at: datetime | None = None
    ended_at: datetime | None = None
    reverted_at: datetime | None = None
    total_rows_processed: int | None = None
    total_transactions_imported: int | None = None
    expenses_imported: int | None = None
    incomes_imported: int | None = None
    transfers_imported: int | None = None
    accounts_created: int | None = None
    sites_created: int | None = None
    categories_created: int | None = None
    subcategories_created: int | None = None
    duplicates_skipped: int | None = None
    rows_failed: int | None = None
    unmatched_categories: int | None = None


# ---------------------------------------------------------------------------
# Session (draft) flow
# ---------------------------------------------------------------------------
class SessionCreated(BaseModel):
    id: int
    filename: str
    file_type: Literal["csv", "xlsx"]
    headers: list[str]
    sample_rows: list[list[str]]
    total_rows: int
    mapping: ColumnMapping
    options: ParseOptions
    profile_id: int | None = None
    profile_name: str | None = None
    previously_imported_at: datetime | None = None
    expires_at: datetime
    truncated_cells: int = 0


class MappingIn(BaseModel):
    mapping: ColumnMapping
    options: ParseOptions = ParseOptions()


class RowErrorOut(BaseModel):
    row: int
    message: str


class PreviewRow(BaseModel):
    rows: list[int]
    kind: Literal["expense", "income", "transfer"]
    date: str
    amount: float
    description: str
    account: str
    to_account: str
    category: str
    subcategory: str
    place: str


class ValueEntry(BaseModel):
    key: str
    value: str
    rows: int
    suggestion: dict[str, Any]


class CategoryEntry(BaseModel):
    key: str
    kind: Literal["expense", "income"]
    category: str
    subcategory: str
    rows: int
    suggestion: dict[str, Any]


class MappingSummary(BaseModel):
    total_rows: int
    valid_rows: int
    error_rows: int
    ignored_rows: int
    expenses: int
    incomes: int
    transfers: int
    date_format: str
    first_date: str | None = None
    last_date: str | None = None
    rows_without_account: int


class MappingResult(BaseModel):
    summary: MappingSummary
    errors: list[RowErrorOut]
    accounts: list[ValueEntry]
    places: list[ValueEntry]
    categories: list[CategoryEntry]
    preview: list[PreviewRow]


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
class NoneDecision(BaseModel):
    action: Literal["none"]


class MatchEntityDecision(BaseModel):
    action: Literal["match"]
    id: int


class CreateAccountDecision(BaseModel):
    action: Literal["create"]
    name: Name
    type: AccountType = AccountType.MISCELLANEOUS
    color: str | None = None

    @field_validator("color")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        if value is not None and not _HEX_COLOR.match(value):
            raise ValueError("Color must be a hex color like #168FFF")
        return value


class CreatePlaceDecision(BaseModel):
    action: Literal["create"]
    name: Name
    is_online: bool = False


class MatchCategoryDecision(BaseModel):
    action: Literal["match"]
    category_id: int
    subcategory_id: int | None = None


class CreateSubcategoryDecision(BaseModel):
    """Create a new subcategory inside one of the user's categories."""

    action: Literal["create_subcategory"]
    category_id: int
    name: Name


class CreateCategoryDecision(BaseModel):
    """Create a new category, optionally with a first subcategory."""

    action: Literal["create_category"]
    name: Name
    color: str = "#168FFF"
    icon: str | None = Field(default=None, max_length=64)
    subcategory_name: str | None = Field(default=None, max_length=100)

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        if not _HEX_COLOR.match(value):
            raise ValueError("Color must be a hex color like #168FFF")
        return value

    @field_validator("subcategory_name")
    @classmethod
    def _sub(cls, value: str | None) -> str | None:
        if value is None:
            return None
            return clean_cell(value) or None


AccountDecision = Annotated[
    NoneDecision | MatchEntityDecision | CreateAccountDecision,
    Field(discriminator="action"),
]
PlaceDecision = Annotated[
    NoneDecision | MatchEntityDecision | CreatePlaceDecision,
    Field(discriminator="action"),
]
CategoryDecision = Annotated[
    NoneDecision
    | MatchCategoryDecision
    | CreateSubcategoryDecision
    | CreateCategoryDecision,
    Field(discriminator="action"),
]


class Decisions(BaseModel):
    accounts: dict[str, AccountDecision] = Field(default_factory=dict)
    places: dict[str, PlaceDecision] = Field(default_factory=dict)
    categories: dict[str, CategoryDecision] = Field(default_factory=dict)
    skip_duplicates: bool = True
    save_profile: bool = False
    profile_name: str | None = Field(default=None, max_length=120)


class CommitPreview(BaseModel):
    expenses: int
    incomes: int
    transfers: int
    duplicates: int
    skipped: int
    accounts_to_create: int
    places_to_create: int
    categories_to_create: int
    subcategories_to_create: int
    problems: list[RowErrorOut]


class CommitResult(BaseModel):
    import_id: int
    message: str
    total_rows_processed: int
    total_transactions_imported: int
    expenses_imported: int
    incomes_imported: int
    transfers_imported: int
    accounts_created: int
    sites_created: int
    categories_created: int
    subcategories_created: int
    duplicates_skipped: int
    rows_failed: int
    unmatched_categories: int


class ImportProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    headers: list[str]
    created_at: datetime | None = None
    last_used_at: datetime | None = None
