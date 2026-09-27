import asyncio
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, schemas
from app.ai.whatsapp_parser import PROMPT_VERSION, WhatsAppParser
from app.api import deps
from app.core.config import settings
from app.utilities.encryption import hash_sha256
from app.utilities.redis import (
    delete_transaction,
    get_transaction,
    invalidate_user_cache,
    store_transaction,
)
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
from app.utilities.whatsapp_telemetry import WhatsAppInteraction, track_interaction
from app.utilities.wide_events import (
    enrich_event,
    get_request_id,
    mark_for_logging,
    timed,
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


class WhatsAppCallback(BaseModel):
    """Model for WhatsApp message callback webhook"""
    object: str
    entry: list[dict[str, Any]]


@router.get("/webhook")
async def verify_webhook(
    request: Request,
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: int = Query(..., alias="hub.challenge"),
) -> int:
    """
    Verification endpoint for WhatsApp API webhook setup

    WhatsApp API will call this endpoint to verify the webhook is properly configured
    """
    # Check if webhook token matches our configuration
    verified = hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN
    enrich_event(request, webhook={"type": "whatsapp", "verification": {"mode": hub_mode, "verified": verified}})
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid verification token",
        )

    return hub_challenge


@router.post("/webhook")
async def process_webhook(
    request: Request,
    callback: WhatsAppCallback = Body(...),
    db: AsyncSession = Depends(deps.async_get_db),
) -> dict[str, str]:
    """
    Process incoming WhatsApp messages

    This endpoint receives messages from the WhatsApp API and processes them
    to extract transaction information and create the corresponding transactions.

    Each message is tracked as its own ``whatsapp.message`` Axiom event (see
    ``app.utilities.whatsapp_telemetry``); the request's wide event only keeps
    a summary pointing at them.
    """
    # Mark this critical endpoint to always be logged (bypasses sampling)
    mark_for_logging(request)

    request_id = get_request_id(request)
    interaction_ids: list[str] = []
    failed = 0

    for entry in callback.entry:
        for change in entry.get("changes", []):
            for message_obj in change.get("value", {}).get("messages", []):
                sender_number = message_obj.get("from", "")
                try:
                    async with track_interaction(
                        wa_message_id=message_obj.get("id"),
                        from_hash=hash_sha256(f"+{sender_number}"),
                        message_type=message_obj.get("type", "unknown"),
                        request_id=request_id,
                    ) as ix:
                        interaction_ids.append(ix.id)
                        await _handle_message(db, message_obj, ix)
                except Exception:
                    # Already recorded on the interaction event; keep processing
                    # the rest of the batch with a clean session.
                    failed += 1
                    await db.rollback()

    enrich_event(
        request,
        webhook={
            "type": "whatsapp",
            "entries_count": len(callback.entry),
            "messages_count": len(interaction_ids),
            "failed_count": failed,
            "interaction_ids": interaction_ids,
        },
    )

    return {"status": "error" if failed else "success"}


async def _handle_message(db: AsyncSession, message_obj: dict[str, Any], ix: WhatsAppInteraction) -> None:
    """Handle a single inbound message, recording what happened on ``ix``."""
    sender_number = message_obj.get("from", "")
    send_to = sender_number  # Reply to the same number
    phone_number = hash_sha256(f"+{sender_number}")

    # Find user by phone number
    user = await crud.user.get_by_phone(db, phone=phone_number)

    ix.set(input=_describe_input(message_obj))

    if not user:
        ix.set(action="unregistered_user", outcome="sent_registration_instructions")
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
        return

    ix.set(
        user={
            "id": user.id,
            "has_default_account": user.default_account_id is not None,
        },
    )

    defaultAccountMessage = "" if user.default_account_id is not None else """💡 *Tip:* También puedes escribir "*cuenta por defecto*" para configurar una cuenta predeterminada y hacer el proceso más rápido."""

    # Handle text messages
    if "text" in message_obj and "body" in message_obj["text"]:
        message_text = message_obj["text"]["body"]

        # Check if user wants to set default account
        if any(keyword in message_text.lower() for keyword in ["cuenta por defecto", "cuenta predeterminada", "default account", "configurar cuenta"]):
            ix.set(action="request_default_account")

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
                ix.set(outcome="no_accounts")
                return

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
            ix.set(outcome="sent_account_list")
            return

        ix.set(action="parse_transaction")

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
            with timed() as t_parse:
                parsed = await whatsapp_parser.parse_message(
                    message=message_text,
                    categories=simplify_categories(categories),
                    places=simplify_places(places),
                    accounts=simplify_accounts(accounts),
                    default_account=default_account
                )
            transaction_data = parsed.transaction

            ix.set(llm={**parsed.trace, "parse_duration_ms": t_parse.ms})

            # Check if parsing returned empty data
            if not parsed.ok:
                ix.set(outcome="parse_failed")
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
                return

            ix.set(transaction=_describe_transaction(transaction_data))

            # Save message id to react later
            transaction_data["message_to_react"] = message_obj["id"]
            # Link the later confirm/cancel back to this parse (see whatsapp_telemetry)
            transaction_data["telemetry"] = {
                "interaction_id": ix.id,
                "input_text": message_text,
                "model_used": parsed.trace.get("model_used"),
                "prompt_version": PROMPT_VERSION,
                "parsed_at": time.time(),
            }
            # Cache transaction data for later confirmation
            transaction_id = transaction_data["id"]

            with timed() as t_cache:
                store_success = await store_transaction(
                    transaction_id=transaction_id,
                    transaction_data=transaction_data,
                    user_id=user.id
                )

            ix.set(cache={"operation": "store_transaction", "success": store_success, "duration_ms": t_cache.ms})

            if not store_success:
                ix.set(outcome="cache_error")
                await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
                await send_text_message(
                    send_to,
                    "❌ Ocurrió un error al procesar tu mensaje. Por favor, intenta de nuevo."
                )
                return

            # Send confirmation message with buttons
            ix.set(outcome="awaiting_confirmation")
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
                    ix.set(outcome="transfer_missing_accounts")
                    await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
                    await send_text_message(
                        send_to,
                        """❌ Para realizar una transferencia, necesitas escribir la cuenta de destino y la cuenta de origen.

Por ejemplo:
    • "Transferir 500 de bbva a santander"
    • "Pasar 1000 de efectivo a tarjeta de crédito"

Asegúrate de mencionar ambas cuentas y que estén registradas en tu perfil."""
                    )
                    return

                if transaction_data.get("from_account_id") == transaction_data.get("to_account_id"):
                    ix.set(outcome="transfer_same_account")
                    await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
                    await send_text_message(
                        send_to,
                        """❌ No puedes transferir dinero a la misma cuenta.

Por favor, especifica dos cuentas diferentes:
    • "Transferir 500 de bbva a santander"
    • "Pasar 1000 de efectivo a tarjeta de crédito" """
                    )
                    return

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
            else:
                ix.set(outcome="unsupported_transaction_type")

        except ValueError as e:
            ix.record_error(e)
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
            ix.set(action="confirm_transaction")

            # Check if transaction exists in cache
            cached_data = await get_transaction(transaction_id)

            if not cached_data:
                ix.set(outcome="expired", feedback=_feedback(transaction_id, None, "expired"))
                await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
                await send_text_message(
                    send_to,
                    "❌ No se encontró la transacción a confirmar. Puede que haya expirado."
                )

                return

            transaction_data = cached_data["data"]
            user_id = int(cached_data["user_id"])
            message_to_react = transaction_data["message_to_react"]
            ix.set(
                feedback=_feedback(transaction_id, transaction_data, "confirmed"),
                transaction=_describe_transaction(transaction_data),
            )

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
                        expesne = await crud.expense.create_with_owner(
                            db=db, obj_in=expense_in, owner_id=user_id
                        )

                    ix.set(database={
                        "operation": "create_expense",
                        "duration_ms": t_db.ms,
                        "success": expesne is not None,
                        "record_id": expesne.id if expesne else None,
                    })

                    if expesne is None:
                        ix.set(outcome="db_error")
                        await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="❌")
                        await send_text_message(
                            send_to,
                            "❌ No se pudo crear el gasto. Intenta de nuevo."
                        )
                    else:
                        # Invalidate user's cached dashboard data
                        await invalidate_user_cache(user_id)
                        await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="✅")
                        await send_text_message(
                            send_to,
                            "✅ ¡Gasto registrado con éxito!"
                        )
                        ix.set(outcome="created")

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
                        income = await crud.income.create_with_owner(
                            db=db, obj_in=income_in, owner_id=user_id
                        )

                    ix.set(database={
                        "operation": "create_income",
                        "duration_ms": t_db.ms,
                        "success": income is not None,
                        "record_id": income.id if income else None,
                    })

                    if income is None:
                        ix.set(outcome="db_error")
                        await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="❌")
                        await send_text_message(
                            send_to,
                            "❌ No se pudo crear el ingreso. Intenta de nuevo."
                        )
                    else:
                        # Invalidate user's cached dashboard data
                        await invalidate_user_cache(user_id)
                        await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="✅")
                        await send_text_message(
                            send_to,
                            "✅ ¡Ingreso registrado con éxito!"
                        )
                        ix.set(outcome="created")

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

                    ix.set(database={
                        "operation": "create_transfer",
                        "duration_ms": t_db.ms,
                        "success": transfer is not None,
                        "record_id": transfer.id if transfer else None,
                    })

                    if transfer is None:
                        ix.set(outcome="db_error")
                        await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="❌")
                        await send_text_message(
                            send_to,
                            "❌ No se pudo crear la transferencia. Intenta de nuevo."
                        )
                    else:
                        # Invalidate user's cached dashboard data
                        await invalidate_user_cache(user_id)
                        await send_reaction(phone_number=send_to, message_id=message_to_react, emoji="✅")
                        await send_text_message(
                            send_to,
                            "✅ ¡Transferencia registrada con éxito!"
                        )
                        ix.set(outcome="created")

                # Remove from cache after processing
                await delete_transaction(transaction_id)

            except Exception as create_error:
                ix.record_error(create_error)
                await send_text_message(
                    send_to,
                    f"❌ Error al crear la transacción: {str(create_error)}"
                )

        elif button_id.startswith("cancel_"):
            # Extract transaction ID from button ID
            transaction_id = button_id.replace("cancel_", "")
            ix.set(action="cancel_transaction")

            cached_data = await get_transaction(transaction_id)
            ix.set(
                outcome="cancelled",
                feedback=_feedback(transaction_id, cached_data["data"] if cached_data else None, "cancelled"),
            )

            # Remove from cache if exists
            await delete_transaction(transaction_id)

            await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
            await send_text_message(
                send_to,
                "❌ Transacción cancelada. No se ha registrado nada."
            )

    # Handle list replies
    elif "interactive" in message_obj and "list_reply" in message_obj["interactive"]:
        list_data = message_obj["interactive"]["list_reply"]
        selection_id = list_data.get("id", "")

        if selection_id.startswith("set_default_"):
            # Extract account ID from selection
            account_id = int(selection_id.replace("set_default_", ""))
            ix.set(action="set_default_account")

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
                ix.set(outcome="default_account_set")

            except ValueError as e:
                ix.record_error(e)
                await send_reaction(phone_number=send_to, message_id=message_obj["id"], emoji="❌")
                await send_text_message(
                    send_to,
                    f"❌ Error al configurar la cuenta por defecto: {str(e)}"
                )

        elif selection_id.startswith("page_prev_") or selection_id.startswith("page_next_"):
            # Handle pagination navigation
            ix.set(action="paginate_accounts")
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
                        "*Selecciona tu cuenta por defecto*",
                        account_rows,
                        page=page
                    )
                    ix.set(outcome="sent_account_list")

            except (ValueError, IndexError) as e:
                ix.record_error(e)
                await send_text_message(
                    send_to,
                    "❌ Error al navegar. Escribe 'cuenta por defecto' para intentar de nuevo."
                )

    else:
        # Images, audio, stickers, locations... are not supported yet
        ix.set(action="unsupported_message", outcome="ignored")


def _describe_input(message_obj: dict[str, Any]) -> dict[str, Any]:
    """What the user sent, as it should appear in telemetry."""
    if "text" in message_obj:
        text = message_obj["text"].get("body", "")
        return {"text": text, "length": len(text)}
    interactive = message_obj.get("interactive", {})
    reply = interactive.get("button_reply") or interactive.get("list_reply")
    if reply:
        return {"reply_id": reply.get("id"), "reply_title": reply.get("title")}
    return {}


def _describe_transaction(transaction_data: dict[str, Any]) -> dict[str, Any]:
    """The fields the LLM extracted, so its choices can be compared to what the user confirms."""
    fields = (
        "id", "type", "amount", "date", "description",
        "category_id", "category", "subcategory_id", "subcategory",
        "place_id", "place", "account_id", "account",
        "from_account_id", "from_account", "to_account_id", "to_account",
    )
    described = {key: transaction_data.get(key) for key in fields}
    if described["date"] is not None:
        described["date"] = str(described["date"])
    return described


def _feedback(transaction_id: str, transaction_data: dict[str, Any] | None, verdict: str) -> dict[str, Any]:
    """The user's verdict on an LLM parse, linked back to the interaction that produced it."""
    feedback: dict[str, Any] = {"verdict": verdict, "transaction_id": transaction_id}
    source = (transaction_data or {}).get("telemetry")
    if source:
        feedback.update(
            source_interaction_id=source.get("interaction_id"),
            source_input_text=source.get("input_text"),
            model_used=source.get("model_used"),
            prompt_version=source.get("prompt_version"),
            seconds_to_decision=round(time.time() - source["parsed_at"], 1) if source.get("parsed_at") else None,
        )
    return feedback
