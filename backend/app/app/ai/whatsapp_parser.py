import json
import secrets
from datetime import date, datetime
from typing import Any, Optional

from openai import AsyncOpenAI, RateLimitError
from pydantic import BaseModel

from app.schemas.account import Account
from app.ai.ocr import TransactionType
from app.utilities.logger import setup_logger

logger = setup_logger("whatsapp_requests", "whatsapp_requests.log")

class WhatsAppMessage(BaseModel):
    """Model for WhatsApp message data"""
    message: str
    from_number: str
    timestamp: Optional[datetime] = None


class WhatsAppParser:
    """Class to parse WhatsApp messages and extract transaction data using OpenRouter"""

    def __init__(
        self,
        api_key: str = None,
        model: str = "openai/gpt-4o-mini",
        fallback_models: list[str] = None,
        site_url: str = None,
        app_name: str = None,
    ):
        """
        Initialize the parser with OpenRouter API key and model configuration

        Args:
            api_key: OpenRouter API key
            model: Model to use (e.g., 'openai/gpt-4o-mini', 'anthropic/claude-3-haiku')
            fallback_models: List of fallback models to try if primary model fails
            site_url: Optional site URL for OpenRouter analytics
            app_name: Optional app name for OpenRouter analytics
        """
        self.model = model
        self.fallback_models = fallback_models
        if api_key:
            # Build optional headers for OpenRouter analytics
            headers = {}
            if site_url:
                headers["HTTP-Referer"] = site_url
            if app_name:
                headers["X-Title"] = app_name

            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers=headers if headers else None,
            )
        else:
            self.client = None

    async def analyze_with_ai(self, message: str, categories, places, accounts) -> dict:
        """
        Analyze WhatsApp message using OpenRouter to extract transaction information
        """
        if not self.client:
            raise ValueError("OpenRouter client not initialized. Please provide an API key.")

        prompt = f"""You are an assistant in a personal finance app. Parse the following message about a financial transaction and extract the relevant information.

CRITICAL REQUIREMENTS:
- You MUST always return a valid JSON object
- The 'type' field is REQUIRED and MUST be one of: 'expense', 'income', or 'transfer'
- The 'amount' field is REQUIRED and must be a positive number

        Rules:
        - type: **REQUIRED** - MUST be one of: 'expense', 'income', or 'transfer'. Use these rules to determine the type:
            * 'transfer': The message mentions moving money between accounts (keywords: "transferir", "pasar de/a", "mover de/a", "enviar de/a").
            * 'income': The message describes money received or earned (keywords: "ingreso", "ingresé", "cobré", "cobro", "me pagaron", "nómina", "nomina", "salario", "sueldo", "venta", "vendí", "devolución", "reembolso", "me depositaron", "recibí", "ganancia", "bono", "premio", "renta cobrada", "freelance", "honorarios"). Also, if the best matching category has `is_income: True`, the type MUST be 'income'.
            * 'expense': Everything else — money spent or paid. Default to 'expense' only if the message does not match income or transfer patterns.
        - amount: Extract the numerical amount as a float
        - date: Extract date in YYYY-MM-DD format. If relative dates are mentioned (today, yesterday, etc.), calculate the actual date ({date.today()})
        - category: Match the best category based on the description from this list: {categories}. Respond with the id and name of the category or null if not applicable. IMPORTANT: Each category has a field `is_income` — if the transaction type is 'income', you MUST select a category where `is_income` is True. If the transaction type is 'expense', you MUST select a category where `is_income` is False.
        - subcategory: **CRITICAL** - The subcategory MUST belong to the selected category. Each category has a list of subcategories. You can ONLY choose a subcategory from the "subcategories" array of the selected category. If the selected category doesn't have an appropriate subcategory in its list, return null. Respond with the id and name of the subcategory or null.
        - place: Match the transaction location to the most appropriate place, using the provided list: {places}. Return the id and name ONLY if there's a clear match in the provided list, otherwise return null.
        - description: Brief description in Spanish of what the transaction was for.
        - account: Identify the payment method or account STRICTLY from the provided list: {accounts}. Only return the id and name if there's an EXACT or VERY CLOSE match (like "bbva" matching "bbva débito"). If the account mentioned is not in the provided list (like "santander" when santander isn't in the list), return null.
        - from_account: For transfers, identify the source account from the provided list: {accounts}. Only return the id and name if there's an EXACT or VERY CLOSE match.
        - to_account: For transfers, identify the destination account from the provided list: {accounts}. Only return the id and name if there's an EXACT or VERY CLOSE match.
        - id: Short (max 10 chars) unique identifier for the transaction with text divided by dashes

        Examples of incoming messages and their expected types:
        - "2000 pesos cena de antes de ayer" -> type: "expense"
        - "154.04 en al super despensa con bbva" -> type: "expense"
        - "ingreso 1800 nomina" -> type: "income"
        - "me pagaron 5000 de freelance" -> type: "income"
        - "cobré 3000 de renta" -> type: "income"
        - "cuenta nu 249 autozone" -> type: "expense"
        - "transferir 500 de bbva a santander" -> type: "transfer"
        - "pasar 1000 de efectivo a tarjeta de credito" -> type: "transfer"

        IMPORTANT: When selecting a subcategory, verify it exists in the selected category's subcategories array. For example:
        - If you select category "Compras" with id 5, you can only choose subcategories that appear in categories[where id=5].subcategories
        - If you select category "Alimentación" with id 3, you can only choose subcategories from categories[where id=3].subcategories
        - Never mix subcategories from different categories

        Do not attempt fuzzy matching for accounts or places. Only return a match if you are highly confident it's the correct one from the provided lists.

        Respond with a single valid JSON object containing all extracted fields. Use null for any fields you cannot determine, EXCEPT for 'type' and 'amount' which are REQUIRED and must always be present.
        """

        try:
            # Build extra_body with fallback models if configured
            extra_body = None
            if self.fallback_models:
                extra_body = {"models": self.fallback_models}

            response = await self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": prompt
                    },
                    {
                        "role": "user",
                        "content": f"Message to parse: \"{message}\""
                    }
                ],
                max_tokens=1000,
                extra_body=extra_body,
            )
            return json.loads(response.choices[0].message.content)
        except RateLimitError as e:
            if "insufficient_quota" in str(e):
                raise ValueError("Insufficient OpenRouter API credits")
            raise ValueError("OpenRouter rate limit exceeded")
        except Exception as e:
            logger.error(f"Error analyzing message with OpenRouter: {str(e)}")
            raise ValueError(f"Error analyzing message with OpenRouter: {str(e)}")

    async def parse_message(
        self,
        message: str,
        categories: list[dict[str, Any]] = None,
        places: list[dict[str, Any]] = None,
        accounts: list[dict[str, Any]] = None,
        default_account: Optional[Account] = None
    ) -> dict[str, Any]:
        """
        Parse WhatsApp message to extract transaction information
        """
        if not message or not message.strip():
            logger.warning("Empty message received for parsing")
            return {}

        try:
            if self.client:
                ai_result = await self.analyze_with_ai(message, categories, places, accounts)
                if not ai_result:
                    logger.warning(f"AI analysis produced empty result for message: {message}")
                    return {}

                transaction = self.convert_ai_result_to_transaction(ai_result, default_account)

                # Cross-check transaction type against category's is_income flag
                transaction = self.correct_type_from_category(transaction, categories or [])

                # Validate the transaction has minimal required data
                if not self.validate_transaction(transaction):
                    logger.warning(f"Parsed transaction failed validation: {transaction}")
                    return {}

                # Validate subcategory belongs to category
                if not self.validate_subcategory_belongs_to_category(
                    transaction.get("category_id"),
                    transaction.get("subcategory_id"),
                    categories or []
                ):
                    logger.warning(
                        f"Subcategory {transaction.get('subcategory_id')} "
                        f"does not belong to category {transaction.get('category_id')}. "
                        f"Removing subcategory from transaction."
                    )
                    # Remove the invalid subcategory but keep the transaction
                    transaction["subcategory_id"] = None
                    transaction["subcategory"] = None

                # If there's a category but no subcategory, remove the category
                if transaction.get("category_id") and not transaction.get("subcategory_id"):
                    logger.warning(
                        f"Category {transaction.get('category_id')} present but no subcategory. "
                        f"Removing category to avoid incomplete categorization."
                    )
                    transaction["category_id"] = None
                    transaction["category"] = None

                return transaction
            else:
                logger.error("No OpenRouter client available for message parsing")
                return {}
        except Exception as ai_error:
            logger.warning(f"AI analysis failed: {str(ai_error)}")
            return {}

    def validate_transaction(self, transaction: dict) -> bool:
        """Validate that a transaction has the minimum required fields"""
        # A valid transaction must have at least an amount and a type
        if "amount" not in transaction or not transaction["amount"]:
            return False

        if "type" not in transaction or not transaction["type"]:
            return False

        if transaction["amount"] <= 0:
            return False

        return True

    def correct_type_from_category(
        self, transaction: dict, categories: list[dict[str, Any]]
    ) -> dict:
        """
        Cross-check the transaction type against the selected category's is_income flag.
        If the AI picked a category with is_income=True but set type as 'expense',
        correct the type to 'income'. Vice versa for is_income=False with type 'income'.
        Transfers are never corrected since they don't depend on category.
        """
        # Don't touch transfers — they're account-to-account, not category-dependent
        if transaction.get("type") == TransactionType.TRANSFER.value or transaction.get("type") == TransactionType.TRANSFER:
            return transaction

        category_id = transaction.get("category_id")
        if not category_id or not categories:
            return transaction

        # Find the category in the list
        category = next((cat for cat in categories if cat.get("id") == category_id), None)
        if not category:
            return transaction

        is_income = category.get("is_income", False)

        if is_income and transaction.get("type") != TransactionType.INCOME.value:
            logger.info(
                f"Correcting transaction type from '{transaction.get('type')}' to 'income' "
                f"because category '{category.get('name')}' has is_income=True"
            )
            transaction["type"] = TransactionType.INCOME.value
        elif not is_income and transaction.get("type") == TransactionType.INCOME.value:
            logger.info(
                f"Correcting transaction type from 'income' to 'expense' "
                f"because category '{category.get('name')}' has is_income=False"
            )
            transaction["type"] = TransactionType.EXPENSE.value

        return transaction

    def validate_subcategory_belongs_to_category(
        self, category_id: int | None, subcategory_id: int | None, categories: list[dict[str, Any]]
    ) -> bool:
        """
        Validate that a subcategory belongs to the specified category

        Args:
            category_id: The category ID
            subcategory_id: The subcategory ID to validate
            categories: List of categories with their subcategories

        Returns:
            True if valid (no subcategory, or subcategory belongs to category), False otherwise
        """
        # If no subcategory specified, it's valid
        if not subcategory_id:
            return False

        # If subcategory is specified but no category, it's invalid
        if not category_id:
            return False

        # Find the category in the list
        category = next((cat for cat in categories if cat.get("id") == category_id), None)
        if not category:
            return False

        # Check if subcategory exists in the category's subcategories
        subcategories = category.get("subcategories", [])
        subcategory_ids = [sub.get("id") for sub in subcategories]

        return subcategory_id in subcategory_ids

    def convert_ai_result_to_transaction(self, ai_result: dict, default_account: Optional[Account] = None) -> dict:
        """Convert AI analysis result to transaction format"""
        # Generate a unique transaction ID if none provided
        tx_id = ai_result.get("id", f"tx-{secrets.token_urlsafe(4)}")

        # Extract account ID if present
        account = ai_result.get("account", {})
        account_id = account.get("id") if isinstance(account, dict) else None
        account_name = account.get("name") if isinstance(account, dict) else None

        # If no account was found and we have a default account, use it
        if not account_id and default_account:
            account_id = default_account.id
            account_name = default_account.name

        # Extract category and subcategory IDs if present
        category = ai_result.get("category", {})
        category_id = category.get("id") if isinstance(category, dict) else None
        category_name = category.get("name") if isinstance(category, dict) else None

        subcategory = ai_result.get("subcategory", {})
        subcategory_id = subcategory.get("id") if isinstance(subcategory, dict) else None
        subcategory_name = subcategory.get("name") if isinstance(subcategory, dict) else None

        # Extract place ID if present
        place = ai_result.get("place", {})
        place_id = place.get("id") if isinstance(place, dict) else None
        place_name = place.get("name") if isinstance(place, dict) else None

        # Extract from_account and to_account for transfers
        from_account = ai_result.get("from_account", {})
        from_account_id = from_account.get("id") if isinstance(from_account, dict) else None
        from_account_name = from_account.get("name") if isinstance(from_account, dict) else None

        to_account = ai_result.get("to_account", {})
        to_account_id = to_account.get("id") if isinstance(to_account, dict) else None
        to_account_name = to_account.get("name") if isinstance(to_account, dict) else None

        # Validate and normalize the type field
        raw_type = ai_result.get("type", "").strip().lower() if ai_result.get("type") else ""
        valid_types = {t.value for t in TransactionType}
        tx_type = raw_type if raw_type in valid_types else TransactionType.EXPENSE

        # Build the transaction object
        transaction = {
            "id": f"{tx_id}-{secrets.token_urlsafe(2)}",
            "type": tx_type,
            "amount": float(ai_result.get("amount", 0)),
            "category": category_name,
            "category_id": category_id,
            "subcategory": subcategory_name,
            "subcategory_id": subcategory_id,
            "place": place_name,
            "place_id": place_id,
            "description": ai_result.get("description"),
            "account": account_name,
            "account_id": account_id,
            "from_account": from_account_name,
            "from_account_id": from_account_id,
            "to_account": to_account_name,
            "to_account_id": to_account_id,
        }

        # Handle date conversion
        date_str = ai_result.get("date")
        if date_str:
            try:
                transaction["date"] = datetime.strptime(date_str, "%Y-%m-%d").date()
            except:
                transaction["date"] = datetime.now().date()
        else:
            transaction["date"] = datetime.now().date()

        return transaction
