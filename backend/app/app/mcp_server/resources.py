"""MCP resources and prompts: context a client can attach without a tool call.

Resources are read-only snapshots of the user's reference data (profile,
accounts, categories, places, recurring series), produced by the same tools
the server exposes. Prompts are the skill's main workflows as ready-made
messages, so clients without the Toston skill still get them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import mcp.types as types

RESOURCE_SCHEME = "toston"


@dataclass(frozen=True)
class McpResource:
    key: str
    name: str
    description: str
    # The read-only tool whose result the resource holds.
    tool_name: str

    @property
    def uri(self) -> str:
        return f"{RESOURCE_SCHEME}://{self.key}"


RESOURCES: dict[str, McpResource] = {
    resource.uri: resource
    for resource in (
        McpResource(
            "profile",
            "Profile",
            "The user's name, locale, display currency and default account.",
            "get_user_profile",
        ),
        McpResource(
            "accounts",
            "Accounts",
            "The user's accounts with their balances (first page).",
            "list_accounts",
        ),
        McpResource(
            "categories",
            "Categories",
            "The user's categories and subcategories (first page).",
            "list_categories",
        ),
        McpResource(
            "places",
            "Places",
            "The user's saved places, i.e. merchants (first page).",
            "list_places",
        ),
        McpResource(
            "recurring",
            "Recurring series",
            "Subscriptions, bills, salary and other recurring series.",
            "list_recurring",
        ),
    )
}


def list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            uri=resource.uri,
            name=resource.name,
            description=resource.description,
            mimeType="application/json",
        )
        for resource in RESOURCES.values()
    ]


# -- prompts --------------------------------------------------------------------


@dataclass(frozen=True)
class McpPrompt:
    name: str
    title: str
    description: str
    arguments: tuple[types.PromptArgument, ...]
    template: str

    def render(self, arguments: dict[str, str] | None) -> str:
        values = {arg.name: "" for arg in self.arguments}
        values.update({k: v for k, v in (arguments or {}).items() if k in values})
        return self.template.format(**values).strip()


_MONTH = types.PromptArgument(
    name="month",
    description="The month to review as YYYY-MM. Defaults to the last full month.",
    required=False,
)

PROMPTS: dict[str, McpPrompt] = {
    prompt.name: prompt
    for prompt in (
        McpPrompt(
            "monthly_review",
            "Monthly review",
            "Review a month of spending against the month before.",
            (_MONTH,),
            """
Review my finances in Toston for {month} (if no month is given, use the last full month).
1. Call compare_periods with date_filter_type "month" for that month.
2. Report income, expenses and net with their change, then the categories that moved most.
3. Point out anything unusual, such as a big jump or a new large merchant.
4. Offer to recategorize anything that looks wrong. Updates need my approval.
""",
        ),
        McpPrompt(
            "log_receipt",
            "Log a receipt or statement",
            "Turn a receipt or statement into Toston transactions.",
            (),
            """
Log the receipt or statement I share in Toston.
1. Read the lines yourself. Resolve accounts, categories and places with list_accounts, list_categories and list_places; never invent ids.
2. Show me a table (date, description, amount, category) and let me correct it.
3. Send bulk_create_transactions in chunks of at most 100 items. It returns a preview: show it, and call confirm_action only after I agree.
""",
        ),
        McpPrompt(
            "subscriptions_review",
            "Subscriptions and bills",
            "List recurring series and find ones not set up yet.",
            (),
            """
Review my subscriptions and recurring bills in Toston.
1. Call list_recurring and show each series with its amount and next date.
2. Ask before calling detect_recurring to find series I haven't set up; it needs my approval and may use my plan's only scan.
3. For suggestions I want, call create_recurring_from_transaction. Pausing, skipping or deleting a series needs my approval too.
""",
        ),
        McpPrompt(
            "find_and_fix",
            "Find and fix transactions",
            "Search transactions and correct their details.",
            (
                types.PromptArgument(
                    name="what",
                    description="What to look for, e.g. 'coffee in March'.",
                    required=False,
                ),
            ),
            """
Find and fix transactions in Toston: {what}
1. Call search_transactions with filters; page with page while has_more is true.
2. Propose the fixes in a table. Fix with update_expense or update_income, or convert_transaction for the wrong type.
3. Each change returns a preview: show them together and confirm each only after I agree.
""",
        ),
    )
}


def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name=prompt.name,
            title=prompt.title,
            description=prompt.description,
            arguments=list(prompt.arguments),
        )
        for prompt in PROMPTS.values()
    ]


def get_prompt(name: str, arguments: dict[str, Any] | None) -> types.GetPromptResult:
    prompt = PROMPTS.get(name)
    if prompt is None:
        raise ValueError(f"Unknown prompt: {name}")
    return types.GetPromptResult(
        description=prompt.description,
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=prompt.render(arguments)),
            )
        ],
    )
