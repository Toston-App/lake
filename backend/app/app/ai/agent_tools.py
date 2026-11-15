"""The chat agent's tools, generated from the shared registry (``tool_specs``).

Each tool validates its arguments with the model in ``TOOL_ARG_MODELS``, fills
a missing date with the user's local date, stages the call for approval when
``requires_confirmation`` says so, and otherwise runs the shared handler
through ``dispatch.execute_tool``, exactly as the MCP server does.

Chat-specific presentation lives here: a read whose spec names a ``ui_chunk``
also returns that UI data part when the model asks for ``user_visible``; the
model gets a compact view for long results (``_MODEL_VIEWS``). Tools without
UI data have ``presentation`` removed from their schema (``hide_presentation``)
so the model is never told it can show something that won't appear.

The agent passes its own ``defer`` (``agent._defer_for_confirmation``), so this
module doesn't import ``agent``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from app.ai.confirmation import ToolExecutionError, requires_confirmation
from app.ai.dispatch import execute_tool
from app.ai.tool_specs import ToolSpec, apply_date_defaults, enabled_specs
from app.ai.tools import TOOL_ARG_MODELS

Defer = Callable[[RunContext[Any], str, dict[str, Any], str], Awaitable[ToolReturn]]

_PAGING_KEYS = ("page", "has_more", "next_page")
_LIST_TOOLS = frozenset(
    {"list_accounts", "list_categories", "list_places", "list_balance_adjustments"}
)


def transaction_model_reference(item: dict[str, Any]) -> dict[str, Any]:
    """Compact private context for resolving transaction follow-ups."""
    reference = {
        key: item.get(key) for key in ("id", "type", "amount", "date", "description")
    }
    for relationship in ("account", "category", "subcategory", "place"):
        value = item.get(relationship)
        if isinstance(value, dict) and value.get("name"):
            reference[f"{relationship}_name"] = value["name"]
    return reference


def _search_view(result: dict[str, Any]) -> tuple[Any, Any]:
    data = result["data"]
    items = data["items"]
    model = {
        "summary": (
            f"Found {data['total']} transactions; returning page "
            f"{data['page']} with {len(items)} items."
        ),
        **data,
        "items": [transaction_model_reference(item) for item in items],
    }
    return model, data


def _list_view(result: dict[str, Any]) -> tuple[Any, Any]:
    items = result["data"]
    model = {"items": items, "total": len(items)}
    model.update({k: result[k] for k in _PAGING_KEYS if k in result})
    return model, {"items": items}


def _default_view(result: dict[str, Any]) -> tuple[Any, Any]:
    data = result["data"]
    paging = {k: result[k] for k in _PAGING_KEYS if k in result}
    return ({"data": data, **paging} if paging else data), data


# tool -> (what the model reads, the UI data part's payload)
_MODEL_VIEWS: dict[str, Callable[[dict[str, Any]], tuple[Any, Any]]] = {
    "search_transactions": _search_view,
    **dict.fromkeys(_LIST_TOOLS, _list_view),
}


@dataclass(frozen=True)
class AgentTool:
    """A generated tool and how to register it (``FunctionToolset.add_function``)."""

    function: Callable[..., Awaitable[Any]]
    name: str
    description: str
    # Removes ``presentation`` from the schema when the tool has no UI data.
    prepare: Callable[..., Awaitable[ToolDefinition]] | None


async def hide_presentation(
    _ctx: RunContext[Any], tool_def: ToolDefinition
) -> ToolDefinition:
    """Drop ``presentation`` from the schema the model sees."""
    schema = dict(tool_def.parameters_json_schema)
    properties = dict(schema.get("properties") or {})
    if "presentation" not in properties:
        return tool_def
    properties.pop("presentation")
    schema["properties"] = properties
    return replace(tool_def, parameters_json_schema=schema)


async def _run(
    spec: ToolSpec, ctx: RunContext[Any], payload: Any, defer: Defer
) -> ToolReturn:
    # Unset, not None: an explicit null (e.g. clearing an end date) is kept.
    args = payload.model_dump(mode="json", exclude_unset=True) if payload else {}
    args = apply_date_defaults(
        spec, args, ctx.deps.local_date, getattr(ctx.deps, "time_zone", None)
    )
    if requires_confirmation(spec.name, args):
        return await defer(ctx, spec.name, args, payload.response_language)
    result, _ = await execute_tool(spec.name, args, ctx.deps.db, ctx.deps.user)
    if not result.get("success"):
        # The agent's tool hook turns this into an error the model sees.
        raise ToolExecutionError(result.get("error") or "The tool failed")
    model_value, ui_data = _MODEL_VIEWS.get(spec.name, _default_view)(result)
    show = spec.ui_chunk and getattr(payload, "presentation", None) == "user_visible"
    return ToolReturn(
        return_value=model_value,
        metadata=[DataChunk(type=spec.ui_chunk, data=ui_data)] if show else [],
    )


def _build(spec: ToolSpec, defer: Defer) -> Callable[..., Awaitable[Any]]:
    model = TOOL_ARG_MODELS.get(spec.name)
    if model is None:

        async def no_args_tool(ctx: RunContext[Any]) -> ToolReturn:
            return await _run(spec, ctx, None, defer)

        no_args_tool.__annotations__ = {"ctx": RunContext[Any], "return": ToolReturn}
        return no_args_tool

    async def tool(ctx: RunContext[Any], payload: Any) -> ToolReturn:
        return await _run(spec, ctx, payload, defer)

    # pydantic-ai reads the argument schema from the annotations.
    tool.__annotations__ = {
        "ctx": RunContext[Any],
        "payload": model,
        "return": ToolReturn,
    }
    return tool


def agent_tools(defer: Defer) -> list[AgentTool]:
    """The registry tools the chat offers now (feature flags applied)."""
    return [
        AgentTool(
            function=_build(spec, defer),
            name=spec.name,
            description=spec.description,
            prepare=None if spec.ui_chunk else hide_presentation,
        )
        for spec in enabled_specs()
    ]
