from decimal import Decimal, Context
import logging
from time import time

import json
import typing
import graphene
import requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from graphene import Enum, Int, String
from graphene_sqlalchemy import SQLAlchemyObjectType
from graphql.execution.executors.asyncio import AsyncioExecutor
from sqlalchemy import and_, desc, or_, func, cast, Numeric
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.sql.expression import lateral, cte
from starlette.graphql import GraphQLApp
from starlette.responses import JSONResponse

from models import Burn, BuyBack, Pair, Swap, Token
from processing import XOR_ID, XSTUSD_ID, KUSD_ID, VXOR_ID, DAI_ID

WHITELIST_URL = "https://raw.githubusercontent.com/sora-xor/polkaswap-token-whitelist-config/master/whitelist.json"  # noqa

XOR_ID_INT = int(XOR_ID, 16)
XSTUSD_ID_INT = int(XSTUSD_ID, 16)
KUSD_ID_INT = int(KUSD_ID, 16)
VXOR_ID_INT = int(VXOR_ID, 16)

__cache = {}

def get_whitelist():
    """
    Download whitelisted tokens. Cache result for 1 day.
    """
    KEY = "whitelist"
    if KEY not in __cache or __cache[KEY]["updated"] < time() - 24 * 3600:
        __cache[KEY] = {"data": requests.get(
            WHITELIST_URL).json(), "updated": time()}
    return __cache["whitelist"]["data"]


async def get_db():
    """
    Open async DB session. To be used as FastAPI dependency.
    """
    from db import async_session

    async with async_session() as session:
        yield session

async def get_last_prices_in_dai(session, token_ids: list):
    dai_id_int = int(DAI_ID, 16)

    latest_swap = lateral(
        select(
            Swap.to_amount,
            Swap.from_amount
        )
        .where(Swap.pair_id == Pair.id)
        .order_by(Swap.timestamp.desc())
        .limit(1)
    )

    result = await session.execute(
        select(
            Pair.from_token_id,
            (latest_swap.c.to_amount / latest_swap.c.from_amount).label("last_price"),
            Pair.quote_price
        )
        .join(latest_swap, onclause=True)  # LATERAL JOIN
        .where(Pair.from_token_id.in_([cast(id, Numeric(80)) for id in token_ids]))
        .where(Pair.to_token_id == cast(dai_id_int, Numeric(80)))
    )

    token_prices = {row[0]: row[2] or row[1] or 0 for row in result.all()}

    xor_price_in_dai = token_prices.get(XOR_ID_INT, 0)

    # If a token doesn't have a direct DAI price, try fetching from XOR
    missing_tokens = [t for t in token_ids if t not in token_prices]

    if missing_tokens and xor_price_in_dai:
        result = await session.execute(
            select(
                Pair.from_token_id,
                (latest_swap.c.to_amount / latest_swap.c.from_amount).label("last_price"),
                Pair.quote_price
            )
            .join(latest_swap, onclause=True)  # LATERAL JOIN
            .where(Pair.from_token_id.in_([cast(id, Numeric(80)) for id in missing_tokens]))
            .where(Pair.to_token_id == cast(XOR_ID_INT, Numeric(80)))
        )

        for row in result.all():
            token_prices[row[0]] = (row[2] or row[1] or 0) * xor_price_in_dai

    return {token_id: token_prices.get(token_id, 0) for token_id in token_ids}

class TokenType(SQLAlchemyObjectType):
    class Meta:
        model = Token


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
    """
    Utility function to handle skip, offset etc GraphQL query parameters
    and convert to equivalent SQL operators.
    q - base SQLAlchemy query
    info - Graphene request info
    """
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
# logger = logging.getLogger(__name__)
# logger.setLevel(logging.INFO)
# logger.addHandler(logging.StreamHandler())

@app.get("/", response_class=HTMLResponse)
async def root():
    """
    Return short info & endpoints index.
    """
    return """
        <!DOCTYPE html>
        <title>SORA Pricing Server</title>
        <h1>SORA Pricing Server</h1>
        <ul>
        <li><a href="/tickers/">Tickers Summary</a></li>
        <li><a href="/pairs/">Pair Summary</a></li>
        <li><a href="/pairs/VAL-XOR/">Specific Pair Info</a></li>
        <li><a href="/graph">GraphQL API</a></li>
        <li><a href="/docs">Docs</a></li>
        </ul>
        """


class FormattedFloat(float):
    def __repr__(self):
        # remove redundant 0 after formatting and then remove . if it is integer number
        return '{:.18f}'.format(self).rstrip('0').rstrip('.')


class JsonFloatEncoder(json.JSONEncoder):
    def encode(self, val):
        if isinstance(val, dict):
            return {k: self.encode(v) for k, v in val.items()}

        if isinstance(val, (list, tuple)):
            return type(val)(self.encode(v) for v in val)

        if isinstance(val, float):
            return FormattedFloat(val)

        return val


class FormattedJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: typing.Any) -> bytes:
        return json.dumps(
            str(content).replace("'", "\"").replace(", ", ",").replace(": ", ":"),
            cls=JsonFloatEncoder,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")


@app.get("/pairs/")
async def pairs(session=Depends(get_db)):
    """
    Returns information on pairs.
    """
    pairs = {}
    # fetch all pairs info
    # select last swap for each pair in subquery to obtain price
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
        # there are separate pairs for selling and buying XOR
        # need to sum them to calculate total volumes
        if p.from_token_id == XOR_ID_INT or p.from_token_id == XSTUSD_ID_INT \
              or p.from_token_id == KUSD_ID_INT or p.from_token_id == VXOR_ID_INT:
            # <p> contains XOR->XXX swaps
            base = p.to_token
            base_volume = p.to_volume
            quote = p.from_token
            quote_volume = p.from_volume
            quote_price = p.quote_price
            if quote_price:
                quote_price = 1 / quote_price
            if last_price:
                # reverse price
                last_price = 1 / last_price
        else:
            base = p.from_token
            base_volume = p.from_volume
            quote = p.to_token
            quote_volume = p.to_volume
            quote_price = p.quote_price
        # quote is always XOR
        id = base.hash + "_" + quote.hash
        if id in pairs:
            # sum up buying and sellling volumes
            if base_volume:
                pairs[id]["base_volume"] += FormattedFloat(base_volume)
            if quote_volume:
                pairs[id]["quote_volume"] += FormattedFloat(quote_volume)
        elif (quote_price is None and last_price == 0) or (last_price is None and quote_price == 0) or \
            (last_price is None and quote_price is None):
            continue
        else:
            pairs[id] = {
                "base_id": base.hash,
                "base_name": base.name,
                "base_symbol": base.symbol,
                "quote_id": quote.hash,
                "quote_name": quote.name,
                "quote_symbol": quote.symbol,
                "last_price": FormattedFloat(quote_price or last_price),
                "base_volume": FormattedFloat(base_volume or 0),
                "quote_volume": FormattedFloat(quote_volume or 0),
            }
    return FormattedJSONResponse(pairs)

@app.get("/tickers/")
async def tickers(session=Depends(get_db)):
    """
    Return information on tickers(pairs).

    Pools just use token_id -> token_id as identifier, so pool_id not included
    """
    pairs = {}
    last_24h = (time() - 24 * 3600) * 1000
    prices_in_dai = await get_last_prices_in_dai(session, [XOR_ID_INT, XSTUSD_ID_INT, KUSD_ID_INT, VXOR_ID_INT])

    swap_stats = cte(
        select(
            Swap.pair_id,
            func.max(Swap.to_amount / Swap.from_amount).label("high_price"),
            func.min(Swap.to_amount / Swap.from_amount).label("low_price")
        )
        .where(Swap.timestamp >= last_24h)
        .group_by(Swap.pair_id)
    )

    latest_swap = lateral(
        select(
            (Swap.to_amount / Swap.from_amount).label("last_price")
        )
        .where(Swap.pair_id == Pair.id)
        .order_by(Swap.timestamp.desc())
        .limit(1)
    )

    query = (
        select(
            Pair,
            latest_swap.c.last_price,
            swap_stats.c.high_price,
            swap_stats.c.low_price
        )
        .select_from(Pair)
        .join(latest_swap, onclause=True)  # LATERAL JOIN
        .join(swap_stats, swap_stats.c.pair_id == Pair.id, isouter=True)
        .options(joinedload(Pair.from_token), joinedload(Pair.to_token))
    )

    request_result = await session.execute(query)

    # fetch all pairs info
    # select last swap for each pair in subquery to obtain price
    for p, last_price, high_price, low_price in request_result:
        # there are separate pairs for selling and buying XOR
        # need to sum them to calculate total volumes
        if p.from_token_id == XOR_ID_INT or p.from_token_id == XSTUSD_ID_INT \
              or p.from_token_id == KUSD_ID_INT or p.from_token_id == VXOR_ID_INT:
            # <p> contains XOR->XXX swaps
            base = p.to_token
            base_volume = p.to_volume
            quote = p.from_token
            quote_volume = p.from_volume
            quote_price = p.quote_price
            if quote_price:
                quote_price = 1 / quote_price
            # reverse price
            if last_price:
                last_price = 1 / last_price
            liquidity_in_dai = ((p.from_token_liquidity or 0) + (p.to_token_liquidity or 0) * (quote_price or last_price or 0))\
                * prices_in_dai[p.from_token_id]
            if low_price or high_price:
                high_price, low_price = (1 / low_price if low_price else None, 
                                         1 / high_price if high_price else None)
        elif p.to_token_id == XOR_ID_INT or p.to_token_id == XSTUSD_ID_INT \
              or p.to_token_id == KUSD_ID_INT or p.to_token_id == VXOR_ID_INT:
            base = p.from_token
            base_volume = p.from_volume
            quote = p.to_token
            quote_volume = p.to_volume
            quote_price = p.quote_price
            liquidity_in_dai = ((p.from_token_liquidity or 0) * (quote_price or last_price or 0) + (p.to_token_liquidity or 0))\
                * prices_in_dai[p.to_token_id]
        else:
            continue

        id = base.hash + "_" + quote.hash
        rev_id = quote.hash + "_" + base.hash

        if rev_id in pairs:
            liquidity_in_dai = pairs[rev_id]["liquidity_in_usd"] = max(FormattedFloat(liquidity_in_dai), pairs[rev_id]["liquidity_in_usd"])
            quote_volume = pairs[rev_id]["base_volume"] = pairs[rev_id]["base_volume"] + FormattedFloat(quote_volume or 0)
            base_volume = pairs[rev_id]["target_volume"] = pairs[rev_id]["target_volume"] + FormattedFloat(base_volume or 0)
        if id in pairs:
            # sum up buying and sellling volumes
            if base_volume:
                pairs[id]["base_volume"] += FormattedFloat(base_volume or 0)
            if quote_volume:
                pairs[id]["target_volume"] += FormattedFloat(quote_volume or 0)
        elif (quote_price is None and last_price == 0) or (last_price is None and quote_price == 0) or \
            (last_price is None and quote_price is None):
            continue
        else:
            pairs[id] = {
                "ticker_id": id,
                "base_currency": base.hash,
                "base_name": base.name,
                "base_symbol": base.symbol, 
                "target_currency": quote.hash,
                "target_name": quote.name,
                "target_symbol": quote.symbol,
                "last_price": FormattedFloat(last_price or quote_price), 
                "base_volume": FormattedFloat(base_volume or 0),
                "target_volume": FormattedFloat(quote_volume or 0),
                "liquidity_in_usd": FormattedFloat(liquidity_in_dai),
                "high": FormattedFloat(high_price or 0),
                "low": FormattedFloat(low_price or 0),
            }
    return FormattedJSONResponse(list(pairs.values()))

@app.get("/pairs/{base}-{quote}/")
async def pair(base: str, quote: str, session=Depends(get_db)):
    """
    Return pricing and volume information on specific pair.
    """
    # get pair and its tokens info
    from_token = Token.__table__.alias("from_token")
    to_token = Token.__table__.alias("to_token")
    whitelist = [Decimal(int(token["address"], 16))
                 for token in get_whitelist()]
    # get volume of base->quote swaps
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
    # get volume of quote->base swaps
    reverse = await session.execute(
        select(Pair).where(
            Pair.from_token_id == pair.to_token_id,
            Pair.to_token_id == pair.from_token_id,
        )
    )
    base_volume = pair.from_volume or 0
    quote_volume = pair.to_volume or 0
    reverse = reverse.scalar()
    # sum up volumes
    if reverse and reverse.to_volume and reverse.from_volume:
        base_volume += reverse.to_volume
        quote_volume += reverse.from_volume
    # query current price (price of last swap of such pair)
    last_swap = (
        await session.execute(
            select(Swap)
            .where(or_(Swap.pair_id == pair.id, Swap.pair_id == reverse.id))
            .order_by(Swap.timestamp.desc())
            .limit(1)
        )
    ).scalar()
    if pair.quote_price:
        last_price = pair.quote_price
    elif last_swap.pair_id == pair.id:
        last_price = last_swap.to_amount / last_swap.from_amount
    else:
        last_price = last_swap.from_amount / last_swap.to_amount
    return FormattedJSONResponse({
        "base_id": base.hash,
        "base_name": base.name,
        "base_symbol": base.symbol,
        "quote_id": quote.hash,
        "quote_name": quote.name,
        "quote_symbol": quote.symbol,
        "last_block": last_swap.block,
        "last_txid": last_swap.hash,
        "last_price": FormattedFloat(last_price),
        "base_volume": FormattedFloat(base_volume),
        "quote_volume": FormattedFloat(quote_volume),
    })


@app.get("/graph")
async def graphql_get(request: Request):
    """
    Return interactive GraphiQL interface.
    """
    return await GraphQLApp(
        schema=graphene.Schema(query=Query), executor_class=AsyncioExecutor
    ).handle_graphql(request)


@app.post("/graph")
async def graphql_post(request: Request, db=Depends(get_db)):
    """
    Handle GraphQL queries.
    """
    gapp = GraphQLApp(
        schema=graphene.Schema(query=Query), executor_class=AsyncioExecutor
    )
    request.db = db
    return await gapp.handle_graphql(request)


@app.get("/healthcheck")
async def healthcheck():
    return {"status": "OK"}
