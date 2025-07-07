import asyncio
import os
import tempfile
from typing import Any

import filetype
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.ai.ocr import OCRHelper
from app.api import deps
from app.core.config import settings
from app.utilities.logger import setup_logger
from app.utilities.simplifier import categories as simplify_categories
from app.utilities.simplifier import places as simplify_places
from app.ai.financial_agent_router import router as financial_agent_router


router = APIRouter()
logger = setup_logger("ocr_requests", "ocr_requests.log")
ocr = OCRHelper(settings.OPENAI_API_KEY)


# TODO: Validate size: https://github.com/fastapi/fastapi/issues/362
def validate_file_type(file: UploadFile) -> None:
    # FILE_SIZE = 2097152 # 2MB

    accepted_file_types = [
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/heic",
        "image/heif",
        "image/heics",
        "png",
        "jpeg",
        "jpg",
        "heic",
        "heif",
        "heics",
    ]
    file_info = filetype.guess(file.file)
    if file_info is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unable to determine file type",
        )

    detected_content_type = file_info.extension.lower()

    if (
        file.content_type not in accepted_file_types
        or detected_content_type not in accepted_file_types
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type",
        )


@router.post("/ocr", response_model=Any)
async def ocr_image(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    image: UploadFile = File(...),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    logger.info(
        f"OCR request received - User ID: {current_user.id} - File: {image.filename}"
    )

    validate_file_type(image)

    try:
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            contents = await image.read()
            temp_file.write(contents)
            temp_file.flush()

            try:
                places_task =  crud.place.get_multi_by_owner(db=db, owner_id=current_user.id)
                categories_task =  crud.category.get_multi_by_owner(db=db, owner_id=current_user.id)

                (places, categories) = await asyncio.gather(places_task, categories_task)

                transaction = await ocr.analyze_image(temp_file.name, simplify_categories(categories), simplify_places(places))

                if transaction == "Insufficient API credits":
                    logger.info(
                        f"OCR request failed - User ID: {current_user.id} - File: {image.filename} - Error: Insufficient API credits"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        detail="Insufficient API credits",
                    )

                parsed_transaction = await ocr.parse_response(
                    db=db, owner_id=current_user.id, response_text=transaction
                )
                logger.info(
                    f"OCR request completed - User ID: {current_user.id} - File: {image.filename}"
                )
                return parsed_transaction

            finally:
                # Remove temporary file
                os.unlink(temp_file.name)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"OCR request failed - User ID: {current_user.id} - File: {image.filename} - Error: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )

router.include_router(financial_agent_router, tags=["Financial Agent"])
