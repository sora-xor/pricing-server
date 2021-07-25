from decimal import Decimal
from time import time

import graphene
import requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from graphene import Enum, Int, String
from graphene_sqlalchemy import SQLAlchemyObjectType
from graphql.execution.executors.asyncio import AsyncioExecutor
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from starlette.graphql import GraphQLApp

from models import Burn, BuyBack, Pair, Swap, Token
from processing import XOR_ID

WHITELIST_URL = "https://raw.githubusercontent.com/sora-xor/polkaswap-token-whitelist-config/master/whitelist.json"  # noqa

__cache = {}


def get_whitelist():
    KEY = "whitelist"
    if KEY not in __cache or __cache[KEY]["updated"] < time() - 24 * 3600:
        __cache[KEY] = {"data": requests.get(WHITELIST_URL).json(), "updated": time()}
    return __cache["whitelist"]["data"]


async def get_db():
    from db import async_session

    async with async_session() as session:
        yield session


class TokenType(SQLAlchemyObjectType):
    class Meta:
        model = Token
        # use `only_fields` to only expose specific fields ie "name"
        # only_fields = ("name",)
        # use `exclude_fields` to exclude specific fields ie "last_name"
        # exclude_fields = ("last_name",)


class PairType(SQLAlchemyObjectType):
    class Meta:
        model = Pair


class SwapType(SQLAlchemyObjectType):
    hash = String()

    class Meta:
        model = Swap


class BurnType(SQLAlchemyObjectType):
    class Meta:
        model = Burn


class BuyBackType(SQLAlchemyObjectType):
    class Meta:
        model = BuyBack


class OrderDirection(Enum):
    asc = 1
    desc = 2


class OrderBy(Enum):
    timestamp = 1


async def resolve_paginated(
    q, info, first=10, skip=None, offset=None, orderBy=None, orderDirection=None
):
    first = min(1000, first)  # limit max reply size
    q = q.limit(first)
    if orderBy:
        orderBy = OrderBy.get(orderBy).name
        q = q.order_by(
            orderBy if orderDirection == OrderDirection.asc else desc(orderBy)
        )
    if skip:
        q = q.offset(skip)
    return [s for s, in await info.context["request"].db.execute(q)]


class Query(graphene.ObjectType):
    tokens = graphene.List(TokenType)
    pairs = graphene.List(PairType)
    swaps = graphene.List(
        SwapType,
        first=Int(),
        skip=Int(),
        orderBy=OrderBy(),
        orderDirection=OrderDirection(),
    )
    burns = graphene.List(
        BurnType,
        first=Int(),
        skip=Int(),
        orderBy=OrderBy(),
        orderDirection=OrderDirection(),
    )
    buy_backs = graphene.List(
        BuyBackType,
        first=Int(),
        skip=Int(),
        orderBy=OrderBy(),
        orderDirection=OrderDirection(),
    )

    async def resolve_tokens(self, info):
        q = select(Token)
        return [t for t, in await info.context["request"].db.execute(q)]

    async def resolve_pairs(self, info):
        q = select(Pair).options(
            selectinload(Pair.from_token), selectinload(Pair.to_token)
        )
        return [p for p, in await info.context["request"].db.execute(q)]

    async def resolve_swaps(self, info, **kwargs):
        q = select(Swap).options(
            selectinload(Swap.pair).selectinload(Pair.from_token),
            selectinload(Swap.pair).selectinload(Pair.to_token),
        )
        return resolve_paginated(q, info, **kwargs)

    async def resolve_burns(self, info, **kwargs):
        q = select(Burn).options(selectinload(Burn.token))
        return resolve_paginated(q, info, **kwargs)

    async def resolve_buy_backs(self, info, **kwargs):
        q = select(BuyBack).options(selectinload(BuyBack.token))
        return resolve_paginated(q, info, **kwargs)


app = FastAPI()


@app.get("/", response_class=HTMLResponse)
async def root():
    return """
        <!DOCTYPE html>
        <title>SORA Pricing Server</title>
        <h1>SORA Pricing Server</h1>
        <ul>
        <li><a href="/pairs/">Pair Summary</a></li>
        <li><a href="/pairs/VAL-XOR/">Specific Pair Info</a></li>
        <li><a href="/graph">GraphQL API</a></li>
        <li><a href="/docs">Docs</a></li>
        </ul>
        """


@app.get("/pairs/")
async def pairs(session=Depends(get_db)):
    # fetch all pairs info
    pairs = {}
    xor_id_int = int(XOR_ID, 16)
    for p, last_price in await session.execute(
        select(
            Pair,
            select(Swap.to_amount / Swap.from_amount)
            .where(Swap.pair_id == Pair.id)
            .order_by(Swap.timestamp.desc())
            .limit(1)
            .scalar_subquery(),
        ).options(selectinload(Pair.from_token), selectinload(Pair.to_token))
    ):
        if p.from_token_id == xor_id_int:
            base = p.to_token
            base_volume = p.to_volume
            quote = p.from_token
            quote_volume = p.from_volume
            if last_price:
                last_price = 1 / last_price
        else:
            # should be no non-XOR pairs
            assert p.to_token_id == xor_id_int
            base = p.from_token
            base_volume = p.from_volume
            quote = p.to_token
            quote_volume = p.to_volume
        id = base.hash + "_" + quote.hash
        if id in pairs:
            if base_volume:
                pairs[id]["base_volume"] += base_volume
            if quote_volume:
                pairs[id]["quote_volume"] += quote_volume
        else:
            pairs[id] = {
                "base_id": base.hash,
                "base_name": base.name,
                "base_symbol": base.symbol,
                "quote_id": quote.hash,
                "quote_name": quote.name,
                "quote_symbol": quote.symbol,
                "last_price": last_price,
                "base_volume": base_volume,
                "quote_volume": quote_volume,
            }
    return pairs


class PairResponse(BaseModel):
    base_id: str = Field(
        "0x0200000000000000000000000000000000000000000000000000000000000000"
    )
    base_name: str = Field("SORA")
    base_symbol: str = Field("XOR")
    quote_id: str = Field(
        "0x0200050000000000000000000000000000000000000000000000000000000000"
    )
    quote_name: str = Field("Polkaswap")
    quote_symbol: str = Field(example="PSWAP")
    last_price: float = Field(example=12.34)
    base_volume: int = Field(example=1000)
    quote_volume: int = Field(example=1000)


@app.get("/pairs/{base}-{quote}/", response_model=PairResponse)
async def pair(base: str, quote: str, session=Depends(get_db)):
    # get pair and its tokens info
    from_token = Token.__table__.alias("from_token")
    to_token = Token.__table__.alias("to_token")
    whitelist = [Decimal(int(token["address"], 16)) for token in get_whitelist()]
    pair = await session.execute(
        select(Pair)
        .options(selectinload(Pair.from_token), selectinload(Pair.to_token))
        .join(from_token, from_token.c.id == Pair.from_token_id)
        .join(to_token, to_token.c.id == Pair.to_token_id)
        .where(
            and_(
                from_token.c.id.in_(whitelist),
                to_token.c.id.in_(whitelist),
                from_token.c.symbol == base,
                to_token.c.symbol == quote,
            )
        )
    )
    pair = pair.scalar()
    if not pair:
        raise HTTPException(status_code=404, detail="Pair not found")
    base = pair.from_token
    quote = pair.to_token
    if quote.id != int(XOR_ID, 16):
        raise HTTPException(status_code=404, detail="Pair not found")
    price = (
        await session.execute(
            select(Swap.to_amount / Swap.from_amount)
            .where(Swap.pair_id == pair.id)
            .order_by(Swap.timestamp.desc())
            .limit(1)
        )
    ).scalar()
    return {
        "base_id": base.hash,
        "base_name": base.name,
        "base_symbol": base.symbol,
        "quote_id": quote.hash,
        "quote_name": quote.name,
        "quote_symbol": quote.symbol,
        "last_price": price,
        "base_volume": pair.from_volume or 0,
        "quote_volume": pair.to_volume or 0,
    }


@app.get("/graph")
async def graphql_get(request: Request):
    return await GraphQLApp(
        schema=graphene.Schema(query=Query), executor_class=AsyncioExecutor
    ).handle_graphql(request)


@app.post("/graph")
async def graphql_post(request: Request, db=Depends(get_db)):
    gapp = GraphQLApp(
        schema=graphene.Schema(query=Query), executor_class=AsyncioExecutor
    )
    request.db = db
    return await gapp.handle_graphql(request)


@app.get("/healthcheck")
async def healthcheck():
    return {"status": "OK"}
