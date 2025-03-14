from typing import Any

import httpx

from app.core.config import settings


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
