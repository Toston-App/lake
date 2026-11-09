"""Structure of the recurring services after splitting `services/recurring.py`."""

import ast
import importlib
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from app.core.recurrence import BusinessDayAdjustment, Frequency, MonthlyMode, Rule
from app.models import RecurringTransaction
from app.services import recurring as recurring_service

from tests.services.test_recurring import LOCAL_TODAY_MODULES

APP_DIR = Path(recurring_service.__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parents[1]

SPLIT_MODULES = (
    "recurring",
    "recurring_fields",
    "recurring_generation",
    "recurring_leases",
    "recurring_schedule",
)
# Which split modules each one may import; anything else would be a cycle or
# unwanted coupling.
ALLOWED_IMPORTS = {
    "recurring_leases": set(),
    "recurring_schedule": set(),
    "recurring_fields": {"recurring_schedule"},
    "recurring_generation": {
        "recurring_fields",
        "recurring_leases",
        "recurring_schedule",
    },
    "recurring": {
        "recurring_fields",
        "recurring_generation",
        "recurring_leases",
        "recurring_schedule",
    },
}

# Public names of `services/recurring.py` before the split; all still importable
# from it. `rule_from` is the only addition.
PUBLIC_NAMES = {
    "InvalidTimezoneError",
    "LEASE",
    "LEASE_POLL_SECONDS",
    "LEASE_WAIT_SECONDS",
    "LIMIT_FIELDS",
    "MAX_FUTURE_YEARS",
    "MAX_OCCURRENCES_PER_RUN",
    "MAX_PAST_YEARS",
    "MAX_TEMPLATES_PER_USER",
    "NON_NULLABLE_FIELDS",
    "RecurringTransaction",
    "SCHEDULE_FIELDS",
    "UPCOMING_PREVIEW",
    "claim_due",
    "create_from_transaction",
    "create_recurring",
    "delete_recurring",
    "earliest_start",
    "generate_due",
    "get_recurring",
    "list_recurring",
    "local_today",
    "logger",
    "pause_for_deleted",
    "postgres_timezones",
    "preview",
    "release_leases",
    "report_unknown_timezones",
    "rule_for",
    "skip_next",
    "to_detail",
    "upcoming_for",
    "update_recurring",
}
NEW_PUBLIC_NAMES = {"rule_from"}
# Private names other code imports from `services/recurring.py`.
PRIVATE_REEXPORTS = {"_shift_years"}

MODULE_ALIASES = {
    "recurring_service": "recurring",
    "recurring_fields": "recurring_fields",
    "recurring_generation": "recurring_generation",
    "recurring_leases": "recurring_leases",
    "recurring_schedule": "recurring_schedule",
}


def _path(module: str) -> Path:
    return APP_DIR / "services" / f"{module}.py"


def _tree(module: str) -> ast.Module:
    return ast.parse(_path(module).read_text())


def _imported_split_modules(module: str) -> set[str]:
    found = set()
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.ImportFrom) and node.module:
            name = node.module.rsplit(".", 1)[-1]
            if node.module.startswith("app.services.") and name in SPLIT_MODULES:
                found.add(name)
    return found


def _top_level_definitions(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _loaded_names(tree: ast.Module) -> set[str]:
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


@pytest.mark.parametrize("module", SPLIT_MODULES)
def test_split_modules_stay_small(module: str):
    assert len(_path(module).read_text().splitlines()) < 450


@pytest.mark.parametrize("module", SPLIT_MODULES)
def test_split_modules_import_only_their_dependencies(module: str):
    assert _imported_split_modules(module) <= ALLOWED_IMPORTS[module]


@pytest.mark.parametrize("module", SPLIT_MODULES)
def test_split_modules_import_on_their_own(module: str):
    # A fresh interpreter, so an import cycle can't hide behind modules other
    # tests already loaded.
    subprocess.run(
        [sys.executable, "-c", f"import app.services.{module}"],
        cwd=APP_DIR.parent,
        check=True,
    )


def test_worker_imports_only_the_generation_module():
    tree = ast.parse((APP_DIR / "worker" / "recurring_worker.py").read_text())
    services = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("app.services")
    }
    services |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name.startswith("app.services")
    }
    assert services == {"app.services.recurring_generation"}


def test_public_names_are_unchanged():
    public = {name for name in vars(recurring_service) if not name.startswith("_")}
    tree = _tree("recurring")
    defined_or_reexported = _top_level_definitions(tree) | {
        alias.asname
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname
    }
    assert {n for n in defined_or_reexported if not n.startswith("_")} == (
        PUBLIC_NAMES | NEW_PUBLIC_NAMES
    )
    assert PUBLIC_NAMES | NEW_PUBLIC_NAMES <= public
    assert PRIVATE_REEXPORTS <= set(vars(recurring_service))


@pytest.mark.parametrize("name", sorted(PUBLIC_NAMES | NEW_PUBLIC_NAMES))
def test_reexported_names_are_the_originals(name: str):
    value = getattr(recurring_service, name)
    homes = [
        module
        for module in SPLIT_MODULES
        if name in _top_level_definitions(_tree(module))
    ]
    assert homes, name
    for home in homes:
        assert getattr(importlib.import_module(f"app.services.{home}"), name) is value


def test_local_today_is_patched_wherever_it_is_looked_up():
    looked_up = {
        module
        for module in SPLIT_MODULES
        if "local_today" in _loaded_names(_tree(module))
    }
    patched = {module.__name__.rsplit(".", 1)[-1] for module in LOCAL_TODAY_MODULES}
    assert looked_up == patched


def _patched_split_names() -> list[tuple[str, str, str]]:
    """(test file, module, name) of every monkeypatch of a split module."""
    patches = []
    for path in TESTS_DIR.rglob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in MODULE_ALIASES
                and isinstance(node.args[1], ast.Constant)
            ):
                module = MODULE_ALIASES[node.args[0].id]
                patches.append((path.name, module, node.args[1].value))
    return patches


def test_patches_target_the_module_that_looks_the_name_up():
    """A name a module merely imports must be looked up there to be patched there.

    Patching a re-exported name whose lookups moved to another module would
    silently stop affecting the code under test.
    """
    patches = _patched_split_names()
    assert patches
    for test_file, module, name in patches:
        tree = _tree(module)
        if name in _top_level_definitions(tree):
            continue
        assert name in _loaded_names(tree), (test_file, module, name)


def _template(**fields) -> RecurringTransaction:
    return RecurringTransaction(start_date=date(2031, 1, 31), **fields)


def test_rule_for_template_defaults():
    assert recurring_service.rule_for(_template(frequency="monthly")) == Rule(
        frequency=Frequency.monthly,
        start_date=date(2031, 1, 31),
        interval=1,
        weekdays=(),
        monthly_mode=MonthlyMode.day_of_month,
        month_days=(),
        week_of_month=None,
        adjustment=BusinessDayAdjustment.none,
        end_date=None,
    )


SCHEDULES = [
    {"frequency": "daily"},
    {"frequency": "weekly", "interval": 2, "weekdays": [0, 4]},
    {
        "frequency": "monthly",
        "monthly_mode": "nth_weekday",
        "weekdays": [1],
        "week_of_month": -1,
        "business_day_adjustment": "previous",
        "end_date": date(2032, 6, 30),
    },
    {"frequency": "semimonthly", "month_days": [1, 15]},
    {
        "frequency": Frequency.monthly,
        "month_days": [31],
        "monthly_mode": MonthlyMode.day_of_month,
        "business_day_adjustment": BusinessDayAdjustment.next,
        "interval": None,
        "weekdays": None,
    },
]


@pytest.mark.parametrize("schedule", SCHEDULES)
def test_rule_from_template_and_fields_agree(schedule: dict):
    fields = {"start_date": date(2031, 1, 31), **schedule}
    from_template = recurring_service.rule_from(_template(**schedule))
    from_fields = recurring_service.rule_from(fields)
    assert from_template == from_fields
    assert recurring_service.rule_for(_template(**schedule)) == from_template
    for rule in (from_template, from_fields):
        assert type(rule.frequency) is Frequency
        assert type(rule.monthly_mode) is MonthlyMode
        assert type(rule.adjustment) is BusinessDayAdjustment
        assert isinstance(rule.weekdays, tuple)
        assert isinstance(rule.month_days, tuple)
