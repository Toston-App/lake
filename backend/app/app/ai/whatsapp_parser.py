import json
import logging
import secrets
from datetime import date, datetime
from typing import Any, Optional

from openai import AsyncOpenAI, RateLimitError
from pydantic import BaseModel

from app.ai.ocr import TransactionType

logging.basicConfig(
    filename="whatsapp_requests.log",
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class WhatsAppMessage(BaseModel):
    """Model for WhatsApp message data"""
    message: str
    from_number: str
    timestamp: Optional[datetime] = None


class WhatsAppParser:
    """Class to parse WhatsApp messages and extract transaction data"""

    def __init__(self, api_key: str = None):
        """Initialize the parser with OpenAI API key"""
        self.client = AsyncOpenAI(api_key=api_key) if api_key else None

    async def analyze_with_ai(self, message: str, categories, places, accounts) -> dict:
        """
        Analyze WhatsApp message using OpenAI to extract transaction information
        """
        if not self.client:
            raise ValueError("OpenAI client not initialized. Please provide an API key.")

        prompt = f"""You are an assistant in a personal finance app. Parse the following message about a financial transaction and extract the relevant information.

        Rules:
        - type: Categorize as 'expense' (default), 'income', or 'transfer'
        - amount: Extract the numerical amount as a float
        - date: Extract date in YYYY-MM-DD format. If relative dates are mentioned (today, yesterday, etc.), calculate the actual date ({date.today()})
        - category: Match the best category based on the description from this list: {categories}. Respond with the id and name of the category or null if not applicable.
        - subcategory: Match to an appropriate subcategory based on the category. Respond with the id and name of the subcategory or null if not applicable.
        - place: Match the transaction location to the most appropriate place, using the provided list: {places}. Return the id and name ONLY if there's a clear match in the provided list, otherwise return null.
        - description: Brief description in Spanish of what the transaction was for.
        - account: Identify the payment method or account STRICTLY from the provided list: {accounts}. Only return the id and name if there's an EXACT or VERY CLOSE match (like "bbva" matching "bbva débito"). If the account mentioned is not in the provided list (like "santander" when santander isn't in the list), return null.
        - id: Short (max 10 chars) unique identifier for the transaction with text divided by dashes

        Examples of incoming messages:
        - "2000 pesos cena de antes de ayer"
        - "154.04 en al super despensa con bbva"
        - "ingreso 1800 nomina"
        - "cuenta nu 249 autozone"

        Do not attempt fuzzy matching for accounts or places. Only return a match if you are highly confident it's the correct one from the provided lists.

        Respond with a single valid JSON object containing all extracted fields. Use null for any fields you cannot determine.
        """

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "user",
                        "content": f"{prompt}\n\nMessage to parse: \"{message}\""
                    }
                ],
                max_tokens=1000,
            )
            return json.loads(response.choices[0].message.content)
        except RateLimitError as e:
            if "insufficient_quota" in str(e):
                raise ValueError("Insufficient OpenAI API credits")
            raise ValueError("OpenAI rate limit exceeded")
        except Exception as e:
            logging.error(f"Error analyzing message with OpenAI: {str(e)}")
            raise ValueError(f"Error analyzing message with OpenAI: {str(e)}")

    async def parse_message(
        self,
        message: str,
        categories: list[dict[str, Any]] = None,
        places: list[dict[str, Any]] = None,
        accounts: list[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """
        Parse WhatsApp message to extract transaction information
        """
        if not message or not message.strip():
            logging.warning("Empty message received for parsing")
            return {}

        try:
            if self.client:
                ai_result = await self.analyze_with_ai(message, categories, places, accounts)
                if not ai_result:
                    logging.warning(f"AI analysis produced empty result for message: {message}")
                    return {}

                transaction = self.convert_ai_result_to_transaction(ai_result)

                # Validate the transaction has minimal required data
                if not self.validate_transaction(transaction):
                    logging.warning(f"Parsed transaction failed validation: {transaction}")
                    return {}

                return transaction
            else:
                logging.error("No OpenAI client available for message parsing")
                return {}
        except Exception as ai_error:
            logging.warning(f"AI analysis failed: {str(ai_error)}")
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

    def convert_ai_result_to_transaction(self, ai_result: dict) -> dict:
        """Convert AI analysis result to transaction format"""
        # Generate a unique transaction ID if none provided
        tx_id = ai_result.get("id", f"tx-{secrets.token_urlsafe(4)}")

        # Extract account ID if present
        account = ai_result.get("account", {})
        account_id = account.get("id") if isinstance(account, dict) else None
        account_name = account.get("name") if isinstance(account, dict) else None

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

        # Build the transaction object
        transaction = {
            "id": tx_id,
            "type": ai_result.get("type", TransactionType.EXPENSE),
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
