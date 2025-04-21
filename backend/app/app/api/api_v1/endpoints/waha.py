import random

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.api import deps
from app.utilities.encryption import hash_sha256
from app.utilities.waha import react_to_message, send_message, send_seen, typing

# we need a cloudflare tunnel from 9000 and 3000
WAHA_URL = "https://springer-along-peers-stockholm.trycloudflare.com"
SESSION = "default"
# TODO: CHANGE THIS (and use env vars)
API_KEY = "admin"

HEADERS = {"api_key": API_KEY}

router = APIRouter()


@router.post("/webhook")
async def handle_whatsapp_message(request: Request, db: AsyncSession = Depends(deps.async_get_db)):
    data = await request.json()

    # TODO: add poll
    if data["event"] != "message":
        # We can't process other event yet
        return f"Unknown event {data['event']}"

    payload = data["payload"]
    print("🚀 ~ payload:", payload)
    text = payload.get("body")

    if not text:
        # We can't process non-text messages yet
        print("No text in message")
        print(payload)
        return "OK"

    # Number in format 1231231231@c.us or @g.us for group
    chat_id = payload["from"]
    print("🚀 ~ chat_id:", chat_id)
    # Message ID - false_11111111111@c.us_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
    message_id = payload['id']

    # IMPORTANT - Always send seen before sending new message
    seen = await send_seen(chat_id=chat_id, message_id=message_id, participant=None)
    print("🚀 ~ seen response:", seen)
    await typing(chat_id=chat_id, seconds=random.random() * 3)

    phone_number = hash_sha256(f"+{chat_id.split('@')[0]}")
    print("🚀 ~ phone_number:", phone_number)
    # Find user by phone number
    user = await crud.user.get_by_phone(db, phone=phone_number)
    print("🚀 ~ user:", user)

    if user is None:
        print("User not found")

        await send_message(
            chat_id=chat_id,
            text="""👋 ¡Hola! Aún no tienes vinculado tu número de telefono.

Vinculalo de la siguiente forma:
1️⃣ Ingresa a: https://cleverbill.ing/dashboard/whatsapp

2️⃣ Registra tu número de WhatsApp

3️⃣ ¡Listo! Ahora puedes enviar tus gastos y ganancias por este chat 🚀

✍️ Envía un mensajes intentando ser lo más claro posible, por ejemplo:
"Gasté 200 pesos en restaurante ayer con mi cuenta bbva"

Ten en cuenta que si no eres de México, es probable que no podamos procesar tu número, mandanos un correo a cleverbilling@proton.me para ayudarte 📧
"""
)
        return {"status": "ok"}

    await react_to_message(message_id=message_id, emoji="⏳")
    await send_message(chat_id=chat_id, text="⏳ Procesando tu mensaje...")

    # Send poll with "Confirm" and "Cancel"
    # await httpx.post(
    #     f"{WAHA_URL}/api/sendPoll",
    #     json={
    #         "session": SESSION,
    #         "chatId": chat_id,
    #         "name": "Do you confirm?",
    #         "options": ["Confirm", "Cancel"]
    #     },
    #     headers=HEADERS
    # )

    # elif data.get("event") == "poll_vote":
    #     poll = data["payload"]
    #     print("🚀 ~ poll:", poll)
    #     chat_id = poll["chatId"]
    #     print("🚀 ~ chat_id:", chat_id)
    #     vote = poll["selectedOption"]
    #     print("🚀 ~ vote:", vote)

        # # Respond based on the user's choice
        # await httpx.post(
        #     f"{WAHA_URL}/api/sendText",
        #     json={"session": SESSION, "chatId": chat_id, "text": f"You chose: {vote}"},
        #     headers=HEADERS
        # )

    return {"status": "ok"}
