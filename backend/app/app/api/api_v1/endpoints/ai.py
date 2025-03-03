from typing import Any

import os
import tempfile
import filetype
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, status

from app import crud, models, schemas
from app.api import deps
from app.ai.ocr import OCRHelper
from app.core.config import settings

router = APIRouter()
ocr = OCRHelper(settings.OPENAI_API_KEY)

logging.basicConfig(
    filename='ocr_requests.log',
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# TODO: Validate size: https://github.com/fastapi/fastapi/issues/362
def validate_file_type(file: UploadFile) -> None:
    # FILE_SIZE = 2097152 # 2MB

    accepted_file_types = ["image/png", "image/jpeg", "image/jpg", "image/heic", "image/heif", "image/heics", "png",
                          "jpeg", "jpg", "heic", "heif", "heics"]
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
    logging.info(f"OCR request received - User ID: {current_user.id} - File: {image.filename}")

    validate_file_type(image)

    try:
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            contents = await image.read()
            temp_file.write(contents)
            temp_file.flush()

            try:
                transaction = await ocr.analyze_image(temp_file.name)

                if transaction == "Insufficient API credits":
                    logging.info(f"OCR request failed - User ID: {current_user.id} - File: {image.filename} - Error: Insufficient API credits")
                    raise HTTPException(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        detail="Insufficient API credits",
                    )

                parsed_transaction = await ocr.parse_response(db=db, owner_id=current_user.id, response_text=transaction)
                logging.info(f"OCR request completed - User ID: {current_user.id} - File: {image.filename}")
                return parsed_transaction

            finally:
                # Remove temporary file
                os.unlink(temp_file.name)

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"OCR request failed - User ID: {current_user.id} - File: {image.filename} - Error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )