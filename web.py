import graphene
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from graphene import Enum, Int
from graphene_sqlalchemy import SQLAlchemyObjectType
from graphql.execution.executors.asyncio import AsyncioExecutor
from pydantic import BaseModel
from sqlalchemy import and_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import scoped_session, selectinload, sessionmaker
from starlette.graphql import GraphQLApp

from models import Pair, Swap, Token, get_db_engine

engine = get_db_engine()
async_session = sessionmaker(engine,
                             expire_on_commit=False,
                             class_=AsyncSession)

db_session = scoped_session(
    sessionmaker(autocommit=False, autoflush=False, bind=engine))


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
    swaps = graphene.List(SwapType,
                          first=Int(),
                          skip=Int(),
                          orderBy=SwapOrderBy(),
                          orderDirection=OrderDirection())

    async def resolve_tokens(self, info):
        async with async_session() as session:
            q = select(Token)
            return [t for t, in await session.execute(q)]

    async def resolve_pairs(self, info):
        async with async_session() as session:
            q = select(Pair).options(
                selectinload(Pair.token0),
                selectinload(Pair.token1),
            )
            return [p for p, in await session.execute(q)]

    async def resolve_swaps(self,
                            info,
                            first=10,
                            skip=None,
                            offset=None,
                            orderBy=None,
                            orderDirection=None):
        async with async_session() as session:
            first = min(1000, first)  # limit max reply size
            q = select(Swap).limit(first).options(
                selectinload(Swap.pair).selectinload(Pair.token0),
                selectinload(Swap.pair).selectinload(Pair.token1),
            )
            if orderBy:
                orderBy = SwapOrderBy.get(orderBy).name
                q = q.order_by(orderBy if orderDirection ==
                               OrderDirection.asc else desc(orderBy))
            if skip:
                q = q.offset(skip)
            return [s for s, in await session.execute(q)]


app = FastAPI()


@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <html>
        <head>
            <title>SORA Pricing Server</title>
        </head>
        <body>
        <ul>
        <li><a href="/pairs/">Pair Summary</a></li>
        <li><a href="/pairs/XOR-PSWAP">Specific Pair Info</a></li>
        <li><a href="/graph">GraphQL API</a></li>
        <li><a href="/docs">Docs</a></li>
        </ul>
        </body>
    </html>
    """


@app.get("/pairs/")
async def pairs():
    async with async_session() as session:
        # 24 hours ago since last imported transaction timestamp
        last_24h = (await session.execute(select(func.max(Swap.timestamp))
                                          )).scalar() - 24 * 3600
        # fetch all pairs info
        pairs = {}
        for p, in await session.execute(
                select(Pair).options(selectinload(Pair.token0),
                                     selectinload(Pair.token1))):
            pairs[p.id] = p
        # obtain last prices
        prices = session.execute(
            select(Swap.__table__.c.pair_id, Swap.__table__.c.price).distinct(
                Swap.__table__.c.pair_id).order_by(
                    Swap.__table__.c.pair_id,
                    Swap.__table__.c.timestamp.desc()))
        # get trade volumes over last 24 hours if exists
        # (sum(amount) from swap table)
        volumes = session.execute(
            select(
                Swap.__table__.c.pair_id,
                func.sum(Swap.__table__.c.token0_amount),
                func.sum(Swap.__table__.c.token1_amount),
            ).where(Swap.__table__.c.timestamp > last_24h).group_by(
                Swap.__table__.c.pair_id, ))
        # build list of all token pairs
        response = {
            pair.token0.hash + "_" + pair.token1.hash: {
                "base_id": pair.token1.hash,
                "base_name": pair.token1.name,
                "base_symbol": pair.token1.symbol,
                "quote_id": pair.token0.hash,
                "quote_name": pair.token0.name,
                "quote_symbol": pair.token0.symbol,
                "base_volume": 0,
                "quote_volume": 0,
            }
            for pair in pairs.values()
        }
        # fill volumes
        for pair_id, a1vol, a2vol in await volumes:
            p = pairs[pair_id]
            response[p.token0.hash + "_" + p.token1.hash].update({
                "base_volume":
                int(a2vol),
                "quote_volume":
                int(a1vol),
            })
        # fill prices
        for pair_id, last_price in await prices:
            p = pairs[pair_id]
            response[p.token0.hash + "_" +
                     p.token1.hash]['last_price'] = last_price
        return response


class PairResponse(BaseModel):
    base_id: str
    base_name: str
    base_symbol: str
    quote_id: str
    quote_name: str
    quote_symbol: str
    last_price: float
    base_volume: int
    quote_volume: int


@app.get("/pairs/{base}-{quote}/", response_model=PairResponse)
async def pair(base, quote):
    async with async_session() as session:
        # will need it later
        last_24h = session.execute(select(func.max(Swap.timestamp)))
        # get pair and its tokens info
        token0 = Token.__table__.alias('token0')
        token1 = Token.__table__.alias('token1')
        pair = await session.execute(
            select(Pair).options(selectinload(Pair.token0),
                                 selectinload(Pair.token1)).join(
                                     token0,
                                     token0.c.id == Pair.token0_id).join(
                                         token1,
                                         token1.c.id == Pair.token1_id).where(
                                             and_(token0.c.symbol == base,
                                                  token1.c.symbol == quote)))
        pair = pair.scalar()
        if not pair:
            raise HTTPException(status_code=404, detail="Pair not found")
        # get 24h volume
        last_24h = (await last_24h).scalar() - 24 * 3600
        volume = session.execute(
            select(
                func.sum(Swap.token0_amount),
                func.sum(Swap.token1_amount),
            ).where(and_(
                Swap.timestamp > last_24h,
                Swap.pair_id == pair.id,
            )))
        # get last price
        price = (await session.execute(
            select(Swap.__table__.c.price).where(
                Swap.pair_id == pair.id).order_by(
                    Swap.timestamp.desc()).limit(1))).scalar()
        for a1vol, a2vol in await volume:
            break
        return {
            "base_id": pair.token1.hash,
            "base_name": pair.token1.name,
            "base_symbol": pair.token1.symbol,
            "quote_id": pair.token0.hash,
            "quote_name": pair.token0.name,
            "quote_symbol": pair.token0.symbol,
            "last_price": price,
            "base_volume": int(a2vol or 0),
            "quote_volume": int(a1vol or 0)
        }


@app.get("/healthcheck")
async def healthcheck():
    return {"status": "OK"}


app.add_route(
    "/graph",
    GraphQLApp(
        schema=graphene.Schema(query=Query),
        executor_class=AsyncioExecutor,
    ))
