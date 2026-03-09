import asyncio
import random

from fastapi import APIRouter, Depends, Request
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
from app.utilities.waha import (
    get_random_example_message,
    react_to_message,
    send_message,
    send_poll,
    send_seen,
    start_typing,
    stop_typing,
    typing,
)
from app.utilities.whatsapp import format_currency
from app.utilities.wide_events import enrich_event, mark_for_logging, timed

router = APIRouter()
whatsapp_parser = WhatsAppParser(settings.OPENAI_API_KEY)
logger = setup_logger("waha_requests", "waha_requests.log")

@router.post("/webhook")
async def handle_whatsapp_message(request: Request, db: AsyncSession = Depends(deps.async_get_db)):
    mark_for_logging(request)

    data = await request.json()

    enrich_event(
        request,
        webhook={
            "type": "waha",
            "event": data.get("event"),
            "request_id": request.headers.get('x-webhook-request-id'),
        },
    )

    if data["event"] != "message" and data["event"] != "poll.vote":
        enrich_event(request, webhook={"outcome": "skipped", "reason": "unknown_event"})
        return f"Unknown event {data['event']}"

    payload = data["payload"]
    chat_id = payload["from"] if data["event"] == "message" else payload["vote"]["from"]
    phone_number = hash_sha256(f"+{chat_id.split('@')[0]}")
    user = await crud.user.get_by_phone(db, phone=phone_number)

    enrich_event(
        request,
        whatsapp={
            "phone_hash": phone_number,
            "has_user": user is not None,
            "event_type": data["event"],
        },
    )

    if user is None:
        logger.warning(f"User not found for phone: {phone_number}")
        enrich_event(request, user_lookup={"found": False, "action": "sent_registration_instructions"})
        await send_seen(chat_id=chat_id, message_id=message_id, participant=None)
        await typing(chat_id=chat_id, seconds=random.random() * 3)
        await send_message(
            chat_id=chat_id,
            text=f"""👋 ¡Hola! Aún no tienes vinculado tu número de telefono.

Vinculalo de la siguiente forma:
1️⃣ Ingresa a: https://dashboard.cleverbill.ing/whatsapp

2️⃣ Registra tu número de WhatsApp

3️⃣ ¡Listo! Ahora puedes enviar tus gastos e ingresos por este chat 🚀

✍️ Envía un mensajes intentando ser lo más claro posible, por ejemplo:
"{get_random_example_message()}"

Ten en cuenta que si no eres de México, es probable que no podamos procesar tu número, mandanos un correo a support@cleverbill.ing para ayudarte 📧
"""
        )
        return {"status": "ok"}

    enrich_event(
        request,
        user={"id": user.id, "email": user.email, "is_superuser": user.is_superuser},
    )

    if data.get("event") == "message":
        text = payload.get("body")

        # Message ID - false_11111111111@c.us_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
        message_id = payload['id']

        enrich_event(
            request,
            whatsapp={
                "message_id": message_id,
                "message_type": "text" if text else "non_text",
                "message_length": len(text) if text else 0,
            },
        )

        if not text:
            # We can't process non-text messages yet
            logger.warning(f"Received non-text message: {payload}")
            return "OK"

        # IMPORTANT - Always send seen before sending new message
        await send_seen(chat_id=chat_id, message_id=message_id, participant=None)
        await react_to_message(message_id=message_id, emoji="⏳")
        await start_typing(chat_id=chat_id)

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
            with timed() as t_parse:
                transaction_data = await whatsapp_parser.parse_message(
                    message=text,
                    categories=simplify_categories(categories),
                    places=simplify_places(places),
                    accounts=simplify_accounts(accounts),
                )

            enrich_event(
                request,
                ai={
                    "provider": "openai",
                    "parse_duration_ms": t_parse.ms,
                    "message_length": len(text),
                    "context_items": {
                        "categories": len(categories),
                        "places": len(places),
                        "accounts": len(accounts),
                    },
                },
            )

            # Check if parsing returned empty data

            if not transaction_data or "amount" not in transaction_data or transaction_data["amount"] <= 0:
                logger.warning(f"Failed to parse message: {text}")
                enrich_event(request, parsing={"success": False, "reason": "invalid_amount"})
                await stop_typing(chat_id=chat_id)
                await react_to_message(message_id=message_id, emoji="😵‍💫")
                await send_message(
                    chat_id=chat_id,
                    text=f"""❌ No pude entender tu mensaje. Por favor, intenta ser más específico.

    Por ejemplo:
    • "{get_random_example_message()}"
    • "Ingreso de 1500 por venta"
    • "350 pesos en gasolina con tarjeta bbva"

    Recuerda que aún estamos en fase de pruebas y puede que no todo funcione perfectamente. Si tienes sugerencias, por favor envíanos un mensaje mediante Reddit o Twitter a @Cleverbilling
    """)
                return {"status": "ok"}

            enrich_event(
                request,
                parsing={
                    "success": True,
                    "transaction_type": transaction_data.get("type"),
                    "amount": transaction_data.get("amount"),
                    "has_category": transaction_data.get("category_id") is not None,
                    "has_place": transaction_data.get("place_id") is not None,
                    "has_account": transaction_data.get("account_id") is not None,
                },
            )

            # Save message id to react later
            transaction_data["message_to_react"] = message_id
            # Cache transaction data for later confirmation
            transaction_id = transaction_data["id"]

            with timed() as t_cache:
                store_success = await store_transaction(
                    transaction_id=transaction_id,
                    transaction_data=transaction_data,
                    user_id=user.id
                )

            enrich_event(
                request,
                cache={
                    "operation": "store_transaction",
                    "success": store_success,
                    "duration_ms": t_cache.ms,
                },
            )

            if not store_success:
                logger.error(f"Failed to store transaction {transaction_id}. Check redis logs")
                await stop_typing(chat_id=chat_id)
                await react_to_message(message_id=message_id, emoji="❌")
                await send_message(
                    chat_id=chat_id,
                    text="❌ Ocurrió un error al procesar tu mensaje. Por favor, intenta de nuevo."
                )
                return {"status": "ok"}

            # Send confirmation message with buttons
            if transaction_data["type"] == "expense":
                await stop_typing(chat_id=chat_id)
                await send_poll(
                    chat_id=chat_id,
                    text=f"""Confirma los datos de tu *gasto* _({transaction_id})_:

    💸 *Monto:* {format_currency(transaction_data['amount'])}
    📅 *Fecha:* {transaction_data['date']}
    🏷️ *Categoría:* {transaction_data['category'] or 'No especificada'} - {transaction_data['subcategory'] or 'No especificada'}
    📍 *Lugar:* {transaction_data['place'] or 'No especificado'}
    📝 *Descripción:* {transaction_data['description'] or 'No especificada'}
    💳 *Cuenta:* {transaction_data['account'] or 'No especificada'}
                    """,
                    options=[
                        f"✅ Confirmar ({transaction_id})",
                        f"❌ Cancelar ({transaction_id})",
                    ]
                )
            elif transaction_data["type"] == "income":
                await stop_typing(chat_id=chat_id)
                await send_poll(
                    chat_id=chat_id,
                    text=f"""Confirma los datos de tu *ingreso* _({transaction_id})_:

    💰 *Monto:* {format_currency(transaction_data['amount'])}
    📅 *Fecha:* {transaction_data['date']}
    🏷️ *Categoría:* {transaction_data['category'] or 'No especificada'} - {transaction_data['subcategory'] or 'No especificada'}
    📍 *Lugar:* {transaction_data['place'] or 'No especificado'}
    📝 *Descripción:* {transaction_data['description'] or 'No especificada'}
    💳 *Cuenta:* {transaction_data['account'] or 'No especificada'}
                    """,
                    options=[
                        f"✅ Confirmar ({transaction_id})",
                        f"❌ Cancelar ({transaction_id})",
                    ]
                )
            elif transaction_data["type"] == "transfer":
                await stop_typing(chat_id=chat_id)
                await send_poll(
                    chat_id=chat_id,
                    text=f"""Confirma los datos de tu *transferencia* _({transaction_id})_:

    💸 *Monto:* {format_currency(transaction_data['amount'])}
    📅 *Fecha:* {transaction_data['date']}
    📝 *Descripción:* {transaction_data['description'] or 'No especificada'}
    💳 *Cuenta origen:* {transaction_data.get('from_account') or 'No especificada'}
    💳 *Cuenta destino:* {transaction_data.get('to_account') or 'No especificada'}
                    """,
                    options=[
                        f"✅ Confirmar ({transaction_id})",
                        f"❌ Cancelar ({transaction_id})",
                    ]
                )

        except ValueError as e:
            logger.error(f"Error parsing message: {str(e)}")
            await stop_typing(chat_id=chat_id)
            await react_to_message(message_id=message_id, emoji="❌")
            await send_message(
                chat_id=chat_id,
                text=f"""❌ Ocurrió un error al procesar tu mensaje: {str(e)}

    Por favor, intenta de nuevo con un formato más claro.""")

    if data.get("event") == "poll.vote":
        poll = data["payload"]["vote"]
        vote = poll["selectedOptions"]

        if not vote:
            return {"status": "ok"}

        vote = vote[0]

        enrich_event(request, whatsapp={"vote_action": vote[:15]})

        await start_typing(chat_id=chat_id)

        if vote.startswith("✅ Confirmar"):
            transaction_id = vote.replace("✅ Confirmar (", "").replace(")", "")

            # Check if transaction exists in cache
            cached_data = await get_transaction(transaction_id)

            if not cached_data:
                await stop_typing(chat_id=chat_id)
                await send_message(
                    chat_id=chat_id,
                    text="❌ No se encontró la transacción a confirmar. Puede que haya expirado."
                )

                return {"status": "ok"}

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

                    with timed() as t_db:
                        result = await crud.expense.create_with_owner(
                            db=db, obj_in=expense_in, owner_id=user_id
                        )

                    enrich_event(
                        request,
                        database={"operation": "create_expense", "duration_ms": t_db.ms, "success": result is not None},
                        transaction={"type": "expense", "id": result.id if result else None, "amount": transaction_data["amount"], "source": "waha"},
                    )

                    await stop_typing(chat_id=chat_id)
                    await react_to_message(message_id=transaction_data["message_to_react"], emoji="✅")
                    await send_message(
                        chat_id=chat_id,
                        text=f"✅ ¡Gasto registrado con éxito! _({transaction_data['id']})_"
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

                    with timed() as t_db:
                        result = await crud.income.create_with_owner(
                            db=db, obj_in=income_in, owner_id=user_id
                        )

                    enrich_event(
                        request,
                        database={"operation": "create_income", "duration_ms": t_db.ms, "success": result is not None},
                        transaction={"type": "income", "id": result.id if result else None, "amount": transaction_data["amount"], "source": "waha"},
                    )

                    await stop_typing(chat_id=chat_id)
                    await react_to_message(message_id=transaction_data["message_to_react"], emoji="✅")
                    await send_message(
                        chat_id=chat_id,
                        text=f"✅ ¡Ingreso registrado con éxito! _({transaction_data['id']})_"
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

                    with timed() as t_db:
                        transfer = await crud.transfer.create_with_owner(
                            db=db, obj_in=transfer_in, owner_id=user_id
                        )

                    enrich_event(
                        request,
                        database={"operation": "create_transfer", "duration_ms": t_db.ms, "success": transfer is not None},
                        transaction={"type": "transfer", "id": transfer.id if transfer else None, "amount": transaction_data["amount"], "source": "waha"},
                    )

                    if transfer is None:
                        await stop_typing(chat_id=chat_id)
                        await react_to_message(message_id=transaction_data["message_to_react"], emoji="❌")
                        await send_message(
                            chat_id=chat_id,
                            text="❌ No se pudo crear la transferencia. Verifica que las cuentas sean válidas."
                        )
                    else:
                        await stop_typing(chat_id=chat_id)
                        await react_to_message(message_id=transaction_data["message_to_react"], emoji="✅")
                        await send_message(
                            chat_id=chat_id,
                            text=f"✅ ¡Transferencia registrada con éxito! _({transaction_data['id']})_"
                        )

                # Remove from cache after processing
                await delete_transaction(transaction_id)

            except Exception as create_error:
                logger.error(f"Error creating transaction: {str(create_error)}")
                await stop_typing(chat_id=chat_id)
                await send_message(
                    chat_id=chat_id,
                    text="❌ Error al crear la transacción. Por favor, intenta de nuevo."
                )
                # moved to the end just in case we don't have access to transaction_data
                await react_to_message(message_id=transaction_data.get("message_to_react"), emoji="❌")


        if vote.startswith("❌ Cancelar"):
            # Extract transaction ID from button ID
            transaction_id = vote.replace("❌ Cancelar (", "").replace(")", "")
            cached_data = await get_transaction(transaction_id)

            if not cached_data:
                await stop_typing(chat_id=chat_id)
                return {"status": "ok"}

            # Remove from cache if exists
            await delete_transaction(transaction_id)

            await stop_typing(chat_id=chat_id)
            await react_to_message(message_id=cached_data["data"]["message_to_react"], emoji="❌")
            await send_message(
                chat_id=chat_id,
                text="❌ Transacción cancelada. No se ha registrado nada."
            )

    return {"status": "ok"}
