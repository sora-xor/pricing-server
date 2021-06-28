import asyncio
import unittest
from time import time

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker

from models import Base, Pair, Swap, Token
from run_node_processing import update_volumes
from web import app, get_db

SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, class_=AsyncSession
)


async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


class ImportTest(unittest.TestCase):
    def test_update_volumes(self):
        async def inner():
            # create tables
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            # insert test data
            async with TestingSessionLocal() as session:
                dai = Token(hash="0x1", name="D", decimals=18, symbol="DAI")
                session.add(dai)
                xor = Token(hash="0x2", name="X", decimals=18, symbol="XOR")
                session.add(xor)
                pair = Pair(token0=dai, token1=xor)
                session.add(pair)
                swap = Swap(
                    id=1,
                    block=2,
                    timestamp=time(),
                    pair=pair,
                    xor_fee=4,
                    price=2,
                    token0_amount=1,
                    token1_amount=2,
                    filter_mode="mode",
                )
                session.add(swap)
                swap = Swap(
                    id=2,
                    block=4,
                    timestamp=time(),
                    pair=pair,
                    xor_fee=4,
                    price=3,
                    token0_amount=1,
                    token1_amount=3,
                    filter_mode="mode",
                )
                session.add(swap)
                await session.commit()
                # call update_volumes()
                await update_volumes(session)
                # check volume columns filled
                pair = (await session.execute(select(Pair))).scalar()
                self.assertEqual(pair.token0_volume, 2.0)
                self.assertEqual(pair.token1_volume, 5.0)
                dai, xor = (
                    await session.execute(select(Token).order_by(Token.id))
                ).scalars()
                self.assertEqual(dai.trade_volume, 2.0)
                self.assertEqual(xor.trade_volume, 5.0)
                # drop tables
                await conn.run_sync(Base.metadata.drop_all)

            asyncio.run(inner())


class WebAppTest(unittest.TestCase):
    async def asyncSetUp(self):
        # create tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # insert test data
        async with TestingSessionLocal() as session:
            dai = Token(hash="0x1", name="D", decimals=18, symbol="DAI")
            session.add(dai)
            xor = Token(hash="0x2", name="X", decimals=18, symbol="XOR")
            session.add(xor)
            pair = Pair(token0=dai, token1=xor, token0_volume=2, token1_volume=5)
            session.add(pair)
            swap = Swap(
                id=1,
                block=2,
                timestamp=3,
                pair=pair,
                xor_fee=4,
                price=2,
                token0_amount=1,
                token1_amount=2,
                filter_mode="mode",
            )
            session.add(swap)
            swap = Swap(
                id=2,
                block=4,
                timestamp=5,
                pair=pair,
                xor_fee=4,
                price=3,
                token0_amount=1,
                token1_amount=3,
                filter_mode="mode",
            )
            session.add(swap)
            await session.commit()

    async def asyncTearDown(self):
        # drop tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    def setUp(self):
        asyncio.run(self.asyncSetUp())

    def tearDown(self):
        asyncio.run(self.asyncTearDown())

    def test_pairs_get(self):
        response = client.get("/pairs/")
        assert response.status_code == 200, response.text
        data = response.json()
        self.assertEqual(
            data,
            {
                "0x1_0x2": {
                    "base_id": "0x2",
                    "base_name": "X",
                    "base_symbol": "XOR",
                    "base_volume": 5.0,
                    "last_price": 3,
                    "quote_id": "0x1",
                    "quote_name": "D",
                    "quote_symbol": "DAI",
                    "quote_volume": 2.0,
                }
            },
        )

    def test_pair_get(self):
        response = client.get("/pairs/DAI-XOR")
        assert response.status_code == 200, response.text
        data = response.json()
        self.assertEqual(
            data,
            {
                "base_id": "0x2",
                "base_name": "X",
                "base_symbol": "XOR",
                "base_volume": 5.0,
                "last_price": 3,
                "quote_id": "0x1",
                "quote_name": "D",
                "quote_symbol": "DAI",
                "quote_volume": 2.0,
            },
        )

    def test_graphql_post(self):
        response = client.post(
            "/graph", json=dict(query="{pairs{id, token0{symbol}, token1{symbol}}}")
        )
        assert response.status_code == 200, response
        data = response.json()
        self.assertEqual(
            data,
            {
                "data": {
                    "pairs": [
                        {
                            "id": "1",
                            "token0": {"symbol": "DAI"},
                            "token1": {"symbol": "XOR"},
                        }
                    ]
                }
            },
        )
