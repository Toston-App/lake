"""The kinds of money movement a user records.

Has no imports from the app, so models, schemas and the pure recurrence
modules can all share it without import cycles.
"""

from enum import Enum


class TransactionType(str, Enum):
    expense = "expense"
    income = "income"
    transfer = "transfer"
