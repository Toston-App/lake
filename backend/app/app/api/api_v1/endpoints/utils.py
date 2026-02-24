from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic.networks import EmailStr

from app import models, schemas
from app.api import deps
from app.utils import send_test_email
from app.utilities.wide_events import enrich_event

router = APIRouter()


@router.post("/test-email/", response_model=schemas.Msg, status_code=201)
def test_email(
    request: Request,
    email_to: EmailStr,
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    """
    Test emails.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email, "is_superuser": True},
        operation={"type": "test_email", "email_to": email_to},
    )

    send_test_email(email_to=email_to)
    return {"msg": "Test email sent"}
