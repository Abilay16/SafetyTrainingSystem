from sqlalchemy import Column, BigInteger, Text, Boolean, DateTime, func
from .db import Base


class User(Base):
    __tablename__ = "user"
    __table_args__ = {"schema": "instr"}

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    login = Column(Text, unique=True, nullable=False)
    pass_hash = Column(Text, nullable=False)
    role = Column(Text, nullable=False)  # ADMIN | GLOBAL | CHIEF
    scope_company_id = Column(BigInteger, nullable=True)
    scope_orgunit_id = Column(BigInteger, nullable=True)
    active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
