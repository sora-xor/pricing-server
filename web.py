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

from models import Pair, Swap, Token

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


class OrderDirection(Enum):
    asc = 1
    desc = 2


class SwapOrderBy(Enum):
    timestamp = 1


class Query(graphene.ObjectType):
    tokens = graphene.List(TokenType)
    pairs = graphene.List(PairType)
    swaps = graphene.List(
        SwapType,
        first=Int(),
        skip=Int(),
        orderBy=SwapOrderBy(),
        orderDirection=OrderDirection(),
    )

    async def resolve_tokens(self, info):
        q = select(Token)
        return [t for t, in await info.context["request"].db.execute(q)]

    async def resolve_pairs(self, info):
        q = select(Pair).options(selectinload(Pair.token0), selectinload(Pair.token1))
        return [p for p, in await info.context["request"].db.execute(q)]

    async def resolve_swaps(
        self, info, first=10, skip=None, offset=None, orderBy=None, orderDirection=None
    ):
        first = min(1000, first)  # limit max reply size
        q = (
            select(Swap)
            .limit(first)
            .options(
                selectinload(Swap.pair).selectinload(Pair.token0),
                selectinload(Swap.pair).selectinload(Pair.token1),
            )
        )
        if orderBy:
            orderBy = SwapOrderBy.get(orderBy).name
            q = q.order_by(
                orderBy if orderDirection == OrderDirection.asc else desc(orderBy)
            )
        if skip:
            q = q.offset(skip)
        return [s for s, in await info.context["request"].db.execute(q)]


app = FastAPI()


@app.get("/", response_class=HTMLResponse)
async def root():
    return """
        <!DOCTYPE html>
        <title>SORA Pricing Server</title>
        <h1>SORA Pricing Server</h1>
        <ul>
        <li><a href="/pairs/">Pair Summary</a></li>
        <li><a href="/pairs/XOR-PSWAP">Specific Pair Info</a></li>
        <li><a href="/graph">GraphQL API</a></li>
        <li><a href="/docs">Docs</a></li>
        </ul>
        """


@app.get("/pairs/")
async def pairs(session=Depends(get_db)):
    # 24 hours ago since last imported transaction timestamp
    # fetch all pairs info
    pairs = {}
    for p, last_price in await session.execute(
        select(
            Pair,
            select(Swap.price)
            .where(Swap.pair_id == Pair.id)
            .order_by(Swap.timestamp.desc())
            .limit(1)
            .scalar_subquery(),
        ).options(selectinload(Pair.token0), selectinload(Pair.token1))
    ):
        p.last_price = last_price
        pairs[p.id] = p
    # build list of all token pairs
    response = {
        pair.token0.hash
        + "_"
        + pair.token1.hash: {
            "base_id": pair.token1.hash,
            "base_name": pair.token1.name,
            "base_symbol": pair.token1.symbol,
            "quote_id": pair.token0.hash,
            "quote_name": pair.token0.name,
            "quote_symbol": pair.token0.symbol,
            "last_price": pair.last_price,
            "base_volume": pair.token1_volume,
            "quote_volume": pair.token0_volume,
        }
        for pair in pairs.values()
    }
    return response


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
    token0 = Token.__table__.alias("token0")
    token1 = Token.__table__.alias("token1")
    whitelist = [token["address"] for token in get_whitelist()]
    pair = await session.execute(
        select(Pair)
        .options(selectinload(Pair.token0), selectinload(Pair.token1))
        .join(token0, token0.c.id == Pair.token0_id)
        .join(token1, token1.c.id == Pair.token1_id)
        .where(
            and_(
                token0.c.hash.in_(whitelist),
                token1.c.hash.in_(whitelist),
                token0.c.symbol == base,
                token1.c.symbol == quote,
            )
        )
    )
    pair = pair.scalar()
    if not pair:
        raise HTTPException(status_code=404, detail="Pair not found")
    price = (
        await session.execute(
            select(Swap.__table__.c.price)
            .where(Swap.pair_id == pair.id)
            .order_by(Swap.timestamp.desc())
            .limit(1)
        )
    ).scalar()
    return {
        "base_id": pair.token1.hash,
        "base_name": pair.token1.name,
        "base_symbol": pair.token1.symbol,
        "quote_id": pair.token0.hash,
        "quote_name": pair.token0.name,
        "quote_symbol": pair.token0.symbol,
        "last_price": price,
        "base_volume": pair.token1_volume or 0,
        "quote_volume": pair.token0_volume or 0,
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
