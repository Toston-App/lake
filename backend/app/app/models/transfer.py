
from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Integer, String, Float, Date
from sqlalchemy.orm import relationship


from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User  # noqa: F401
    from .account import Account  # noqa: F401


class Transfer(Base):
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    amount: float = Column(Float, index=True, nullable=False)
    date: Date = Column(Date, index=True)
    description: str = Column(String, index=True)
    from_acc = Column(Integer, ForeignKey("account.id"))
    to_acc = Column(Integer, ForeignKey("account.id"))
    account_from: "Account" = relationship("Account", foreign_keys=[from_acc], back_populates="transfers_out")
    account_to: "Account" = relationship("Account", foreign_keys=[to_acc], back_populates="transfers_in")
    owner_id: int = Column(Integer, ForeignKey("user.id"))
    owner: "User" = relationship("User", back_populates="transfers")

