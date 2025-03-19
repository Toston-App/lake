from typing import Any

import httpx

from app.core.config import settings


def format_currency(
    amount: float,
    currency_symbol: str = "$",
    decimal_places: int = 2,
    thousands_sep: str = ",",
    decimal_point: str = ".",
    symbol_position: str = "prefix",
    add_space: bool = False
) -> str:
    """
    Format a number as a currency string with customizable formatting

    Args:
        amount: The amount to format
        currency_symbol: The currency symbol (default: $)
        decimal_places: Number of decimal places (default: 2)
        thousands_sep: Character to use as thousands separator (default: ,)
        decimal_point: Character to use as decimal point (default: .)
        symbol_position: Whether the currency symbol should be a 'prefix' or 'suffix' (default: prefix)
        add_space: Whether to add a space between the number and symbol (default: False)

    Returns:
        Formatted currency string

    Examples:
        >>> format_currency(1234.56)
        '$1,234.56'
        >>> format_currency(1234.56, currency_symbol="€", symbol_position="suffix")
        '1,234.56€'
        >>> format_currency(1234.56, thousands_sep=".", decimal_point=",")
        '$1.234,56'
        >>> format_currency(-1234.56)
        '-$1,234.56'
    """
    # Format with Python's built-in formatting
    formatted = f"{abs(amount):,.{decimal_places}f}"

    # Replace the default separators with the specified ones if different
    if thousands_sep != "," or decimal_point != ".":
        formatted = formatted.replace(",", "TEMP").replace(".", decimal_point).replace("TEMP", thousands_sep)

    # Add the currency symbol in the correct position
    space = " " if add_space else ""
    if symbol_position.lower() == "suffix":
        result = f"{formatted}{space}{currency_symbol}"
    else:  # default to prefix
        result = f"{currency_symbol}{space}{formatted}"

    # Add negative sign if needed
    if amount < 0:
        result = f"-{result}"

    return result


async def send_whatsapp_message(
    phone_number: str,
    message_type: str,
    message_content: dict,
) -> dict[str, Any]:
    """
    Send a WhatsApp message to a user

    Args:
        phone_number: The recipient's phone number
        message_type: Type of message ("text", "reaction", etc.)
        message_content: Content specific to the message type

    Returns:
        Dictionary with status and API response
    """
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_number,
        "type": message_type,
        message_type: message_content
    }
    print("🚀 ~ payload:", payload)

    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"
            },
            json=payload
        )

        print(res.json(), "here")

        if res.status_code != 200:
            print("rip", res.json())
            return {"status": "error", "response": res.json()}

        return {"status": "success", "response": res.json()}


async def send_text_message(
    phone_number: str, message: str = "", preview_url: bool = False
) -> dict[str, Any]:
    """
    Send a text message via WhatsApp

    Args:
        phone_number: Recipient's phone number
        message: Text message content
        preview_url: Whether to generate URL previews in the message

    Returns:
        Response from WhatsApp API
    """
    message_content = {
        "preview_url": preview_url,
        "body": message
    }

    return await send_whatsapp_message(phone_number, "text", message_content)


async def send_reaction(
    phone_number: str, message_id: str, emoji: str
) -> dict[str, Any]:
    """
    React to a message with an emoji

    Args:
        phone_number: Recipient's phone number
        message_id: ID of the message to react to
        emoji: Emoji character to send as reaction

    Returns:
        Response from WhatsApp API
    """
    message_content = {
        "message_id": message_id,
        "emoji": emoji
    }

    return await send_whatsapp_message(phone_number, "reaction", message_content)

async def send_interactive(
    phone_number: str, text: str, buttons: list[dict[str, str]]
) -> dict[str, Any]:
    """
    Send a message with interactive buttons

    Args:
        phone_number: Recipient's phone number
        text: Text message content
        buttons: List of button objects

    Returns:
        Response from WhatsApp API
    """
    message_content = {
        "body": {
            "text": text
        },
        "type": "button",
        "action": {
            "buttons": [
                {
                    "type": "reply",
                    "reply": {
                        "id": button["id"],
                        "title": button["title"]
                    }
                } for button in buttons
            ]
        }
    }

    return await send_whatsapp_message(phone_number, "interactive", message_content)
