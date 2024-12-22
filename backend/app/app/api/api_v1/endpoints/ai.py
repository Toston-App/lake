from typing import Any

import tempfile
import os
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
import filetype

from app import crud, models, schemas
from app.api import deps
from app.ai.ocr import OCRHelper
from app.core.config import settings

router = APIRouter()
ocr = OCRHelper(settings.OPENAI_API_KEY)


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
    validate_file_type(image)

    try:
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            contents = await image.read()
            temp_file.write(contents)
            temp_file.flush()

            transaction = await ocr.analyze_image(temp_file.name)
            parsed_transaction = await ocr.parse_response(db=db, owner_id=current_user.id, response_text=transaction)

            # Remove temporary file
            os.unlink(temp_file.name)
            return parsed_transaction
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=e
        )