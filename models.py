from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import backref, declarative_base, relationship

Base = declarative_base()


class Token(Base):
    __tablename__ = "token"

    # Original scheme don't work locally. Keep it here to make local development easier.
    # pk = Column(Integer, primary_key=True)
    # id = Column(Numeric(80), nullable=False, unique=True)
    id = Column(Numeric(80), primary_key=True)
    symbol = Column(String(8), nullable=False)
    name = Column(String(128), nullable=False)
    decimals = Column(Integer, nullable=False)
    trade_volume = Column(Numeric())

    @property
    def hash(self):
        return "0x%064x" % int(self.id)


class Pair(Base):
    __tablename__ = "pair"
    id = Column(Integer, primary_key=True)
    from_token_id = Column(ForeignKey("token.id"), nullable=False)
    to_token_id = Column(ForeignKey("token.id"), nullable=False)
    from_volume = Column(Numeric())
    to_volume = Column(Numeric())
    from_token = relationship(
        Token,
        foreign_keys=[from_token_id],
        backref=backref("from_pairs", uselist=True, cascade="delete,all"),
    )
    to_token = relationship(
        Token,
        foreign_keys=[to_token_id],
        backref=backref("to_pairs", uselist=True, cascade="delete,all"),
    )
    quote_price = Column(Numeric(), nullable=True)


class Swap(Base):
    __tablename__ = "swap"

    id = Column(Integer, primary_key=True)
    txid = Column(Numeric(80))
    block = Column(Integer, nullable=False)
    timestamp = Column(BigInteger, index=True, nullable=False)
    xor_fee = Column(Numeric(40), nullable=False)
    pair_id = Column(ForeignKey("pair.id"), nullable=False)
    from_amount = Column(Numeric(), nullable=False)
    to_amount = Column(Numeric(), nullable=False)
    filter_mode = Column(String(32), nullable=False)
    swap_fee_amount = Column(Numeric())
    pair = relationship(
        Pair, backref=backref("swaps", uselist=True, cascade="delete,all")
    )

    @property
    def hash(self):
        return "0x%064x" % int(self.txid)


class Burn(Base):
    __tablename__ = "burn"

    id = Column(Integer, primary_key=True)
    block = Column(Integer, nullable=False)
    timestamp = Column(BigInteger, index=True, nullable=False)
    token_id = Column(ForeignKey("token.id"), nullable=False)
    amount = Column(Numeric(), nullable=False)
    token = relationship(
        Token, backref=backref("burns", uselist=True, cascade="delete,all")
    )


class BuyBack(Base):
    __tablename__ = "buyback"

    id = Column(Integer, primary_key=True)
    block = Column(Integer, nullable=False)
    timestamp = Column(BigInteger, index=True, nullable=False)
    token_id = Column(ForeignKey("token.id"), nullable=False)
    amount = Column(Numeric(), nullable=False)
    token = relationship(
        Token, backref=backref("buybacks", uselist=True, cascade="delete,all")
    )


Index("idx_swap_pair_timestamp_desc", Swap.pair_id, Swap.timestamp.desc())
