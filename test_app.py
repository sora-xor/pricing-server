import asyncio
import unittest
from decimal import Decimal
from time import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker

from models import Base, Pair, Swap, Token
from processing import XOR_ID
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


class DBTestCase(unittest.TestCase):
    async def asyncSetUp(self):
        # create tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        # drop tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    def setUp(self):
        asyncio.run(self.asyncSetUp())

    def tearDown(self):
        asyncio.run(self.asyncTearDown())


class ImportTest(DBTestCase):
    def test_update_volumes(self):
        async def inner():
            # insert test data
            async with TestingSessionLocal() as session:
                dai = Token(id=1, name="D", decimals=18, symbol="DAI")
                session.add(dai)
                xor = Token(id=int(XOR_ID, 16), name="X", decimals=18, symbol="XOR")
                session.add(xor)
                pair = Pair(from_token=dai, to_token=xor)
                session.add(pair)
                swap = Swap(
                    id=1,
                    block=2,
                    timestamp=time() * 1000,
                    pair=pair,
                    xor_fee=4,
                    from_amount=1 * 10 ** 17,
                    to_amount=2 * 10 ** 17,
                    filter_mode="mode",
                )
                session.add(swap)
                swap = Swap(
                    id=2,
                    block=4,
                    timestamp=time() * 1000,
                    pair=pair,
                    xor_fee=4,
                    from_amount=1 * 10 ** 17,
                    to_amount=3 * 10 ** 17,
                    filter_mode="mode",
                )
                session.add(swap)
                await session.commit()
                # call update_volumes()
                await update_volumes(session)
                # check volume columns filled
                pair = (await session.execute(select(Pair))).scalar()
                self.assertEqual(pair.from_volume, Decimal("0.2"))
                self.assertEqual(pair.to_volume, Decimal("0.5"))
                dai, xor = (
                    await session.execute(select(Token).order_by(Token.id))
                ).scalars()
                self.assertEqual(dai.trade_volume, Decimal(".2"))
                self.assertEqual(xor.trade_volume, Decimal(".5"))

        asyncio.run(inner())


class WebAppTest(DBTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # insert test data
        async with TestingSessionLocal() as session:
            dai = Token(id=1, name="D", decimals=18, symbol="DAI")
            session.add(dai)
            xor = Token(id=int(XOR_ID, 16), name="X", decimals=18, symbol="XOR")
            session.add(xor)
            pair = Pair(from_token=dai, to_token=xor, from_volume=2, to_volume=5)
            session.add(pair)
            swap = Swap(
                id=1,
                block=2,
                timestamp=3,
                pair=pair,
                xor_fee=4,
                from_amount=1,
                to_amount=2,
                filter_mode="mode",
            )
            session.add(swap)
            swap = Swap(
                id=2,
                block=4,
                timestamp=5,
                pair=pair,
                xor_fee=4,
                from_amount=1,
                to_amount=3,
                filter_mode="mode",
            )
            session.add(swap)
            await session.commit()

    def test_pairs_get(self):
        response = client.get("/pairs/")
        assert response.status_code == 200, response.text
        data = response.json()
        self.assertEqual(
            data,
            {
                "0x"
                + "0" * 63
                + "1_"
                + XOR_ID: {
                    "base_id": "0x" + "0" * 63 + "1",
                    "base_name": "D",
                    "base_symbol": "DAI",
                    "base_volume": 2.0,
                    "last_price": 3,
                    "quote_id": XOR_ID,
                    "quote_name": "X",
                    "quote_symbol": "XOR",
                    "quote_volume": 5.0,
                }
            },
        )

    @patch("web.get_whitelist")
    def test_pair_get(self, whitelist_mock):
        whitelist_mock.return_value = [{"address": "0x1"}, {"address": XOR_ID}]
        response = client.get("/pairs/DAI-XOR")
        assert response.status_code == 200, response.text
        data = response.json()
        self.assertEqual(
            data,
            {
                "base_id": "0x" + "0" * 63 + "1",
                "base_name": "D",
                "base_symbol": "DAI",
                "base_volume": 2.0,
                "last_price": 3,
                "quote_id": XOR_ID,
                "quote_name": "X",
                "quote_symbol": "XOR",
                "quote_volume": 5.0,
            },
        )

    def test_graphql_post(self):
        response = client.post(
            "/graph", json=dict(query="{pairs{id, fromToken{symbol}, toToken{symbol}}}")
        )
        assert response.status_code == 200, response.text
        data = response.json()
        self.assertEqual(
            data,
            {
                "data": {
                    "pairs": [
                        {
                            "id": "1",
                            "fromToken": {"symbol": "DAI"},
                            "toToken": {"symbol": "XOR"},
                        }
                    ]
                }
            },
        )
