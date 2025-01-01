import base64
import json
from enum import Enum
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.encoders import jsonable_encoder
from openai import AsyncOpenAI, RateLimitError


from app import crud, models, schemas
from app.utilities.matcher import find_cat_match, find_subcat_match


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
            return base64.b64encode(image_file.read()).decode('utf-8')

    async def analyze_image(self, image_path: str, model: str = "gpt-4o-mini", json: bool = True) -> str:
        """Analyze image using OpenAI Vision API"""
        base64_image = self.encode_image(image_path)

        prompt = """You are a bot in a finance app and your responsibility is to help with automatic transaction details.
        Please analyze the attached image and give me all the transactions on it. The rules are:
        - Aim for all the transactions on the image.
        - The amount must be included as a number, converted to MXN if needed.
        - The date must be in the format of YYYY-MM-DD. If the date is not available, you can leave it empty. If is a supermarket ticket, date is generally at the bottom of the ticket.
        - The place must be the name of the place where the transaction happened.
        - The description must be a short text describing the transaction but keep the name of the item bought in sentence case. If is possible, try to include the quantity and the price of the item.
        - Category (categorize as "Compras","Alimentos y bebidas","Seguros","Vivienda","Vehículos","Transporte","Vida y entretenimiento","Salud y deporte","Suscripciones y servicios","Gastos financieros","Inversiones","Ingresos" or empty if not applicable. Only use one category each time).
        - Subcategory match each item to its subcategory based on general knowledge.
        - Type (categorize as 'expense', 'income', or 'transfer').
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
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1000
            )
            return response.choices[0].message.content
        except RateLimitError as e:
            if "insufficient_quota" in str(e):
                return "Insufficient API credits"
            return "Rate limit exceeded"
        except Exception as e:
            raise Exception(f"Error analyzing image: {str(e)}")

    async def parse_response(self, db: AsyncSession, owner_id: int, response_text: str,) -> Dict[str, Any]:
        """Parse OpenAI response and match categories with synonyms"""
        try:
            user_categories = jsonable_encoder(await crud.category.get_multi_by_owner(db=db, owner_id=owner_id))
            transactions = json.loads(response_text)["transactions"]
            count = 0

            for transaction in transactions:
                # add id
                transaction["id"] = count
                count += 1

                if "category" in transaction:
                    cat_match = find_cat_match(transaction["category"], user_categories)
                    transaction["category_id"] = cat_match['id'] if cat_match else None

                if "subcategory" in transaction:
                    subcat_match = find_subcat_match(transaction["subcategory"], transaction["category"], user_categories)

                    if subcat_match is None and transaction["category_id"]:
                        for cat in user_categories:
                            if cat["id"] == transaction["category_id"]:
                                subcat_match = cat['subcategories'][0]
                                break


                    transaction["subcategory_id"] = subcat_match['id'] if subcat_match else None

                # Ensure amount is float
                if "amount" in transaction:
                    transaction["amount"] = float(str(transaction["amount"]).replace(",", ""))

                # Parse date string to datetime
                if "date" in transaction and transaction["date"]:
                    try:
                        transaction["date"] = datetime.strptime(transaction["date"], "%Y-%m-%d").date()
                    except:
                        transaction["date"] = None

            return transactions
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse response: {str(e)}")
