"""Catalog of frontend components the chat agent can ask the UI to render.

The backend is the source of truth for what the client renders. Data tools emit
their matching components automatically; ``render_data`` is reserved for the
two generic, model-populated components.
"""

from __future__ import annotations

from app.schemas.chat import ChatAction, RenderDirective

# Each entry maps a frontend component id to a human-readable description (shown
# to the model) and the action types whose `detail` feeds the component.
RENDER_COMPONENTS: list[dict] = [
    {
        "id": "accounts_list",
        "description": "List of the user's accounts with their balances.",
        "source_action_types": ["accounts_listed"],
    },
    {
        "id": "categories_list",
        "description": "List of categories with their subcategories.",
        "source_action_types": ["categories_listed"],
    },
    {
        "id": "places_list",
        "description": "List of places (merchants/locations).",
        "source_action_types": ["places_listed"],
    },
    {
        "id": "transactions_list",
        "description": "Results of a transaction search (expenses, incomes, transfers).",
        "source_action_types": ["transactions_searched"],
    },
    {
        "id": "balance_adjustments_list",
        "description": "List of balance adjustments for one or all accounts.",
        "source_action_types": ["balance_adjustments_listed"],
    },
    {
        "id": "period_summary",
        "description": (
            "Aggregated financial summary for a period: total balance plus "
            "income, expenses, and net for the selected range."
        ),
        "source_action_types": ["summary_fetched"],
    },
    {
        "id": "charts",
        "description": "Charts visualizing spending/income over a period.",
        "source_action_types": ["charts_fetched"],
    },
    {
        "id": "web_sources",
        "description": (
            "Web search results with titles, URLs, and snippets. "
            "Backed by search_web results from this turn."
        ),
        "source_action_types": ["web_searched"],
    },
    {
        "id": "data_table",
        "description": (
            "Generic comparison or breakdown table. Provide data directly: "
            "{title?, columns: [string], rows: [[string|number]]}."
        ),
        "model_data": True,
    },
    {
        "id": "stat_cards",
        "description": (
            "Key figures as stat cards. Provide data directly: "
            "{items: [{label, value, sub?}]}."
        ),
        "model_data": True,
    },
]

RENDER_COMPONENT_IDS: list[str] = [c["id"] for c in RENDER_COMPONENTS]

# Reverse map: action type -> component id (for the deterministic fallback).
_ACTION_TYPE_TO_COMPONENT: dict[str, str] = {
    action_type: component["id"]
    for component in RENDER_COMPONENTS
    if not component.get("model_data")
    for action_type in component["source_action_types"]
}

_MODEL_DATA_COMPONENT_IDS: frozenset[str] = frozenset(
    c["id"] for c in RENDER_COMPONENTS if c.get("model_data")
)


def is_valid_component(component_id: str) -> bool:
    return component_id in RENDER_COMPONENT_IDS


def is_model_data_component(component_id: str) -> bool:
    return component_id in _MODEL_DATA_COMPONENT_IDS


def component_for_action_type(action_type: str) -> str | None:
    """Return the component id backed by a given action type, if any."""
    return _ACTION_TYPE_TO_COMPONENT.get(action_type)


def render_catalog_prompt() -> str:
    """Render the component catalog as a prompt block for the system prompt."""
    lines = []
    for component in RENDER_COMPONENTS:
        line = f"- {component['id']}: {component['description']}"
        if component.get("model_data"):
            line += " (provide `data` with render_data)"
        lines.append(line)
    return "\n".join(lines)


def resolve_render_detail(
    component_id: str,
    actions: list[ChatAction],
    *,
    from_action: str | None = None,
) -> RenderDirective | None:
    """Build a RenderDirective for a chosen component from the turn's actions.

    Pairs the component with the `detail` of a matching action produced earlier
    in the turn. ``from_action`` may pin the data to a specific action type;
    otherwise the component's own source action types are used. Returns ``None``
    when the component id is unknown or no backing action data exists.
    """
    if not is_valid_component(component_id):
        return None

    if is_model_data_component(component_id):
        return None

    component = next(c for c in RENDER_COMPONENTS if c["id"] == component_id)
    candidate_types = (
        [from_action] if from_action else component["source_action_types"]
    )

    # Prefer the most recent matching action in the turn.
    for action in reversed(actions):
        if action.type in candidate_types:
            return RenderDirective(component=component_id, detail=action.detail)

    return RenderDirective(component=component_id, detail=None)
