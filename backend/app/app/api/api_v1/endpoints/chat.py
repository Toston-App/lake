from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import json
import asyncio

from app import models
from app.api import deps
from app.ai.financial_agent import FinancialAgent, ChatMessage, ChatResponse
from app.utilities.logger import setup_logger

router = APIRouter()
logger = setup_logger("chat_requests", "chat_requests.log")


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
    logger.info(f"Chat request received - User ID: {current_user.id} - Message: {chat_message.message[:100]}...")
    
    try:
        # Initialize the financial agent
        agent = FinancialAgent(db, current_user.id)
        
        # Process the message
        response = await agent.chat(chat_message.message)
        
        logger.info(f"Chat request completed - User ID: {current_user.id}")
        
        return ChatResponse(
            response=response,
            success=True
        )
        
    except Exception as e:
        logger.error(f"Chat request failed - User ID: {current_user.id} - Error: {str(e)}")
        return ChatResponse(
            response="I apologize, but I encountered an error while processing your request. Please try again.",
            success=False,
            error=str(e)
        )


@router.post("/stream")
async def chat_stream(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    chat_message: ChatMessage,
    current_user: models.User = Depends(deps.get_current_active_user),
):
    """
    Stream chat responses for real-time interaction.
    Compatible with Vercel AI SDK streaming.
    """
    logger.info(f"Streaming chat request received - User ID: {current_user.id} - Message: {chat_message.message[:100]}...")
    
    async def generate_stream():
        try:
            # Initialize the financial agent
            agent = FinancialAgent(db, current_user.id)
            
            # Process the message
            response = await agent.chat(chat_message.message)
            
            # Stream the response in chunks for real-time display
            chunk_size = 50
            for i in range(0, len(response), chunk_size):
                chunk = response[i:i + chunk_size]
                yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
                await asyncio.sleep(0.05)  # Small delay for smooth streaming
            
            # Send completion signal
            yield f"data: {json.dumps({'content': '', 'done': True})}\n\n"
            
            logger.info(f"Streaming chat request completed - User ID: {current_user.id}")
            
        except Exception as e:
            logger.error(f"Streaming chat request failed - User ID: {current_user.id} - Error: {str(e)}")
            error_response = f"I apologize, but I encountered an error while processing your request: {str(e)}"
            yield f"data: {json.dumps({'content': error_response, 'done': True, 'error': str(e)})}\n\n"
    
    return StreamingResponse(
        generate_stream(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
        }
    )


@router.get("/capabilities")
async def get_chat_capabilities(
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get information about what the financial assistant can do.
    """
    capabilities = {
        "capabilities": [
            {
                "name": "Spending Analysis",
                "description": "Analyze spending patterns for different time periods",
                "examples": [
                    "Show me my spending for this month",
                    "What did I spend most on last week?",
                    "How much did I spend this year?"
                ]
            },
            {
                "name": "Category Tracking",
                "description": "Track spending by specific categories",
                "examples": [
                    "How much have I spent on groceries this month?",
                    "Show me my restaurant spending for last week",
                    "What's my entertainment spending this year?"
                ]
            },
            {
                "name": "Trend Analysis",
                "description": "Analyze spending trends over time",
                "examples": [
                    "Show me my spending trends over the last 3 months",
                    "Which categories are increasing?",
                    "How has my spending changed this year?"
                ]
            },
            {
                "name": "Account Balances",
                "description": "Get information about account balances",
                "examples": [
                    "What's my current balance across all accounts?",
                    "Show me my savings account balance",
                    "What's the balance in my checking account?"
                ]
            }
        ],
        "supported_periods": [
            "this month",
            "last month", 
            "this week",
            "last week",
            "this year"
        ]
    }
    
    return capabilities


@router.post("/test")
async def test_chat_endpoint(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Test endpoint to verify the chat functionality is working.
    """
    test_message = "Show me my spending for this month"
    
    try:
        agent = FinancialAgent(db, current_user.id)
        response = await agent.chat(test_message)
        
        return {
            "status": "success",
            "test_message": test_message,
            "response": response,
            "user_id": current_user.id
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "user_id": current_user.id
        }
