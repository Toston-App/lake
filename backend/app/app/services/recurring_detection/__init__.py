"""Detect recurring series in a user's history and turn them into templates.

A scan loads the user's unlinked transactions, resolves each description to a
merchant (cached alias -> AI -> deterministic fallback), runs the pure
detection in `app.core.recurrence_detection`, drops what the user already has
or dismissed, optionally lets the AI classify what's left and returns ranked
suggestions. Only the last result is saved (`recurring_detection_usage`).

Access: paid tiers scan without limit. Free users get one scan that returns
suggestions; a scan that finds nothing doesn't use it up. A previous scan on
any tier counts, so a lapsed paid user who scanned has no free scan left.

Modules: `access` (who may scan, the scan slot), `history` (what a scan
reads, merchant resolution), `scan` (detection and running a scan) and
`suggestions` (accept / dismiss). This package only re-exports their public
names: patch a name in the module that looks it up, not here.
"""

from .access import (
    CANCEL_COOLDOWN,
    FREE_SCANS,
    RELEASE_TIMEOUT_S,
    STALE_CLAIM,
    ScanCooldownError,
    ScanLimitError,
    ScanStatus,
    Usage,
    check_access,
    claim_scan,
    has_unlimited_scans,
    release_scan,
    scan_status,
    scans_remaining,
    wait_for_cleanup,
)
from .history import (
    AI_MERCHANT_CHUNK,
    AI_MERCHANT_MAX_CHUNKS,
    Alias,
    Dismissal,
    Template,
    load_points,
    resolve_merchants,
)
from .scan import ScanResult, detect, last_result, run_scan
from .suggestions import (
    MAX_DISMISSALS_PER_USER,
    NEWER_IN_GROUP_LIMIT,
    accept_suggestions,
    dismiss_suggestions,
    undo_dismissal,
)

__all__ = [
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
