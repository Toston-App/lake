import asyncio
from typing import Any, Optional

import httpx

from app.core.config import settings

WAHA_URL = settings.WAHA_URL
SESSION = settings.WAHA_SESSION
API_KEY = settings.WAHA_API_KEY

HEADERS = {"api_key": API_KEY}

async def send_message(
    chat_id: str, text: str
) -> dict[str, Any]:
    """
    General function to send WhatsApp messages

    Args:
        chat_id: Recipient's phone number
        text: Content of the message

    Returns:
        Response from WhatsApp API
    """
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{WAHA_URL}/api/sendText",
            headers=HEADERS,
            json={
                "session": SESSION,
                "chatId": chat_id,
                "text": text
            }
        )

        return res.json()

async def send_poll(
    chat_id: str,
    reply_to: Optional[str] = None,
    text: str = "",
    options: list[str] = ["Confirm", "Cancel"],
    multiple_answers: bool = False):
    """
    Send a poll to a WhatsApp chat
    Args:
        chat_id: Recipient's phone number
        reply_to: ID of the message to reply to
        text: Content of the poll
        options: List of options for the poll
        multiple_answers: Allow multiple answers
    """

    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{WAHA_URL}/api/sendPoll",
            headers=HEADERS,
            json={
                "session": SESSION,
                "chatId": chat_id,
                "replyTo": reply_to,
                "poll": {
                    "name": text,
                    "options": options,
                    "multipleAnswers": multiple_answers,
                }
            }
        )
        return res.status_code

async def react_to_message(
    message_id: str,
    emoji: str
) -> dict[str, Any]:
    """
    React to a specific message with an emoji

    Args:
        message_id: ID of the message to react to
        chatId: Chat ID
        emoji: Emoji to react with

    Returns:
        Response from WhatsApp API
    """
    async with httpx.AsyncClient() as client:
        res = await client.put(
            f"{WAHA_URL}/api/reaction",
            headers=HEADERS,
            json={
                "session": SESSION,
                "messageId": message_id,
                "reaction": emoji,
            }
        )
        return res.json()

async def send_seen(
    chat_id: str,
    message_id: str,
    participant: Optional[str] = None
):
    """
    Mark a message as seen

    Args:
        chat_id: Chat ID
        message_id: ID of the message to mark as seen

    Returns:
        Response from WhatsApp API
    """
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{WAHA_URL}/api/sendSeen",
            headers=HEADERS,
            json={
                "session": SESSION,
                "chatId": chat_id,
                "messageId": message_id,
                "participant": participant,
            }
        )
        print("🚀 ~ res:", res)
        return res.status_code

async def start_typing(chat_id: str) -> dict[str, Any]:
    """
    Start typing indicator in chat

    Args:
        chat_id: Chat ID

    Returns:
        Response from WhatsApp API
    """
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{WAHA_URL}/api/startTyping",
            headers=HEADERS,
            json={
                "session": SESSION,
                "chatId": chat_id,
            }
        )
        return res.json()

async def stop_typing(chat_id: str) -> dict[str, Any]:
    """
    Stop typing indicator in chat

    Args:
        chat_id: Chat ID

    Returns:
        Response from WhatsApp API
    """
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{WAHA_URL}/api/stopTyping",
            headers=HEADERS,
            json={
                "session": SESSION,
                "chatId": chat_id,
            }
        )
        return res.json()

async def typing(chat_id: str, seconds: float) -> None:
    """
    Show typing indicator for specified duration

    Args:
        chat_id: Chat ID
        seconds: Duration to show typing indicator
    """
    await start_typing(chat_id=chat_id)
    await asyncio.sleep(seconds)
    await stop_typing(chat_id=chat_id)
