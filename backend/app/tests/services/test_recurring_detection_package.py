"""Shape of the `app.services.recurring_detection` package.

The service used to be one 1000+ line module. It is now a package of
cohesive modules that re-exports the old public API. Tests patch names in
the module that looks them up; the last check here keeps every such patch
pointed at a name the code actually reads, so none silently stops patching.
"""

import ast
import importlib
from pathlib import Path
from types import ModuleType

from app.services import recurring_detection as detection

PACKAGE = detection.__name__
PACKAGE_DIR = Path(detection.__file__).parent
APP_DIR = PACKAGE_DIR.parents[1]
TESTS_DIR = APP_DIR.parent / "tests"
MODULES = ("access", "history", "scan", "suggestions")

# The names the single-file module defined (not imported), as of 596738d.
PUBLIC_API = [
    "AI_MERCHANT_CHUNK",
    "AI_MERCHANT_MAX_CHUNKS",
    "Alias",
    "CANCEL_COOLDOWN",
    "Dismissal",
    "FREE_SCANS",
    "MAX_DISMISSALS_PER_USER",
    "NEWER_IN_GROUP_LIMIT",
    "RELEASE_TIMEOUT_S",
    "STALE_CLAIM",
    "ScanCooldownError",
    "ScanLimitError",
    "ScanResult",
    "ScanStatus",
    "Template",
    "Usage",
    "accept_suggestions",
    "check_access",
    "claim_scan",
    "detect",
    "dismiss_suggestions",
    "has_unlimited_scans",
    "last_result",
    "load_points",
    "release_scan",
    "resolve_merchants",
    "run_scan",
    "scan_status",
    "scans_remaining",
    "undo_dismissal",
    "wait_for_cleanup",
]


def _submodule(name: str) -> ModuleType:
    return importlib.import_module(f"{PACKAGE}.{name}")


def test_is_a_package_of_small_modules():
    assert not (APP_DIR / "services" / "recurring_detection.py").exists()
    assert (PACKAGE_DIR / "__init__.py").exists()
    files = sorted(p.name for p in PACKAGE_DIR.glob("*.py"))
    assert files == ["__init__.py", *(f"{m}.py" for m in MODULES)]
    for path in PACKAGE_DIR.glob("*.py"):
        assert len(path.read_text().splitlines()) <= 400, path


def test_public_api_is_unchanged():
    public = sorted(
        name
        for name in dir(detection)
        if not name.startswith("_")
        and not isinstance(getattr(detection, name), ModuleType)
    )
    assert public == sorted(PUBLIC_API)
    assert sorted(detection.__all__) == sorted(PUBLIC_API)
    # Re-exports, not copies: each is the object its module defines.
    for name in PUBLIC_API:
        owners = [m for m in MODULES if name in vars(_submodule(m))]
        assert any(
            getattr(_submodule(m), name) is getattr(detection, name) for m in owners
        )


def test_submodules_import_in_a_cycle_free_order():
    # access and history import nothing from the package; scan and
    # suggestions only from them.
    allowed = {
        "access": set(),
        "history": set(),
        "scan": {"access", "history"},
        "suggestions": {"history"},
    }
    for name in MODULES:
        tree = ast.parse((PACKAGE_DIR / f"{name}.py").read_text())
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }
        absolute = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.level == 0
            and (node.module or "").startswith(PACKAGE)
        }
        assert absolute == set(), name
        assert imported <= allowed[name], name


def _aliases(tree: ast.AST) -> dict[str, ModuleType]:
    """Local names bound to modules by the file's imports (any scope)."""
    out: dict[str, ModuleType] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                try:
                    module = importlib.import_module(f"{node.module}.{alias.name}")
                except ImportError:
                    continue
                out[alias.asname or alias.name] = module
    return out


def _resolve(expr: ast.AST, aliases: dict[str, ModuleType]) -> ModuleType | None:
    if isinstance(expr, ast.Name):
        return aliases.get(expr.id)
    if isinstance(expr, ast.Attribute):
        base = _resolve(expr.value, aliases)
        value = getattr(base, expr.attr, None) if base else None
        return value if isinstance(value, ModuleType) else None
    return None


def _looked_up_in_functions(module: ModuleType) -> set[str]:
    """Global names a module's functions read at call time."""
    tree = ast.parse(Path(module.__file__).read_text())
    return {
        sub.id
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
        for sub in ast.walk(fn)
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
    }


def _attribute_reads(source_paths: list[Path], target: ModuleType) -> set[str]:
    """`<alias>.<name>` reads, in these files, through an alias of `target`."""
    names = set()
    for path in source_paths:
        tree = ast.parse(path.read_text())
        bound = {k for k, v in _aliases(tree).items() if v is target}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in bound
            ):
                names.add(node.attr)
    return names


def _patches() -> list[tuple[Path, int, ast.AST, ModuleType, str]]:
    found = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        source = path.read_text()
        if "recurring_detection" not in source:
            continue
        tree = ast.parse(source)
        aliases = _aliases(tree)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and ast.unparse(node.func) == "monkeypatch.setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
            ):
                continue
            target = _resolve(node.args[0], aliases)
            if target is not None:
                found.append(
                    (path, node.lineno, node.args[0], target, node.args[1].value)
                )
    return found


def test_every_patch_targets_a_name_the_code_looks_up():
    package_modules = {_submodule(m) for m in MODULES}
    app_sources = [p for p in APP_DIR.rglob("*.py") if PACKAGE_DIR not in p.parents]
    package_sources = sorted(PACKAGE_DIR.glob("*.py"))
    checked = set()
    for path, line, expr, target, name in _patches():
        where = f"{path.name}:{line} patches {target.__name__}.{name}"
        assert hasattr(target, name), where
        if target is detection:
            # Only names callers read through the package, e.g. the
            # endpoints' `detection_service.run_scan`.
            assert name in _attribute_reads(app_sources, detection), where
        elif target in package_modules:
            assert name in _looked_up_in_functions(target), where
        elif (
            isinstance(expr, ast.Attribute)
            or target.__name__ == "app.ai.recurring_detection"
        ):
            # A module the package calls into (`ai.resolve_merchants`,
            # `recurring_service.create_from_transaction`).
            assert name in _attribute_reads(package_sources, target), where
        else:
            continue
        checked.add((target.__name__, name))
    # The patches the split had to move, so this check can't pass vacuously.
    assert {
        (f"{PACKAGE}.scan", "detect"),
        (f"{PACKAGE}.scan", "_finish_scan"),
        (f"{PACKAGE}.access", "_cleanup_session"),
        (f"{PACKAGE}.access", "async_session"),
        (f"{PACKAGE}.suggestions", "MAX_DISMISSALS_PER_USER"),
        (f"{PACKAGE}.suggestions", "NEWER_IN_GROUP_LIMIT"),
        (f"{PACKAGE}.suggestions", "_rollback"),
        (PACKAGE, "check_access"),
        (PACKAGE, "run_scan"),
        ("app.ai.recurring_detection", "resolve_merchants"),
    } <= checked
