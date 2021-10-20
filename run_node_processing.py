import argparse
import asyncio
import logging
import sys
from dataclasses import asdict
from decimal import Decimal
from time import time
from typing import Dict, List

import decouple
from scalecodec.type_registry import load_type_registry_file
from sqlalchemy import and_, func, update
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from substrateinterface import SubstrateInterface
from tqdm import trange

from models import Burn, BuyBack, Pair, Swap, Token
from processing import (
    CURRENCIES,
    DEPOSITED,
    PSWAP_ID,
    VAL_ID,
    XOR_ID,
    get_processing_functions,
    get_timestamp,
)


def connect_to_substrate_node():
    try:
        substrate = SubstrateInterface(
            url=decouple.config("SUBSTRATE_URL", "ws://127.0.0.1:9944"),
            type_registry_preset="default",
            type_registry=load_type_registry_file("custom_types.json"),
        )
        return substrate
    except ConnectionRefusedError:
        logging.error(
            "⚠️ No local Substrate node running, try running 'start_local_substrate_node.sh' first"  # noqa
        )
        return None


def get_events_from_block(substrate, block_id: int):
    """
    Return events from block number <block_id> grouped by extrinsic_id.
    """
    logging.info("Getting events from block %i", block_id)
    block_hash = substrate.get_block_hash(block_id=block_id)

    # Retrieve extrinsics in block
    result = substrate.get_runtime_block(
        block_hash=block_hash, ignore_decoding_errors=True
    )
    events = substrate.get_events(block_hash)

    # group events by extrinsic_idx in dict
    grouped_events: Dict[int, List] = {}
    for event in events:
        event = str(event)
        eventdict = eval(event)
        idx = eventdict["extrinsic_idx"]

        if idx in grouped_events.keys():
            grouped_events[idx].append(eventdict)
        else:
            grouped_events[idx] = [eventdict]
    return events, result, grouped_events


def process_events(dataset, func_map, result, grouped_events):
    """
    Call function from func_map for every extrinsic depending on extrinsic type.
    """
    extrinsic_idx = 0
    timestamp = get_timestamp(result)

    for extrinsic in result["block"]["extrinsics"]:
        extrinsic_events = grouped_events[extrinsic_idx]
        extrinsic_idx += 1
        exdict = extrinsic and extrinsic.value
        if exdict and "call_function" in exdict.keys():
            tx_type = exdict["call_function"]
            processing_func = func_map.get(tx_type)
            if processing_func:
                tx = processing_func(timestamp, extrinsic_events, exdict)
                if tx:
                    dataset.append(asdict(tx))


async def get_or_create_token(substrate, session, id: int) -> Token:
    q = session.execute(select(Token).where(Token.id == Decimal(id)))
    for (token,) in await q:
        return token
    assets = substrate.rpc_request("assets_listAssetInfos", [])["result"]
    for a in assets:
        if int(a["asset_id"], 16) == id:
            a = Token(
                id=id, name=a["name"], symbol=a["symbol"], decimals=int(a["precision"])
            )
            session.add(a)
            await session.commit()
            return a
    logging.error("Asset not found: " + hash)
    raise RuntimeError("Asset not found: " + hash)


async def get_or_create_pair(
    substrate, session, pairs, from_token_id: str, to_token_id: str
):
    if (from_token_id, to_token_id) not in pairs:
        from_token = get_or_create_token(substrate, session, from_token_id)
        to_token = get_or_create_token(substrate, session, to_token_id)
        p = Pair(from_token_id=(await from_token).id, to_token_id=(await to_token).id)
        session.add(p)
        await session.commit()
        pairs[from_token_id, to_token_id] = p
    return pairs[from_token_id, to_token_id]


async def get_all_pairs(session):
    pairs = {}
    for (p,) in await session.execute(
        select(Pair).options(selectinload(Pair.from_token), selectinload(Pair.to_token))
    ):
        pairs[p.from_token.id, p.to_token.id] = p
    return pairs


async def update_volumes(session):
    """
    Update Pair.from_volume, Pair.to_volume and Token.trade_volume.
    """
    last_24h = (time() - 24 * 3600) * 1000
    div = Decimal(10 ** 18)
    await session.execute(
        update(Token).values(
            trade_volume=func.coalesce(
                select(func.sum(Swap.from_amount) / div)
                .join(Pair, Pair.id == Swap.pair_id)
                .where(and_(Swap.timestamp > last_24h, Pair.from_token_id == Token.id))
                .scalar_subquery(),
                0,
            )
            + func.coalesce(
                select(func.sum(Swap.to_amount) / div)
                .join(Pair, Pair.id == Swap.pair_id)
                .where(and_(Swap.timestamp > last_24h, Pair.to_token_id == Token.id))
                .scalar_subquery(),
                0,
            )
            + func.coalesce(
                select(func.sum(Burn.amount) / div)
                .where(and_(Burn.timestamp > last_24h, Burn.token_id == Token.id))
                .scalar_subquery(),
                0,
            )
            + func.coalesce(
                select(func.sum(BuyBack.amount) / div)
                .where(and_(BuyBack.timestamp > last_24h, BuyBack.token_id == Token.id))
                .scalar_subquery(),
                0,
            )
        )
    )
    await session.execute(
        update(Pair).values(
            from_volume=select(func.sum(Swap.from_amount / div))
            .where(and_(Swap.pair_id == Pair.id, Swap.timestamp > last_24h))
            .scalar_subquery(),
            to_volume=select(func.sum(Swap.to_amount / div))
            .where(and_(Swap.pair_id == Pair.id, Swap.timestamp > last_24h))
            .scalar_subquery(),
        )
    )
    await session.commit()


def get_event_param(event, param_idx):
    return event.value["params"][param_idx]["value"]


async def async_main(async_session, begin=1, clean=False, silent=False):
    # get the number of last block in the chain
    substrate = connect_to_substrate_node()
    end = substrate.get_runtime_block(substrate.get_chain_head())["block"]["header"][
        "number"
    ]
    selected_events = {"swap"}
    func_map = {
        k: v for k, v in get_processing_functions().items() if k in selected_events
    }
    xor_id_int = int(XOR_ID, 16)
    val_id_int = int(VAL_ID, 16)
    pswap_id_int = int(PSWAP_ID, 16)
    async with async_session() as session:
        # cache list of pairs in memory
        # to avoid SELECTing them everytime there is need to lookup ID by hash
        pairs = await get_all_pairs(session)
        # find number of last block already in DB and resume from there
        last = (await session.execute(func.max(Swap.block))).scalar()
        if last:
            begin = last + 1
        # sync from last block in the DB to last block in the chain
        pending = None
        if not silent:
            logging.info("Importing from %i to %i", begin, end)
        # make sure XOR, VAL and PSWAP token entries created
        # be able to import burns and buybacks
        await get_or_create_token(substrate, session, xor_id_int)
        await get_or_create_token(substrate, session, val_id_int)
        await get_or_create_token(substrate, session, pswap_id_int)
        for block in (range if silent or not sys.stdout.isatty() else trange)(
            begin, end
        ):
            # get events from <block> to <dataset>
            dataset = []
            events, res, grouped_events = get_events_from_block(substrate, block)
            timestamp = get_timestamp(res)
            process_events(dataset, func_map, res, grouped_events)
            # await previous INSERT to finish if any
            if pending:
                await pending
                pending = None
            # prepare data to be INSERTed
            swaps = []
            for tx in dataset:
                try:
                    # skip transactions with invalid asset type 0x000....0
                    from_asset = int(tx.pop("input_asset_id"), 16)
                    to_asset = int(tx.pop("output_asset_id"), 16)
                    if not from_asset or not to_asset:
                        continue
                    if from_asset == xor_id_int or to_asset == xor_id_int:
                        data = [
                            (
                                from_asset,
                                tx.pop("in_amount"),
                                to_asset,
                                tx.pop("out_amount"),
                            )
                        ]
                    else:
                        data = [
                            (
                                from_asset,
                                tx.pop("in_amount"),
                                xor_id_int,
                                tx["xor_amount"],
                            ),
                            (
                                xor_id_int,
                                tx["xor_amount"],
                                to_asset,
                                tx.pop("out_amount"),
                            ),
                        ]
                    del tx["xor_amount"]
                    tx["filter_mode"] = tx["filter_mode"][0]
                    tx["txid"] = tx.pop("id")
                    for from_asset, from_amount, to_asset, to_amount in data:
                        tx["pair_id"] = (
                            await get_or_create_pair(
                                substrate, session, pairs, from_asset, to_asset
                            )
                        ).id
                        swaps.append(
                            Swap(
                                block=block,
                                from_amount=from_amount,
                                to_amount=to_amount,
                                **tx
                            )
                        )
                except Exception as e:
                    logging.error(
                        "Failed to process transaction %s in block %i:", tx, block
                    )
                    logging.error(e)
                    raise
            # collect burns/buybacks
            burns = []
            buybacks = []
            for idx, e in enumerate(events):
                module = e.value["module_id"]
                event = e.value["event_id"]
                if module == "PswapDistribution" and event == "FeesExchanged":
                    if (
                        len(events) > idx + 4
                        and events[idx + 4].value["event_id"] == "IncentiveDistributed"
                    ):
                        # buy back
                        pswap_received = get_event_param(e, 5)
                        # burn all and remint
                        pswap_reminted_lp = get_event_param(
                            events[idx + 2], 2
                        )  # Currencies.Deposit
                        pswap_reminted_parliament = get_event_param(
                            events[idx + 3], 2
                        )  # Currencies.Deposit
                        pswap_burned = (
                            pswap_received
                            - pswap_reminted_parliament
                            - pswap_reminted_lp
                        )
                        burns.append(
                            Burn(
                                block=block,
                                timestamp=timestamp,
                                token_id=pswap_id_int,
                                amount=pswap_burned,
                            )
                        )
                        buybacks.append(
                            BuyBack(
                                block=block,
                                timestamp=timestamp,
                                token_id=pswap_id_int,
                                amount=pswap_reminted_lp + pswap_reminted_parliament,
                            )
                        )
                elif module == "XorFee" and event == "FeeWithdrawn":
                    extrinsic_id = e.value["extrinsic_idx"]
                    xor_total_fee = get_event_param(e, 1)
                    # there are free tx's, thus handled via check
                    if xor_total_fee != 0:
                        # no events with this info, only estimation
                        xor_burned_estimated = int(xor_total_fee * 0.4)
                        burns.append(
                            Burn(
                                block=block,
                                timestamp=timestamp,
                                token_id=xor_id_int,
                                amount=xor_burned_estimated,
                            )
                        )
                        if len(events) > idx + 2:
                            # 50% xor is exchanged to val
                            buyback_event = events[idx + 2]
                            if (
                                buyback_event.value["module_id"] == CURRENCIES
                                and buyback_event.value["event_id"] == DEPOSITED
                            ):
                                xor_dedicated_for_buy_back = get_event_param(
                                    events[idx + 2], 2
                                )
                                buybacks.append(
                                    BuyBack(
                                        block=block,
                                        timestamp=timestamp,
                                        token_id=xor_id_int,
                                        amount=xor_dedicated_for_buy_back,
                                    )
                                )
                        if len(events) > idx + 10:
                            # exchanged val burned
                            event_with_val_burned = events[idx + 9]
                            # 10% burned val is reminted to parliament
                            event_with_val_reminted_parliament = events[idx + 10]
                            if (
                                event_with_val_burned.value["extrinsic_idx"]
                                == extrinsic_id
                                and event_with_val_reminted_parliament.value[
                                    "extrinsic_idx"
                                ]
                                == extrinsic_id
                            ):
                                if (
                                    event_with_val_burned.value["event_id"]
                                    == "Withdrawn"
                                    and event_with_val_reminted_parliament.value[
                                        "event_id"
                                    ]
                                    == "Deposited"
                                ):
                                    val_burned = get_event_param(
                                        event_with_val_burned, 2
                                    )
                                    burns.append(
                                        Burn(
                                            block=block,
                                            timestamp=timestamp,
                                            token_id=val_id_int,
                                            amount=val_burned,
                                        )
                                    )
                                    val_reminted_parliament = get_event_param(
                                        event_with_val_reminted_parliament, 2
                                    )
                                    buybacks.append(
                                        BuyBack(
                                            block=block,
                                            timestamp=timestamp,
                                            token_id=val_id_int,
                                            amount=val_reminted_parliament,
                                        )
                                    )
            if swaps or burns:
                # save instances to DB
                if swaps:
                    session.add_all(swaps)
                if burns:
                    session.add_all(burns)
                    session.add_all(buybacks)
                pending = session.commit()
        # wait for pending DB commit to finish
        if pending:
            await pending
        if not silent:
            logging.info("Updating trade volumes...")
        await update_volumes(session)


async def async_main_loop(async_session, args):
    """
    Run import in an infinite loop.
    """
    while True:
        await async_main(async_session, args.begin, args.clean, args.silent)
        if not args.silent:
            logging.info("Waiting for new blocks...")
        await asyncio.sleep(60)


if __name__ == "__main__":
    from db import async_session

    # parse command line arguments

    parser = argparse.ArgumentParser(
        description="Import swap history from Substrate node into DB."
    )
    parser.add_argument(
        "--clean",
        "-c",
        action="store_true",
        help="clean (drop) and re-create database tables before import",
    )
    parser.add_argument(
        "--silent", "-s", action="store_true", help="print no output except errors"
    )
    parser.add_argument(
        "--begin", "-b", type=int, default=1, help="first block to index"
    )
    parser.add_argument(
        "--follow", "-f", action="store_true", help="continiously poll for new blocks"
    )
    args = parser.parse_args()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(message)s",
        level=logging.WARNING if args.silent else logging.INFO,
    )
    if args.follow:
        # in follow mode import new blocks then sleep for 1 minute
        # then import again in a loop
        asyncio.run(async_main_loop(async_session, args))
    else:
        asyncio.run(async_main(async_session, args.begin, args.clean, args.silent))
