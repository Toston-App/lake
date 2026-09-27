import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from openai import AsyncOpenAI, RateLimitError
from pydantic import BaseModel

from app.ai.ocr import TransactionType
from app.schemas.account import Account

PROMPT_TEMPLATE = """You are an assistant in a personal finance app. Parse the following message about a financial transaction and extract the relevant information.

CRITICAL REQUIREMENTS:
- You MUST always return a valid JSON object
- The 'type' field is REQUIRED and MUST be one of: 'expense', 'income', or 'transfer'
- The 'amount' field is REQUIRED and must be a positive number

        Rules:
        - type: **REQUIRED** - MUST be one of: 'expense', 'income', or 'transfer'. This field cannot be null or omitted.
        - amount: Extract the numerical amount as a float
        - date: Extract date in YYYY-MM-DD format. If relative dates are mentioned (today, yesterday, etc.), calculate the actual date ({today})
        - category: Match the best category based on the description from this list: {categories}. Respond with the id and name of the category or null if not applicable.
        - subcategory: **CRITICAL** - The subcategory MUST belong to the selected category. Each category has a list of subcategories. You can ONLY choose a subcategory from the "subcategories" array of the selected category. If the selected category doesn't have an appropriate subcategory in its list, return null. Respond with the id and name of the subcategory or null.
        - place: Match the transaction location to the most appropriate place, using the provided list: {places}. Return the id and name ONLY if there's a clear match in the provided list, otherwise return null.
        - description: Brief description in Spanish of what the transaction was for.
        - account: Identify the payment method or account STRICTLY from the provided list: {accounts}. Only return the id and name if there's an EXACT or VERY CLOSE match (like "bbva" matching "bbva débito"). If the account mentioned is not in the provided list (like "santander" when santander isn't in the list), return null.
        - from_account: For transfers, identify the source account from the provided list: {accounts}. Only return the id and name if there's an EXACT or VERY CLOSE match.
        - to_account: For transfers, identify the destination account from the provided list: {accounts}. Only return the id and name if there's an EXACT or VERY CLOSE match.
        - id: Short (max 10 chars) unique identifier for the transaction with text divided by dashes

        Examples of incoming messages:
        - "2000 pesos cena de antes de ayer"
        - "154.04 en al super despensa con bbva"
        - "ingreso 1800 nomina"
        - "cuenta nu 249 autozone"
        - "transferir 500 de bbva a santander"
        - "pasar 1000 de efectivo a tarjeta de credito"

        IMPORTANT: When selecting a subcategory, verify it exists in the selected category's subcategories array. For example:
        - If you select category "Compras" with id 5, you can only choose subcategories that appear in categories[where id=5].subcategories
        - If you select category "Alimentación" with id 3, you can only choose subcategories from categories[where id=3].subcategories
        - Never mix subcategories from different categories

        Do not attempt fuzzy matching for accounts or places. Only return a match if you are highly confident it's the correct one from the provided lists.

        Respond with a single valid JSON object containing all extracted fields. Use null for any fields you cannot determine, EXCEPT for 'type' and 'amount' which are REQUIRED and must always be present.
        """

# Changes automatically whenever the prompt is edited, so LLM quality can be
# compared across prompt revisions in Axiom.
PROMPT_VERSION = hashlib.sha256(PROMPT_TEMPLATE.encode()).hexdigest()[:12]


class WhatsAppMessage(BaseModel):
    """Model for WhatsApp message data"""
    message: str
    from_number: str
    timestamp: Optional[datetime] = None


@dataclass
class ParseResult:
    """Outcome of parsing a message, plus a trace of what the LLM did.

    ``transaction`` is empty when the message could not be parsed, in which
    case ``trace["failure_reason"]`` says why.
    """
    transaction: dict[str, Any]
    trace: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.transaction)


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

    async def analyze_with_ai(self, message: str, categories, places, accounts, trace: dict[str, Any]) -> dict:
        """
        Analyze WhatsApp message using OpenRouter to extract transaction information.

        Fills ``trace`` with the model used, token usage, latency and raw output.
        """
        if not self.client:
            raise ValueError("OpenRouter client not initialized. Please provide an API key.")

        prompt = PROMPT_TEMPLATE.format(
            today=date.today(),
            categories=categories,
            places=places,
            accounts=accounts,
        )

        # Build extra_body with fallback models if configured
        extra_body = None
        if self.fallback_models:
            extra_body = {"models": self.fallback_models}

        start = time.monotonic()
        try:
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
        except RateLimitError as e:
            if "insufficient_quota" in str(e):
                raise ValueError("Insufficient OpenRouter API credits")
            raise ValueError("OpenRouter rate limit exceeded")
        except Exception as e:
            raise ValueError(f"Error analyzing message with OpenRouter: {str(e)}")
        finally:
            trace["latency_ms"] = round((time.monotonic() - start) * 1000, 2)

        choice = response.choices[0]
        raw_output = choice.message.content
        usage = response.usage
        trace.update(
            model_used=response.model,
            generation_id=response.id,
            finish_reason=choice.finish_reason,
            raw_output=raw_output,
            usage={
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            } if usage else None,
        )

        try:
            return json.loads(raw_output)
        except (TypeError, json.JSONDecodeError) as e:
            trace["failure_reason"] = "invalid_json"
            raise ValueError(f"LLM returned invalid JSON: {str(e)}")

    async def parse_message(
        self,
        message: str,
        categories: list[dict[str, Any]] = None,
        places: list[dict[str, Any]] = None,
        accounts: list[dict[str, Any]] = None,
        default_account: Optional[Account] = None
    ) -> ParseResult:
        """
        Parse WhatsApp message to extract transaction information
        """
        trace: dict[str, Any] = {
            "provider": "openrouter",
            "model_requested": self.model,
            "fallback_models": self.fallback_models,
            "prompt_version": PROMPT_VERSION,
            "context_items": {
                "categories": len(categories or []),
                "places": len(places or []),
                "accounts": len(accounts or []),
            },
            "adjustments": [],
        }

        def fail(reason: str) -> ParseResult:
            trace.setdefault("failure_reason", reason)
            return ParseResult(transaction={}, trace=trace)

        if not message or not message.strip():
            return fail("empty_message")

        if not self.client:
            return fail("no_client")

        try:
            ai_result = await self.analyze_with_ai(message, categories, places, accounts, trace)
        except ValueError as e:
            trace["error"] = str(e)
            return fail("llm_error")

        trace["ai_result"] = ai_result
        if not ai_result:
            return fail("empty_result")

        try:
            transaction = self.convert_ai_result_to_transaction(ai_result, default_account)
        except (TypeError, ValueError, AttributeError) as e:
            trace["error"] = f"{type(e).__name__}: {e}"
            return fail("conversion_error")

        if default_account and transaction.get("account_id") == default_account.id and not _ref_id(ai_result.get("account")):
            trace["adjustments"].append("default_account_applied")

        # Validate the transaction has minimal required data
        if not self.validate_transaction(transaction):
            return fail("validation_failed")

        # Validate subcategory belongs to category
        if not self.validate_subcategory_belongs_to_category(
            transaction.get("category_id"),
            transaction.get("subcategory_id"),
            categories or []
        ):
            if transaction.get("subcategory_id"):
                trace["adjustments"].append("subcategory_not_in_category")
            # Remove the invalid subcategory but keep the transaction
            transaction["subcategory_id"] = None
            transaction["subcategory"] = None

        # If there's a category but no subcategory, remove the category
        if transaction.get("category_id") and not transaction.get("subcategory_id"):
            trace["adjustments"].append("category_dropped_without_subcategory")
            transaction["category_id"] = None
            transaction["category"] = None

        return ParseResult(transaction=transaction, trace=trace)

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

        # Build the transaction object
        transaction = {
            "id": f"{tx_id}-{secrets.token_urlsafe(2)}",
            "type": ai_result.get("type") or TransactionType.EXPENSE,
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
            except (TypeError, ValueError):
                transaction["date"] = datetime.now().date()
        else:
            transaction["date"] = datetime.now().date()

        return transaction


def _ref_id(value: Any) -> Any:
    """Return the ``id`` of an ``{id, name}`` reference returned by the LLM."""
    return value.get("id") if isinstance(value, dict) else None
