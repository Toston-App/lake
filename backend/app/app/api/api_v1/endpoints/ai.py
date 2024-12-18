from typing import Any

import tempfile
import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.ai.ocr import OCRHelper
from app.core.config import settings

router = APIRouter()
ocr = OCRHelper(settings.OPENAI_API_KEY)


@router.post("/ocr", response_model=Any)
async def ocr_image(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    image: UploadFile = File(...),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    if not image.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File uploaded is not an image"
        )

    try:
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            contents = await image.read()
            temp_file.write(contents)
            temp_file.flush()

            transaction = await ocr.analyze_image(temp_file.name)
            print("🚀 ~ transaction:", transaction)
            # transaction = '{\n  "transactions": [\n    {\n      "amount": 430.00,\n      "date": "2024-12-15",\n      "place": "Camarones Vvalos",\n      "description": "Hamburguesa de pescado x1, Tostada de ceviche x2",\n      "category": "Alimentos y bebidas",\n      "subcategory": "Comida rápida",\n      "type": "expense"\n    }\n  ]\n}'

            parsed_transaction = await ocr.parse_response(db=db, owner_id=current_user.id, response_text=transaction)
            print("🚀 ~ parsed_transaction:", parsed_transaction)

            return parsed_transaction

            # # Create transaction in database
            # db_transaction = await crud.transaction.create(
            #     db=db,
            #     obj_in=schemas.TransactionCreate(
            #         user_id=current_user.id,
            #         type=transaction.type,
            #         amount=transaction.amount,
            #         date=transaction.date or datetime.now(),
            #         category=transaction.category,
            #         subcategory=transaction.subcategory,
            #         place=transaction.place,
            #         description=transaction.description
            #     )
            # )

            # Clean up temp file
            os.unlink(temp_file.name)

            # return db_transaction
    except Exception as e:
        print("🚀 ~ e:", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=e
        )