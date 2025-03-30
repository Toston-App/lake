import base64
import json
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from openai import AsyncOpenAI, RateLimitError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


class TransactionType(str, Enum):
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"


class Transaction(BaseModel):
    type: TransactionType
    amount: float
    date: Optional[datetime]
    category: Optional[str]
    subcategory: Optional[str]
    place: Optional[str]
    description: Optional[str]


class OCRHelper:
    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key)

    def encode_image(self, image_path: str) -> str:
        """Convert image to base64 string"""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

    async def analyze_image(
        self, image_path: str, categories, places, model: str = "gpt-4o-mini", json: bool = True
    ) -> str:
        """Analyze image using OpenAI Vision API"""
        base64_image = self.encode_image(image_path)

        prompt = f"""You are an assistant in a personal finance app. Parse the following image about a financial transaction and extract the relevant information.

        Rules:
        - Aim for all the transactions on the image.
        - type: Categorize as 'expense' (default), 'income', or 'transfer'
        - amount: Extract the numerical amount as a float
        - date: Extract date in YYYY-MM-DD format. If relative dates are mentioned (today, yesterday, etc.), calculate the actual date ({date.today()})
        - category: Match the best category based on the description from this list: {categories}. Respond with the id and name of the category or null if not applicable. If type is income, search for `is_income: True` in the category list I provided.
        - subcategory: Match to an appropriate subcategory based on the category you matched. ALWAYS respond with the id and name of the subcategory or null if you didn't find a category match. For income transactions, search for `is_income: True` in the categories list I provided.
        - description: Brief description in Spanish of what the transaction was for.
        - place: Match the transaction location to the most appropriate place in base the description, using the provided list: {places}. Return the id and name ONLY if there's a clear match in the provided list, otherwise return null.

        You must respond in valid JSON with the key "transactions" and the value is list of the transactions. Don't wrap the response in a markdown code."""

        try:
            response = await self.client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"} if json else None,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                },
                            },
                        ],
                    }
                ],
                max_tokens=1000,
            )
            return response.choices[0].message.content
        except RateLimitError as e:
            if "insufficient_quota" in str(e):
                return "Insufficient API credits"
            return "Rate limit exceeded"
        except Exception as e:
            raise Exception(f"Error analyzing image: {str(e)}")

    async def parse_response(
        self,
        db: AsyncSession,
        owner_id: int,
        response_text: str,
    ) -> dict[str, Any]:
        """Parse OpenAI response and match categories with synonyms"""
        try:
            transactions = json.loads(response_text)["transactions"]
            count = 0

            for transaction in transactions:
                # add id
                transaction["id"] = count
                count += 1

                # add made_from ocr
                transaction["made_from"] = "OCR";

                # Ensure amount is float
                if "amount" in transaction:
                    transaction["amount"] = abs(float(
                        str(transaction["amount"]).replace(",", "")
                    ))

                # Parse date string to datetime
                if "date" in transaction and transaction["date"]:
                    try:
                        transaction["date"] = datetime.strptime(
                            transaction["date"], "%Y-%m-%d"
                        ).date()
                    except:
                        transaction["date"] = None

                # Extract category and subcategory IDs if present
                category = transaction.get("category", {})
                category_id = category.get("id") if isinstance(category, dict) else None
                category_name = category.get("name") if isinstance(category, dict) else None

                subcategory = transaction.get("subcategory", {})
                subcategory_id = subcategory.get("id") if isinstance(subcategory, dict) else None
                subcategory_name = subcategory.get("name") if isinstance(subcategory, dict) else None

                if category_id and category_name:
                    transaction["category_id"] = category_id
                    transaction["category_name"] = category_name

                if subcategory_id and subcategory_name:
                    transaction["subcategory_id"] = subcategory_id
                    transaction["subcategory_name"] = subcategory_name

                place = transaction.get("place", {})
                place_id = place.get("id") if isinstance(place, dict) else None
                place_name = place.get("name") if isinstance(place, dict) else None

                if place_id and place_name:
                    transaction["place_id"] = place_id
                    transaction["place_name"] = place_name

            return transactions
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse response: {str(e)}")
