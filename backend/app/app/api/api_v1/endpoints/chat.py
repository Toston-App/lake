from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import json
import asyncio

from app import models
from app.api import deps
from app.ai.financial_agent import FinancialAgent, ChatMessage, ChatResponse
from app.ai.transaction_parser import TransactionParser
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


@router.post("/transaction")
async def create_transaction(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    chat_message: ChatMessage,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create a transaction (expense, income, or transfer) from natural language input.
    """
    logger.info(f"Transaction creation request received - User ID: {current_user.id} - Message: {chat_message.message[:100]}...")
    
    try:
        # Initialize the transaction parser
        parser = TransactionParser(db, current_user.id)
        
        # Parse and create the transaction
        parsed_transaction = await parser.parse_transaction(chat_message.message)
        result = await parser.create_transaction(parsed_transaction)
        
        # Format the response message
        if parsed_transaction.transaction_type == "expense":
            message = f"✅ Expense recorded: ${result['amount']:.2f} for {result['description']}"
        elif parsed_transaction.transaction_type == "income":
            message = f"✅ Income recorded: ${result['amount']:.2f} for {result['description']}"
        elif parsed_transaction.transaction_type == "transfer":
            message = f"✅ Transfer recorded: ${result['amount']:.2f} from {result['from_account']} to {result['to_account']}"
        else:
            message = f"✅ Transaction recorded: ${result['amount']:.2f} for {result['description']}"
        
        logger.info(f"Transaction creation completed - User ID: {current_user.id} - Type: {parsed_transaction.transaction_type}")
        
        return {
            "success": True,
            "message": message,
            "transaction": result,
            "transaction_type": parsed_transaction.transaction_type
        }
        
    except Exception as e:
        logger.error(f"Transaction creation failed - User ID: {current_user.id} - Error: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "message": f"I couldn't create the transaction: {str(e)}"
        }


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
            },
            {
                "name": "Quick Expense Entry",
                "description": "Quickly add expenses using natural language",
                "examples": [
                    "Add $25 for lunch at McDonald's",
                    "Record $150 gas expense",
                    "Add $50 for groceries",
                    "Record $30 for coffee"
                ]
            },
            {
                "name": "Income Recording",
                "description": "Record income transactions using natural language",
                "examples": [
                    "Add $2000 salary deposit",
                    "Record $500 freelance payment",
                    "Add $1000 bonus",
                    "Record $200 refund"
                ]
            },
            {
                "name": "Transfer Tracking",
                "description": "Track transfers between accounts using natural language",
                "examples": [
                    "Transfer $500 from checking to savings",
                    "Move $100 to crypto wallet",
                    "Transfer $200 from savings to checking",
                    "Move $50 to emergency fund"
                ]
            }
        ],
        "supported_periods": [
            "this month",
            "last month", 
            "this week",
            "last week",
            "this year"
        ],
        "transaction_types": [
            "expense",
            "income", 
            "transfer"
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


@router.post("/test-transaction")
async def test_transaction_endpoint(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Test endpoint to verify the transaction creation functionality is working.
    """
    test_message = "Add $25 for lunch at McDonald's"
    
    try:
        parser = TransactionParser(db, current_user.id)
        parsed_transaction = await parser.parse_transaction(test_message)
        
        return {
            "status": "success",
            "test_message": test_message,
            "parsed_transaction": {
                "type": parsed_transaction.transaction_type,
                "amount": parsed_transaction.amount,
                "description": parsed_transaction.description,
                "date": parsed_transaction.date,
                "account_id": parsed_transaction.account_id,
                "category_id": parsed_transaction.category_id,
                "place_id": parsed_transaction.place_id
            },
            "user_id": current_user.id
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "user_id": current_user.id
        }
