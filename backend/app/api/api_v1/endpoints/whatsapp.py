import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, schemas
from app.ai.whatsapp_parser import WhatsAppParser
from app.api import deps
from app.core.config import settings
from app.utilities.encryption import hash_sha256
from app.utilities.logger import setup_logger
from app.utilities.redis import delete_transaction, get_transaction, store_transaction
from app.utilities.simplifier import accounts as simplify_accounts
from app.utilities.simplifier import categories as simplify_categories
from app.utilities.simplifier import places as simplify_places
from app.utilities.whatsapp import (
    format_currency,
    send_interactive,
    send_reaction,
    send_text_message,
)

router = APIRouter()
whatsapp_parser = WhatsAppParser(settings.OPENAI_API_KEY)
logger = setup_logger("whatsapp_requests", "whatsapp_requests.log")


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
    logger.info(f"Webhook verification request received: {hub_mode}, {hub_verify_token}")

    # Check if webhook token matches our configuration
    if hub_verify_token != settings.WHATSAPP_VERIFY_TOKEN:
        logger.error(f"Invalid verification token: {hub_verify_token}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid verification token",
        )

    logger.info(f"Webhook verification successful, returning challenge: {hub_challenge}")
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
                    if "value" in change:
                        # Process interactive button responses
                        if "messages" in change["value"]:
                            for message_obj in change["value"]["messages"]:
                                sender_number = message_obj.get("from", "")
                                send_to = sender_number  # Reply to the same number
                                phone_number = hash_sha256(f"+{sender_number}")

                                # Find user by phone number
                                user = await crud.user.get_by_phone(db, phone=phone_number)

                                if not user:
                                    logger.warning(f"No user found for phone number: {phone_number}")
                                    await send_text_message(
                                        send_to,
                                        """👋 ¡Hola! Aún no tienes vinculado tu número de telefono.

Vinculalo de la siguiente forma:
1️⃣ Ingresa a: https://cleverbill.ing/dashboard/whatsapp

2️⃣ Registra tu número de WhatsApp

3️⃣ ¡Listo! Ahora puedes enviar tus gastos e ingresos por este chat 🚀

✍️ Envía un mensajes intentando ser lo más claro posible, por ejemplo:
"Gasté 200 pesos en restaurante ayer con mi cuenta bbva"

Ten en cuenta que si no eres de México, es probable que no podamos procesar tu número, mandanos un correo a support@cleverbill.ing para ayudarte 📧
                                        """
                                    )
                                    continue

                                # Handle text messages
                                if "text" in message_obj and "body" in message_obj["text"]:
                                    message_text = message_obj["text"]["body"]
                                    logger.info(f"Text message received from {phone_number}: {message_text}")

                                    # Send "processing" reaction
                                    await send_reaction(
                                        send_to,
                                        message_obj["id"],
                                        "⏳"
                                    )

                                    categories_task = crud.category.get_multi_by_owner(db=db, owner_id=user.id)
                                    places_task = crud.place.get_multi_by_owner(db=db, owner_id=user.id)
                                    accounts_task = crud.account.get_multi_by_owner(db=db, owner_id=user.id)

                                    # Fetch user data in parallel
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

                                        # Check if parsing returned empty data
                                        if not transaction_data or "amount" not in transaction_data or transaction_data["amount"] <= 0:
                                            logger.warning(f"Failed to parse message: {message_text}")
                                            await send_text_message(
                                                send_to,
                                                """❌ No pude entender tu mensaje. Por favor, intenta ser más específico.

Por ejemplo:
• "Gasté 200 pesos en restaurante ayer"
• "Ingreso de 1500 por venta"
• "350 pesos en gasolina con tarjeta bbva"
                                                """
                                            )
                                            continue

                                        # Cache transaction data for later confirmation
                                        transaction_id = transaction_data["id"]

                                        store_success = await store_transaction(
                                            transaction_id=transaction_id,
                                            transaction_data=transaction_data,
                                            user_id=user.id
                                        )

                                        if not store_success:
                                            logger.error(f"Failed to store transaction {transaction_id}. Check redis logs")
                                            await send_text_message(
                                                send_to,
                                                "❌ Ocurrió un error al procesar tu mensaje. Por favor, intenta de nuevo."
                                            )
                                            continue

                                        # Send confirmation message with buttons
                                        if transaction_data["type"] == "expense":
                                            await send_interactive(
                                                send_to,
                                                f"""Confirma los datos de tu *gasto* _({transaction_id})_:

💸 *Monto:* {format_currency(transaction_data['amount'])}
📅 *Fecha:* {transaction_data['date']}
🏷️ *Categoría:* {transaction_data['category'] or 'No especificada'} - {transaction_data['subcategory'] or 'No especificada'}
📍 *Lugar:* {transaction_data['place'] or 'No especificado'}
📝 *Descripción:* {transaction_data['description'] or 'No especificada'}
💳 *Cuenta:* {transaction_data['account'] or 'No especificada'}
                                                """,
                                                [
                                                    {"title": "❌ Cancelar", "id": f"cancel_{transaction_id}"},
                                                    {"title": "✅ Confirmar", "id": f"confirm_{transaction_id}"},
                                                ]
                                            )
                                        elif transaction_data["type"] == "income":
                                            await send_interactive(
                                                send_to,
                                                f"""Confirma los datos de tu *ingreso* _({transaction_id})_:

💰 *Monto:* {format_currency(transaction_data['amount'])}
📅 *Fecha:* {transaction_data['date']}
🏷️ *Categoría:* {transaction_data['category'] or 'No especificada'} - {transaction_data['subcategory'] or 'No especificada'}
📍 *Lugar:* {transaction_data['place'] or 'No especificado'}
📝 *Descripción:* {transaction_data['description'] or 'No especificada'}
💳 *Cuenta:* {transaction_data['account'] or 'No especificada'}
                                                """,
                                                [
                                                    {"title": "❌ Cancelar", "id": f"cancel_{transaction_id}"},
                                                    {"title": "✅ Confirmar", "id": f"confirm_{transaction_id}"},
                                                ]
                                            )
                                        elif transaction_data["type"] == "transfer":
                                            logger.info("Transfer transaction detected")
                                            await send_text_message(
                                                send_to,
                                                "Lo siento, aún no se pueden hacer transferencias por WhatsApp, pero estamos trabajando en ello 🚀"
                                            )

                                    except ValueError as e:
                                        logger.error(f"Error parsing message: {str(e)}")
                                        await send_text_message(
                                            send_to,
                                            f"""❌ Ocurrió un error al procesar tu mensaje: {str(e)}

Por favor, intenta de nuevo con un formato más claro."""
                                        )

                                # Handle interactive responses (button clicks)
                                elif "interactive" in message_obj and "button_reply" in message_obj["interactive"]:
                                    button_data = message_obj["interactive"]["button_reply"]
                                    button_id = button_data.get("id", "")

                                    if button_id.startswith("confirm_"):
                                        # Extract transaction ID from button ID
                                        transaction_id = button_id.replace("confirm_", "")

                                        # Check if transaction exists in cache
                                        cached_data = await get_transaction(transaction_id)

                                        if not cached_data:
                                            await send_text_message(
                                                send_to,
                                                "❌ No se encontró la transacción a confirmar. Puede que haya expirado."
                                            )

                                            continue

                                        transaction_data = cached_data["data"]
                                        user_id = int(cached_data["user_id"])

                                        # Create transaction based on type
                                        try:
                                            if transaction_data["type"] == "expense":
                                                # Create expense
                                                expense_in = schemas.ExpenseCreate(
                                                    amount=transaction_data["amount"],
                                                    date=transaction_data["date"],
                                                    category_id=transaction_data.get("category_id"),
                                                    subcategory_id=transaction_data.get("subcategory_id"),
                                                    place_id=transaction_data.get("place_id"),
                                                    account_id=transaction_data.get("account_id"),
                                                    description=transaction_data.get("description") or "Added via WhatsApp",
                                                    made_from="WhatsApp"
                                                )

                                                await crud.expense.create_with_owner(
                                                    db=db, obj_in=expense_in, owner_id=user_id
                                                )

                                                await send_text_message(
                                                    send_to,
                                                    "✅ ¡Gasto registrado con éxito!"
                                                )

                                            elif transaction_data["type"] == "income":
                                                # Create income
                                                income_in = schemas.IncomeCreate(
                                                    amount=transaction_data["amount"],
                                                    date=transaction_data["date"],
                                                    subcategory_id=transaction_data.get("subcategory_id"),
                                                    place_id=transaction_data.get("place_id"),
                                                    account_id=transaction_data.get("account_id"),
                                                    description=transaction_data.get("description") or "Added via WhatsApp",
                                                    made_from="WhatsApp"
                                                )

                                                await crud.income.create_with_owner(
                                                    db=db, obj_in=income_in, owner_id=user_id
                                                )

                                                await send_text_message(
                                                    send_to,
                                                    "✅ ¡Ingreso registrado con éxito!"
                                                )

                                            # Remove from cache after processing
                                            await delete_transaction(transaction_id)

                                        except Exception as create_error:
                                            logger.error(f"Error creating transaction: {str(create_error)}")
                                            await send_text_message(
                                                send_to,
                                                f"❌ Error al crear la transacción: {str(create_error)}"
                                            )

                                    elif button_id.startswith("cancel_"):
                                        # Extract transaction ID from button ID
                                        transaction_id = button_id.replace("cancel_", "")

                                        # Remove from cache if exists
                                        await delete_transaction(transaction_id)

                                        await send_text_message(
                                            send_to,
                                            "❌ Transacción cancelada. No se ha registrado nada."
                                        )

        return {"status": "success"}

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return {"status": "error", "message": str(e)}

