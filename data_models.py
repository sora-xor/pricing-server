"""

/pairs -> Details on cryptoassets traded on an exchange.
/tickers -> Market related statistics for all markets for the last 24 hours.
/orderbook -> Order book depth of any given trading pair, split into two different arrays for bid and ask orders.
/historical -> Historical trade data for any given trading pair.



What are the pairs available

## Pairs

ticker_id string      Mandatory   Identifier of a ticker with delimiter to separate base/target, eg. BTC_ETH
base      string      Mandatory   Symbol/currency code of a the base cryptoasset, eg. BTC
target    string      Mandatory   Symbol/currency code of the target cryptoasset, eg. ETH


## Tickers


{
      "ticker_id": "BTC_ETH",
      "base_currency": "BTC",
      "target_currency": "ETH",
      "last_price":"50.0",
      "base_volume":"10",
      "target_volume":"500",
      "bid":"49.9",
      "ask":"50.1",
      "high":”51.3”,
      “low”:”49.2”,
}


## Orderbook

Get orders: bid and ask by ticker_id

{
   "ticker_id": "BTC_ETH",
   "timestamp":"1700050000",
   "bids":[
      [
         "49.8",
         "0.50000000"
      ],
      [
         "49.9",
         "6.40000000"
      ]
   ],
   "asks":[
      [
         "50.1",
         "9.20000000"
      ],
      [
         "50.2",
         "7.9000000"
      ]
   ]
}

## Historical trades


Example query:
.../api/historical_trades?ticker_id=BTC_ETH&limit=10
“buy”: [
   {
      "trade_id":1234567,
      "price":"50.1",
      "base_volume":"0.1",
      "target_volume":"1",
      "trade_timestamp":"1700050000",
      "type":"buy"
   }
],
“Sell”: [
   {
      "trade_id":1234567,
      "price":"50.1",
      "base_volume":"0.1",
      "target_volume":"1",
      "trade_timestamp":"1700050000",
      "type":"sell"
   }
]


DAI -> Oracle asset

Map the query

block 15:000 - what was the xor

XOR to DAI
XOR and VAL - converion
Some of the assets - we will get a different price

Ignore -

Withdraw & Adding LIQUIDITY
 - Market depth



USDT
UCDC



"""
from dataclasses import dataclass


@dataclass
class SoraOp:
    id: int
    timestamp: int
    xor_fee: int


@dataclass
class LiquidityTx(SoraOp):
    asset1_type: str
    asset2_type: str
    asset1_amount: float
    asset2_amount: float


@dataclass
class Swap(LiquidityTx):
    filter_mode: str
    swap_fee_amount: float


@dataclass
class Withdraw(LiquidityTx):
    pass


@dataclass
class Deposit(LiquidityTx):
    pass


@dataclass
class InBridgeTx(SoraOp):
    asset_id: str
    amount: float
    external_hash: str


@dataclass
class OutBridgeTx(SoraOp):
    asset_id: str
    amount: float
    address: str
    ext_type: str


@dataclass
class ClaimTx(SoraOp):
    asset_id: str
    amount: float


@dataclass
class TransferTx(SoraOp):
    asset_id: str
    amount: float


@dataclass
class BondStakeTx(SoraOp):
    batch_type: str
    batch_amount: float
