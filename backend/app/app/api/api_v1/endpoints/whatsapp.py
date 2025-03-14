import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.ai.whatsapp_parser import WhatsAppParser
from app.api import deps
from app.core.config import settings
from app.utilities.encryption import hash_sha256
from app.utilities.simplifier import accounts as simplify_accounts
from app.utilities.simplifier import categories as simplify_categories
from app.utilities.simplifier import places as simplify_places
from app.utilities.whatsapp import send_interactive, send_reaction, send_text_message

router = APIRouter()
whatsapp_parser = WhatsAppParser(settings.OPENAI_API_KEY)


logging.basicConfig(
    filename="whatsapp_api.log",
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

class WhatsAppCallback(BaseModel):
    """Model for WhatsApp message callback webhook"""
    object: str
    entry: list[dict[str, Any]]


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: int = Query(..., alias="hub.challenge"),
) -> int:
    """
    Verification endpoint for WhatsApp API webhook setup

    WhatsApp API will call this endpoint to verify the webhook is properly configured
    """
    logging.info(f"Webhook verification request received: {hub_mode}, {hub_verify_token}")

    # Check if webhook token matches our configuration
    if hub_verify_token != settings.WHATSAPP_VERIFY_TOKEN:
        logging.error(f"Invalid verification token: {hub_verify_token}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid verification token",
        )

    logging.info(f"Webhook verification successful, returning challenge: {hub_challenge}")
    return hub_challenge


@router.post("/webhook")
async def process_webhook(
    callback: WhatsAppCallback = Body(...),
    db: AsyncSession = Depends(deps.async_get_db),
) -> dict[str, str]:
    """
    Process incoming WhatsApp messages

    This endpoint receives messages from the WhatsApp API and processes them
    to extract transaction information and create the corresponding transactions
    """
    try:
        # Process each incoming message
        for entry in callback.entry:
            # WhatsApp sends messages in 'changes' array
            if "changes" in entry:
                for change in entry["changes"]:
                    if "value" in change and "messages" in change["value"]:
                        for message_obj in change["value"]["messages"]:
                            if "text" in message_obj and "body" in message_obj["text"]:
                                print("🚀 ~ message_obj:", message_obj)
                                # TODO: if this fails for other countries, parse the number like the update user endpoint
                                phone_number = hash_sha256(f"+{message_obj['from']}")
                                message_text = message_obj["text"]["body"]
                                send_to = message_obj["from"]
                                logging.info(f"Message received from {phone_number}: {message_text}")

                                # Find user by phone number
                                user = await crud.user.get_by_phone(db, phone=phone_number)
                                print("🚀 ~ user:", jsonable_encoder(user))

                                if not user:
                                    logging.warning(f"No user found for phone number: {phone_number}")

                                    foo = await send_text_message(
                                        send_to,
                                        """👋 ¡Hola! Aún no tienes vinculado tu número de telefono.

Vinculalo de la siguiente forma:
1️⃣ Ingresa a: https://cleverbill.ing/dashboard/whatsapp

2️⃣ Registra tu número de WhatsApp

3️⃣ ¡Listo! Ahora puedes enviar tus gastos y ganancias por este chat 🚀

✍️ Envía un mensajes intentando ser lo más claro posible, por ejemplo:
"Gasté 200 pesos en restaurante ayer con mi cuenta bbva"

Ten en cuenta que si no eres de México, es probable que no podamos procesar tu número, mandanos un correo a cleverbilling@proton.me para ayudarte 📧
                                        """
                                    )

                                    print("🚀 ~ foo:", foo)
                                    continue

                                foo = await send_reaction(
                                    send_to,
                                    message_obj["id"],
                                    "⏳"
                                )
                                print("🚀 ~ foo:", foo)

                                categories_task = crud.category.get_multi_by_owner(db=db, owner_id=user.id)
                                places_task = crud.place.get_multi_by_owner(db=db, owner_id=user.id)
                                accounts_task = crud.account.get_multi_by_owner(db=db, owner_id=user.id)

                                (
                                    accounts,
                                    places,
                                    categories,
                                ) = await asyncio.gather(
                                    accounts_task,
                                    places_task,
                                    categories_task,
                                )


                                # Parse message to extract transaction data
                                try:
                                    transaction_data = await whatsapp_parser.parse_message(
                                        message=message_text,
                                        categories=simplify_categories(categories),
                                        places=simplify_places(places),
                                        accounts=simplify_accounts(accounts),
                                    )
                                    print("🚀 ~ transaction_data:", transaction_data, transaction_data['id'])

                                    if transaction_data["type"] == "expense":
                                        await send_interactive(
                                            send_to,
                                            f"""Confirma los datos de tu *gasto* _({transaction_data['id']})_:

💸 *Monto:* {transaction_data['amount']}
📅 *Fecha:* {transaction_data['date']}
🏷️ *Categoría:* {transaction_data['category']} - {transaction_data['subcategory']}
📍 *Lugar:* {transaction_data['place']}
📝 *Descripción:* {transaction_data['description']}
💳 *Cuenta:* {transaction_data['account']}
                                            """,
                                            [
                                                {"title": "❌ Cancelar", "id": f"cancel_{transaction_data['id']}"},
                                                {"title": "✅ Confirmar", "id": f"confirm_{transaction_data['id']}"},
                                            ]
                                        )

                                        # # Create expense
                                        # expense_in = schemas.ExpenseCreate(
                                        #     amount=transaction_data["amount"],
                                        #     date=transaction_data["date"],
                                        #     category_id=transaction_data.get("category_id"),
                                        #     subcategory_id=transaction_data.get("subcategory_id"),
                                        #     place=transaction_data.get("place"),
                                        #     description=transaction_data.get("description") or "Added via WhatsApp"
                                        # )

                                        # await crud.expense.create_with_owner(
                                        #     db=db, obj_in=expense_in, owner_id=user.id
                                        # )

                                    # elif transaction_data["type"] == "income":
                                    #     # Create income
                                    #     income_in = schemas.IncomeCreate(
                                    #         amount=transaction_data["amount"],
                                    #         date=transaction_data["date"],
                                    #         category_id=transaction_data.get("category_id"),
                                    #         subcategory_id=transaction_data.get("subcategory_id"),
                                    #         place=transaction_data.get("place"),
                                    #         description=transaction_data.get("description") or "Added via WhatsApp"
                                    #     )

                                    #     await crud.income.create_with_owner(
                                    #         db=db, obj_in=income_in, owner_id=user.id
                                    #     )

                                    # elif transaction_data["type"] == "transfer":
                                    #     logging.info("Transfer transaction detected")
                                    #     await send_text_message(
                                    #         send_to,
                                    #         "Lo siento, aún no se pueden hacer transferencias por WhatsApp, pero estamos trabajando en ello 🚀"
                                    #     )

                                    # Send confirmation message (would integrate with WhatsApp API)
                                    logging.info(f"Transaction(s) created successfully for user {user.id}")

                                except ValueError as e:
                                    logging.error(f"Error parsing message: {str(e)}")

        return {"status": "success"}

    except Exception as e:
        logging.error(f"Error processing webhook: {str(e)}")
        return {"status": "error", "message": str(e)}

