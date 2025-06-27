from pydantic import BaseModel, Field
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.ai.mine import agent

from app import models
from app.api import deps


router = APIRouter()


class ChatResponse(BaseModel):
    """Chat response model for the API"""
    response: str = Field(description="Agent's response")
    success: bool = Field(description="Whether the request was successful")
    error: Optional[str] = Field(description="Error message if any", default=None) 

class ChatMessage(BaseModel):
    """Chat message model for the API"""
    message: str = Field(description="User's message")

@router.post("/message", response_model=ChatResponse)
async def chat_message(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    chat_message: ChatMessage,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Process a chat message and return a response from the financial assistant.
    Compatible with Vercel AI SDK.
    """

    try:
        result = await agent.run(chat_message.message)
        print("🚀 ~ result:", result)

        return ChatResponse(
            response=result.output,
            success=True
        )

    except Exception as e:
        return ChatResponse(
            response="I apologize, but I encountered an error while processing your request. Please try again.",
            success=False,
            error=str(e)
        )

