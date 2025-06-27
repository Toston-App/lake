import re
from datetime import datetime, timedelta, date as date_type
from typing import Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.crud.crud_expense import expense
from app.crud.crud_income import income
from app.crud.crud_transfer import transfer
from app.crud.crud_account import account
from app.crud.crud_category import category
from app.crud.crud_place import place
from app.models.account import Account
from app.models.category import Category
from app.models.place import Place


class ParsedTransaction(BaseModel):
    """Parsed transaction details from natural language"""
    transaction_type: str = Field(description="Type of transaction: expense, income, or transfer")
    amount: float = Field(description="Transaction amount")
    description: str = Field(description="Transaction description")
    date: date_type | None = Field(description="Transaction date", default=None)
    account_id: int | None = Field(description="Account ID", default=None)
    category_id: int | None = Field(description="Category ID", default=None)
    place_id: int | None = Field(description="Place ID", default=None)
    from_account_id: int | None = Field(description="From account ID for transfers", default=None)
    to_account_id: int | None = Field(description="To account ID for transfers", default=None)


class TransactionParser:
    """Parse natural language input to extract transaction details"""
    
    def __init__(self, db: AsyncSession, user_id: int):
        self.db = db
        self.user_id = user_id
        
        # Common expense keywords
        self.expense_keywords = [
            'expense', 'spent', 'paid', 'bought', 'purchased', 'cost', 'lunch', 'dinner',
            'breakfast', 'gas', 'fuel', 'groceries', 'shopping', 'coffee', 'food',
            'transport', 'uber', 'taxi', 'bus', 'train', 'entertainment', 'movie',
            'restaurant', 'bar', 'drinks', 'clothes', 'shoes', 'electronics'
        ]
        
        # Common income keywords
        self.income_keywords = [
            'income', 'salary', 'wage', 'payment', 'deposit', 'received', 'earned',
            'freelance', 'bonus', 'commission', 'refund', 'return', 'dividend',
            'interest', 'rental', 'business', 'side hustle'
        ]
        
        # Common transfer keywords
        self.transfer_keywords = [
            'transfer', 'move', 'send', 'withdraw', 'deposit', 'between', 'from', 'to'
        ]
    
    async def parse_transaction(self, message: str) -> ParsedTransaction:
        """Parse a natural language message to extract transaction details"""
        message_lower = message.lower()
        
        # Determine transaction type
        transaction_type = self._determine_transaction_type(message_lower)
        
        # Extract amount
        amount = self._extract_amount(message)
        if amount is None:
            raise ValueError("Could not extract amount from message")
        
        # Extract description
        description = self._extract_description(message, amount)
        
        # Extract date (default to today)
        transaction_date = self._extract_date(message) or date_type.today()
        
        if transaction_type == "transfer":
            return await self._parse_transfer(message, amount, description, transaction_date)
        elif transaction_type == "income":
            return await self._parse_income(message, amount, description, transaction_date)
        else:  # expense
            return await self._parse_expense(message, amount, description, transaction_date)
    
    def _determine_transaction_type(self, message: str) -> str:
        """Determine if the message is about an expense, income, or transfer"""
        message_words = message.split()
        
        # Check for transfer keywords
        for word in message_words:
            if word in self.transfer_keywords:
                return "transfer"
        
        # Check for income keywords
        for word in message_words:
            if word in self.income_keywords:
                return "income"
        
        # Default to expense
        return "expense"
    
    def _extract_amount(self, message: str) -> Optional[float]:
        """Extract amount from message"""
        # Look for currency patterns: $25, $25.50, 25 dollars, etc.
        patterns = [
            r'\$(\d+(?:\.\d{2})?)',  # $25, $25.50
            r'(\d+(?:\.\d{2})?)\s*(?:dollars?|bucks?)',  # 25 dollars, 25 bucks
            r'(\d+(?:\.\d{2})?)\s*usd',  # 25 usd
        ]
        
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return float(match.group(1))
        
        return None
    
    def _extract_description(self, message: str, amount: float) -> str:
        """Extract description from message"""
        # Remove amount and common words, keep the meaningful part
        amount_str = f"${amount}" if amount.is_integer() else f"${amount:.2f}"
        message_clean = message.replace(amount_str, "").replace(str(int(amount)), "").replace(str(amount), "")
        
        # Remove common filler words
        filler_words = ['add', 'record', 'for', 'at', 'in', 'on', 'the', 'a', 'an', 'and', 'or', 'but']
        words = message_clean.split()
        meaningful_words = [word for word in words if word.lower() not in filler_words]
        
        description = " ".join(meaningful_words).strip()
        return description if description else "Transaction"
    
    def _extract_date(self, message: str) -> Optional[date_type]:
        """Extract date from message (simplified - defaults to today)"""
        # This is a simplified version - could be enhanced with date parsing
        today_keywords = ['today', 'now', 'current']
        yesterday_keywords = ['yesterday']
        
        message_lower = message.lower()
        
        for keyword in yesterday_keywords:
            if keyword in message_lower:
                return date_type.today() - timedelta(days=1)
        
        # Default to today
        return date_type.today()
    
    async def _parse_expense(self, message: str, amount: float, description: str, transaction_date: date_type) -> ParsedTransaction:
        """Parse expense transaction"""
        # Find account
        account_id = await self._find_account(message)
        
        # Find category
        category_id = await self._find_category(message)
        
        # Find place
        place_id = await self._find_place(message)
        
        return ParsedTransaction(
            transaction_type="expense",
            amount=amount,
            description=description,
            date=transaction_date,
            account_id=account_id,
            category_id=category_id,
            place_id=place_id
        )
    
    async def _parse_income(self, message: str, amount: float, description: str, transaction_date: date_type) -> ParsedTransaction:
        """Parse income transaction"""
        # Find account
        account_id = await self._find_account(message)
        
        return ParsedTransaction(
            transaction_type="income",
            amount=amount,
            description=description,
            date=transaction_date,
            account_id=account_id
        )
    
    async def _parse_transfer(self, message: str, amount: float, description: str, transaction_date: date_type) -> ParsedTransaction:
        """Parse transfer transaction"""
        # Find from and to accounts
        from_account_id, to_account_id = await self._find_transfer_accounts(message)
        
        return ParsedTransaction(
            transaction_type="transfer",
            amount=amount,
            description=description,
            date=transaction_date,
            from_account_id=from_account_id,
            to_account_id=to_account_id
        )
    
    async def _find_account(self, message: str) -> Optional[int]:
        """Find account ID from message"""
        accounts = await account.get_multi_by_owner(db=self.db, owner_id=self.user_id)
        
        message_lower = message.lower()
        
        for acc in accounts:
            if acc.name.lower() in message_lower:
                return acc.id
        
        # Default to first account if none found
        return accounts[0].id if accounts else None
    
    async def _find_category(self, message: str) -> Optional[int]:
        """Find category ID from message"""
        categories = await category.get_multi_by_owner(db=self.db, owner_id=self.user_id)
        
        message_lower = message.lower()
        
        # Common category mappings
        category_mappings = {
            'food': ['lunch', 'dinner', 'breakfast', 'restaurant', 'coffee', 'food', 'groceries'],
            'transport': ['gas', 'fuel', 'uber', 'taxi', 'bus', 'train', 'transport'],
            'entertainment': ['movie', 'bar', 'drinks', 'entertainment'],
            'shopping': ['clothes', 'shoes', 'electronics', 'shopping']
        }
        
        for cat in categories:
            # Direct match
            if cat.name.lower() in message_lower:
                return cat.id
            
            # Check category mappings
            if cat.name.lower() in category_mappings:
                for keyword in category_mappings[cat.name.lower()]:
                    if keyword in message_lower:
                        return cat.id
        
        return None
    
    async def _find_place(self, message: str) -> Optional[int]:
        """Find place ID from message"""
        places = await place.get_multi_by_owner(db=self.db, owner_id=self.user_id)
        
        message_lower = message.lower()
        
        for plc in places:
            if plc.name.lower() in message_lower:
                return plc.id
        
        return None
    
    async def _find_transfer_accounts(self, message: str) -> Tuple[Optional[int], Optional[int]]:
        """Find from and to accounts for transfers"""
        accounts = await account.get_multi_by_owner(db=self.db, owner_id=self.user_id)
        
        message_lower = message.lower()
        from_account_id = None
        to_account_id = None
        
        # Look for "from X to Y" pattern
        for i, acc in enumerate(accounts):
            if acc.name.lower() in message_lower:
                # Check if it's mentioned after "from" or "to"
                acc_index = message_lower.find(acc.name.lower())
                
                # Look for "from" before this account
                from_index = message_lower.rfind('from', 0, acc_index)
                if from_index != -1:
                    from_account_id = acc.id
                
                # Look for "to" after this account
                to_index = message_lower.find('to', acc_index)
                if to_index != -1:
                    to_account_id = acc.id
        
        # If we couldn't determine, try to guess based on account types
        if not from_account_id or not to_account_id:
            checking_accounts = [acc for acc in accounts if acc.type.value == "Checking Accounts"]
            savings_accounts = [acc for acc in accounts if acc.type.value == "Savings Accounts"]
            
            if not from_account_id and checking_accounts:
                from_account_id = checking_accounts[0].id
            if not to_account_id and savings_accounts:
                to_account_id = savings_accounts[0].id
        
        return from_account_id, to_account_id
    
    async def create_transaction(self, parsed_transaction: ParsedTransaction) -> Dict[str, Any]:
        """Create the actual transaction in the database"""
        try:
            if parsed_transaction.transaction_type == "expense":
                from app.schemas.expense import ExpenseCreate
                
                expense_data = ExpenseCreate(
                    amount=parsed_transaction.amount,
                    date=parsed_transaction.date.strftime("%Y-%m-%d") if parsed_transaction.date else None,
                    description=parsed_transaction.description,
                    account_id=parsed_transaction.account_id,
                    category_id=parsed_transaction.category_id,
                    place_id=parsed_transaction.place_id
                )
                
                created_expense = await expense.create_with_owner(
                    db=self.db,
                    obj_in=expense_data,
                    owner_id=self.user_id
                )
                
                return {
                    "type": "expense",
                    "id": created_expense.id,
                    "amount": created_expense.amount,
                    "description": created_expense.description,
                    "date": created_expense.date,
                    "account": created_expense.account.name if created_expense.account else None,
                    "category": created_expense.category.name if created_expense.category else None,
                    "place": created_expense.place.name if created_expense.place else None
                }
            
            elif parsed_transaction.transaction_type == "income":
                from app.schemas.income import IncomeCreate
                
                income_data = IncomeCreate(
                    amount=parsed_transaction.amount,
                    date=parsed_transaction.date.strftime("%Y-%m-%d") if parsed_transaction.date else None,
                    description=parsed_transaction.description,
                    account_id=parsed_transaction.account_id
                )
                
                created_income = await income.create_with_owner(
                    db=self.db,
                    obj_in=income_data,
                    owner_id=self.user_id
                )
                
                return {
                    "type": "income",
                    "id": created_income.id,
                    "amount": created_income.amount,
                    "description": created_income.description,
                    "date": created_income.date,
                    "account": created_income.account.name if created_income.account else None
                }
            
            elif parsed_transaction.transaction_type == "transfer":
                from app.schemas.transfer import TransferCreate
                
                transfer_data = TransferCreate(
                    amount=parsed_transaction.amount,
                    date=parsed_transaction.date.strftime("%Y-%m-%d") if parsed_transaction.date else None,
                    description=parsed_transaction.description,
                    from_acc=parsed_transaction.from_account_id,
                    to_acc=parsed_transaction.to_account_id
                )
                
                created_transfer = await transfer.create_with_owner(
                    db=self.db,
                    obj_in=transfer_data,
                    owner_id=self.user_id
                )
                
                return {
                    "type": "transfer",
                    "id": created_transfer.id,
                    "amount": created_transfer.amount,
                    "description": created_transfer.description,
                    "date": created_transfer.date,
                    "from_account": created_transfer.account_from.name if created_transfer.account_from else None,
                    "to_account": created_transfer.account_to.name if created_transfer.account_to else None
                }
        
        except Exception as e:
            raise ValueError(f"Failed to create transaction: {str(e)}") 