from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import backref, declarative_base, relationship

Base = declarative_base()


class Token(Base):
    __tablename__ = "token"

    id = Column(Integer, primary_key=True)
    hash = Column(String(66), unique=True, nullable=False)
    symbol = Column(String(8), nullable=False)
    name = Column(String(128), nullable=False)
    decimals = Column(Integer, nullable=False)
    trade_volume = Column(Float)


class Pair(Base):
    __tablename__ = "pair"
    id = Column(Integer, primary_key=True)
    token0_id = Column(ForeignKey("token.id"), nullable=False)
    token1_id = Column(ForeignKey("token.id"), nullable=False)
    token0 = relationship(
        Token,
        foreign_keys=[token0_id],
        backref=backref("pairs0", uselist=True, cascade="delete,all"),
    )
    token1 = relationship(
        Token,
        foreign_keys=[token1_id],
        backref=backref("pairs1", uselist=True, cascade="delete,all"),
    )
    token0_volume = Column(Float)
    token1_volume = Column(Float)


class Swap(Base):
    __tablename__ = "swap"

    id = Column(Numeric(20), primary_key=True)
    block = Column(Integer, nullable=False)
    timestamp = Column(BigInteger, index=True, nullable=False)
    xor_fee = Column(Numeric(20), nullable=False)
    pair_id = Column(ForeignKey("pair.id"), nullable=False)
    token0_amount = Column(Numeric(33), nullable=False)
    token1_amount = Column(Numeric(33), nullable=False)
    price = Column(Float)
    filter_mode = Column(String(32), nullable=False)
    swap_fee_amount = Column(Numeric(21))
    pair = relationship(
        Pair, backref=backref("swaps", uselist=True, cascade="delete,all")
    )


Index("idx_swap_pair_timestamp_desc", Swap.pair_id, Swap.timestamp.desc())
