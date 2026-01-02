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
    send_paginated_list,
    send_reaction,
    send_text_message,
)

router = APIRouter()

# Parse fallback models from comma-separated string
_fallback_models = None
if settings.OPENROUTER_FALLBACK_MODELS:
    _fallback_models = [m.strip() for m in settings.OPENROUTER_FALLBACK_MODELS.split(",") if m.strip()]

whatsapp_parser = WhatsAppParser(
    api_key=settings.OPENROUTER_API_KEY,
    model=settings.OPENROUTER_MODEL,
    fallback_models=_fallback_models,
    site_url=settings.OPENROUTER_SITE_URL,
    app_name=settings.OPENROUTER_APP_NAME,
)
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
                                print("Sender number:", sender_number)
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

                                defaultAccountMessage = "" if user.default_account_id is not None else """💡 *Tip:* También puedes escribir "*cuenta por defecto*" para configurar una cuenta predeterminada y hacer el proceso más rápido."""

                                # Handle text messages
                                if "text" in message_obj and "body" in message_obj["text"]:
                                    message_text = message_obj["text"]["body"]
                                    logger.info(f"Text message received from {phone_number}: {message_text}")

                                    # Check if user wants to set default account
                                    if any(keyword in message_text.lower() for keyword in ["cuenta por defecto", "cuenta predeterminada", "default account", "configurar cuenta"]):
                                        # Fetch user accounts
                                        accounts = await crud.account.get_multi_by_owner(db=db, owner_id=user.id)

                                        if not accounts:
                                            await send_text_message(
                                                send_to,
                                                """❌ No tienes cuentas registradas aún.

Para agregar una cuenta:
1️⃣ Ingresa a https://cleverbill.ing/dashboard/accounts
2️⃣ Crea una nueva cuenta
3️⃣ Regresa aquí y escribe "cuenta por defecto" para configurarla"""
                                            )
                                            continue

                                        # Create sections for the interactive list
                                        account_rows = []
                                        for account in accounts:
                                            account_rows.append({
                                                "id": f"set_default_{account.id}",
                                                "title": account.name,
                                                "description": f"{account.type.value} • {format_currency(account.current_balance)}"
                                            })

                                        # Get current default account
                                        current_default = await crud.user.get_default_account(db=db, user_id=user.id)
                                        current_text = f" (Actual: {current_default.name})" if current_default else ""

                                        await send_paginated_list(
                                            send_to,
                                            f"*Selecciona tu cuenta por defecto{current_text}*",
                                            account_rows
                                        )
                                        continue

                                    # Send "processing" reaction for transaction messages
                                    await send_reaction(send_to, message_obj["id"], "⏳")

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

                                    # Find the default account from the fetched accounts
                                    default_account = None
                                    if user.default_account_id:
                                        default_account = next(
                                            (account for account in accounts if account.id == user.default_account_id),
                                            None
                                        )

                                    # Parse message to extract transaction data
                                    try:
                                        transaction_data = await whatsapp_parser.parse_message(
                                            message=message_text,
                                            categories=simplify_categories(categories),
                                            places=simplify_places(places),
                                            accounts=simplify_accounts(accounts),
                                            default_account=default_account
                                        )

                                        # Check if parsing returned empty data
                                        if not transaction_data or "amount" not in transaction_data or transaction_data["amount"] <= 0:
                                            logger.warning(f"Failed to parse message: {message_text}")
                                            await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="😵‍💫")
                                            await send_text_message(
                                                send_to,
                                                f"""❌ No pude entender tu mensaje. Por favor, intenta ser más específico.

Por ejemplo:
• "Gasté 200 pesos en restaurante ayer"
• "Ingreso de 1500 por venta"
• "350 pesos en gasolina con tarjeta bbva"
• "Transferí 500 de bbva a santander"

{defaultAccountMessage}
                                                """
                                            )
                                            continue

                                        # Save message id to react later
                                        transaction_data["message_to_react"] = message_obj["id"]
                                        # Cache transaction data for later confirmation
                                        transaction_id = transaction_data["id"]

                                        store_success = await store_transaction(
                                            transaction_id=transaction_id,
                                            transaction_data=transaction_data,
                                            user_id=user.id
                                        )

                                        if not store_success:
                                            logger.error(f"Failed to store transaction {transaction_id}. Check redis logs")
                                            await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
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

{defaultAccountMessage}
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

{defaultAccountMessage}
                                                """,
                                                [
                                                    {"title": "❌ Cancelar", "id": f"cancel_{transaction_id}"},
                                                    {"title": "✅ Confirmar", "id": f"confirm_{transaction_id}"},
                                                ]
                                            )
                                        elif transaction_data["type"] == "transfer":
                                            if not transaction_data.get("from_account_id") or not transaction_data.get("to_account_id"):
                                                logger.warning(f"Transfer validation failed - missing accounts: {message_text}")
                                                await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
                                                await send_text_message(
                                                    send_to,
                                                    """❌ Para realizar una transferencia, necesitas escribir la cuenta de destino y la cuenta de origen.

Por ejemplo:
    • "Transferir 500 de bbva a santander"
    • "Pasar 1000 de efectivo a tarjeta de crédito"

Asegúrate de mencionar ambas cuentas y que estén registradas en tu perfil."""
                                                )
                                                continue

                                            if transaction_data.get("from_account_id") == transaction_data.get("to_account_id"):
                                                logger.warning(f"Transfer validation failed - same account: {message_text}")
                                                await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
                                                await send_text_message(
                                                    send_to,
                                                    """❌ No puedes transferir dinero a la misma cuenta.

Por favor, especifica dos cuentas diferentes:
    • "Transferir 500 de bbva a santander"
    • "Pasar 1000 de efectivo a tarjeta de crédito" """
                                                )
                                                continue

                                            await send_interactive(
                                                send_to,
                                                f"""Confirma los datos de tu *transferencia* _({transaction_id})_:

💸 *Monto:* {format_currency(transaction_data['amount'])}
📅 *Fecha:* {transaction_data['date']}
📝 *Descripción:* {transaction_data['description'] or 'Sin descripción'}
💳 *Cuenta origen:* {transaction_data.get('from_account')}
💳 *Cuenta destino:* {transaction_data.get('to_account')}
                                                """,
                                                [
                                                    {"title": "❌ Cancelar", "id": f"cancel_{transaction_id}"},
                                                    {"title": "✅ Confirmar", "id": f"confirm_{transaction_id}"},
                                                ]
                                            )

                                    except ValueError as e:
                                        logger.error(f"Error parsing message: {str(e)}")
                                        await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
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
                                            await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
                                            await send_text_message(
                                                send_to,
                                                "❌ No se encontró la transacción a confirmar. Puede que haya expirado."
                                            )

                                            continue

                                        transaction_data = cached_data["data"]
                                        user_id = int(cached_data["user_id"])
                                        message_to_react = transaction_data["message_to_react"]

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

                                                expesne = await crud.expense.create_with_owner(
                                                    db=db, obj_in=expense_in, owner_id=user_id
                                                )

                                                if expesne is None:
                                                    logger.error(f"Failed to create expense: {transaction_data}")
                                                    await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="❌")
                                                    await send_text_message(
                                                        send_to,
                                                        "❌ No se pudo crear el gasto. Intenta de nuevo."
                                                    )
                                                else:
                                                    await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="✅")
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

                                                income = await crud.income.create_with_owner(
                                                    db=db, obj_in=income_in, owner_id=user_id
                                                )

                                                if income is None:
                                                    logger.error(f"Failed to create income: {transaction_data}")
                                                    await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="❌")
                                                    await send_text_message(
                                                        send_to,
                                                        "❌ No se pudo crear el ingreso. Intenta de nuevo."
                                                    )
                                                else:
                                                    await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="✅")
                                                    await send_text_message(
                                                        send_to,
                                                        "✅ ¡Ingreso registrado con éxito!"
                                                    )

                                            elif transaction_data["type"] == "transfer":
                                                # Create transfer
                                                transfer_in = schemas.TransferCreate(
                                                    amount=transaction_data["amount"],
                                                    date=transaction_data["date"],
                                                    from_acc=transaction_data.get("from_account_id"),
                                                    to_acc=transaction_data.get("to_account_id"),
                                                    description=transaction_data.get("description") or "Added via WhatsApp",
                                                )

                                                transfer = await crud.transfer.create_with_owner(
                                                    db=db, obj_in=transfer_in, owner_id=user_id
                                                )

                                                if transfer is None:
                                                    logger.error(f"Failed to create transfer: {transaction_data}")
                                                    await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="❌")
                                                    await send_text_message(
                                                        send_to,
                                                        "❌ No se pudo crear la transferencia. Intenta de nuevo."
                                                    )
                                                else:
                                                    await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="✅")
                                                    await send_text_message(
                                                        send_to,
                                                        "✅ ¡Transferencia registrada con éxito!"
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

                                        await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
                                        await send_text_message(
                                            send_to,
                                            "❌ Transacción cancelada. No se ha registrado nada."
                                        )

                                # Handle list replies
                                elif "list_reply" in message_obj["interactive"]:
                                    list_data = message_obj["interactive"]["list_reply"]
                                    selection_id = list_data.get("id", "")

                                    if selection_id.startswith("set_default_"):
                                        # Extract account ID from selection
                                        account_id = int(selection_id.replace("set_default_", ""))

                                        try:
                                            # Set the default account
                                            await crud.user.set_default_account(db=db, user_id=user.id, account_id=account_id)

                                            # Get the account name for confirmation
                                            account = await crud.account.get_by_id(db=db, owner_id=user.id, id=account_id)

                                            await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="✅")
                                            await send_text_message(
                                                send_to,
                                                f"""✅ ¡Perfecto! Tu cuenta por defecto ahora es: *{account.name}*

Ahora cuando envíes mensajes como "gasté 200 en comida" sin especificar cuenta, se registrará automáticamente en esta cuenta.

Si quieres usar otra cuenta específica, solo menciona su nombre: "gasté 200 en comida con mi tarjeta BBVA" """
                                            )

                                        except ValueError as e:
                                            await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
                                            await send_text_message(
                                                send_to,
                                                f"❌ Error al configurar la cuenta por defecto: {str(e)}"
                                            )

                                    elif selection_id.startswith("page_prev_") or selection_id.startswith("page_next_"):
                                        # Handle pagination navigation
                                        try:
                                            if selection_id.startswith("page_prev_"):
                                                page_info = selection_id.replace("page_prev_set_default_", "")
                                                page = int(page_info)
                                            else:  # page_next_
                                                page_info = selection_id.replace("page_next_set_default_", "")
                                                page = int(page_info)

                                            # Fetch user accounts again and send the requested page
                                            accounts = await crud.account.get_multi_by_owner(db=db, owner_id=user.id)

                                            if accounts:
                                                account_rows = []
                                                for account in accounts:
                                                    account_rows.append({
                                                        "id": f"set_default_{account.id}",
                                                        "title": account.name,
                                                        "description": f"{account.type.value} • {format_currency(account.current_balance)}"
                                                    })

                                                await send_paginated_list(
                                                    send_to,
                                                    f"*Selecciona tu cuenta por defecto*",
                                                    account_rows,
                                                    page=page
                                                )

                                        except (ValueError, IndexError) as e:
                                            logger.error(f"Error handling pagination: {str(e)}")
                                            await send_text_message(
                                                send_to,
                                                "❌ Error al navegar. Escribe 'cuenta por defecto' para intentar de nuevo."
                                            )

        return {"status": "success"}

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return {"status": "error", "message": str(e)}

