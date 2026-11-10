"""Where an expense or income was created; stored in the ``made_from`` column."""

MADE_FROM_WEB = "Web"
MADE_FROM_WHATSAPP = "WhatsApp"
MADE_FROM_OCR = "OCR"
MADE_FROM_CHAT = "Chat"
MADE_FROM_RECURRING = "Recurring"
MADE_FROM_MCP = "MCP"

MADE_FROM_VALUES = (
    MADE_FROM_WEB,
    MADE_FROM_WHATSAPP,
    MADE_FROM_OCR,
    MADE_FROM_CHAT,
    MADE_FROM_RECURRING,
    MADE_FROM_MCP,
)


def validate_made_from(value: str | None) -> str | None:
    if value not in MADE_FROM_VALUES:
        raise ValueError(f"made_from must be one of: {', '.join(MADE_FROM_VALUES)}")
    return value
