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
import substrateinterface
from substrateinterface import SubstrateInterface
from tqdm import trange

from models import Burn, BuyBack, Pair, Swap, Token
from processing import (
    CURRENCIES,
    DEPOSITED,
    PSWAP_ID,
    VAL_ID,
    XOR_ID,
    XSTUSD_ID,
    KUSD_ID,
    VXOR_ID,
    get_processing_functions,
    get_timestamp,
    get_value,
)

# Enable logging of RPC requests
# substrateinterface.logger.setLevel(logging.DEBUG)

DENOM = Decimal(10 ** 18)

SWAP_FEE_ASSETS = {}

def get_fee_price_func(substrate, block_hash, pairs):
    xor_id_int = int(XOR_ID, 16)
    for asset_id in list(SWAP_FEE_ASSETS):
        asset_id_int = int(asset_id, 16)
        if (asset_id_int, xor_id_int) in pairs and pairs[asset_id_int, xor_id_int].quote_price is not None:
            SWAP_FEE_ASSETS[asset_id] = float(pairs[asset_id_int, xor_id_int].quote_price)

    def get_fee_price(asset_id):
        if asset_id in SWAP_FEE_ASSETS:
            return SWAP_FEE_ASSETS[asset_id]

        asset_id_int = int(asset_id, 16)
        if (asset_id_int, xor_id_int) in pairs and pairs[asset_id_int, xor_id_int].quote_price is not None:
            SWAP_FEE_ASSETS[asset_id] = float(pairs[asset_id_int, xor_id_int].quote_price)
            return SWAP_FEE_ASSETS[asset_id]
        
        params = [
            0,
            asset_id,
            XOR_ID,
            "1000000000000000000",
            "WithDesiredInput",
            [],
            "Disabled",
            block_hash,
        ]
        result = substrate.rpc_request("liquidityProxy_quote", params)
        price = 0
        if result["result"] is not None:
            price = int(result["result"]["amount_without_impact"]) / DENOM

        SWAP_FEE_ASSETS[asset_id] = float(price)
        return price
    return get_fee_price

def connect_to_substrate_node():
    try:
        substrate = SubstrateInterface(
            url=decouple.config("SUBSTRATE_URL", "ws://127.0.0.1:9944"),
            type_registry=load_type_registry_file("custom_types.json"),
            ss58_format=69,
        )
        return substrate
    except ConnectionRefusedError:
        logging.error(
            "⚠️ No local Substrate node running, try running 'start_local_substrate_node.sh' first"  # noqa
        )
        return None


def connect_to_substrate_node_mst():
    try:
        substrate = SubstrateInterface(
            url=decouple.config("SUBSTRATE_URL", "ws://127.0.0.1:9944"),
            type_registry=load_type_registry_file("custom_types_mst.json"),
            ss58_format=69,
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
    result = substrate.get_block(
        block_hash=block_hash
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
    return block_hash, events, result, grouped_events


def process_events(dataset, func_map, result, grouped_events, get_fee_price):
    """
    Call function from func_map for every extrinsic depending on extrinsic type.
    """
    extrinsic_idx = 0
    timestamp = get_timestamp(result)

    for extrinsic in result["extrinsics"]:
        extrinsic_events = grouped_events[extrinsic_idx]
        extrinsic_idx += 1
        exdict = extrinsic and extrinsic.value
        if exdict and "call" in exdict.keys():
            tx_type = exdict["call"]["call_function"]
            processing_func = func_map.get(tx_type)
            if processing_func:
                tx = processing_func(timestamp, extrinsic_events, exdict, get_fee_price)
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
                id=id, name=a["name"], symbol=a["symbol"], decimals=int(
                    a["precision"])
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
        select(Pair).options(selectinload(
            Pair.from_token), selectinload(Pair.to_token))
    ):
        pairs[p.from_token.id, p.to_token.id] = p
    return pairs


async def update_volumes(session):
    """
    Update Pair.from_volume, Pair.to_volume and Token.trade_volume.
    """
    last_24h = (time() - 24 * 3600) * 1000
    from_amounts = session.execute(
        select(Pair.from_token_id, func.sum(Swap.from_amount))
        .where(Swap.timestamp > last_24h)
        .join(Pair, Pair.id == Swap.pair_id)
        .group_by(Pair.from_token_id)
    )
    to_amounts = session.execute(
        select(Pair.to_token_id, func.sum(Swap.to_amount))
        .where(Swap.timestamp > last_24h)
        .join(Pair, Pair.id == Swap.pair_id)
        .group_by(Pair.to_token_id)
    )
    burn_amounts = session.execute(
        select(Burn.token_id, func.sum(Burn.amount))
        .where(Burn.timestamp > last_24h)
        .group_by(Burn.token_id)
    )
    buyback_amounts = session.execute(
        select(BuyBack.token_id, func.sum(BuyBack.amount))
        .where(BuyBack.timestamp > last_24h)
        .group_by(BuyBack.token_id)
    )
    tokens = session.execute(select(Token))
    from_amounts = dict(list(await from_amounts))
    to_amounts = dict(list(await to_amounts))
    burn_amounts = dict(list(await burn_amounts))
    buyback_amounts = dict(list(await buyback_amounts))
    objects = []
    for token in (await tokens).scalars().all():
        volume = (
            from_amounts.get(token.id, 0)
            + to_amounts.get(token.id, 0)
            + burn_amounts.get(token.id, 0)
            + buyback_amounts.get(token.id, 0)
        ) / Decimal(10 ** token.decimals)
        token.trade_volume = volume
        objects.append(token)
    session.add_all(objects)
    await session.execute(
        update(Pair).values(
            from_volume=select(func.sum(Swap.from_amount / DENOM))
            .where(and_(Swap.pair_id == Pair.id, Swap.timestamp > last_24h))
            .scalar_subquery(),
            to_volume=select(func.sum(Swap.to_amount / DENOM))
            .where(and_(Swap.pair_id == Pair.id, Swap.timestamp > last_24h))
            .scalar_subquery(),
        )
    )
    await session.commit()


def get_event_param(event, param_idx):
    attribute = event.value["event"]["attributes"][param_idx]
    return get_value(attribute)


async def async_main(async_session, begin=1, clean=False, silent=False):
    # if clean:
    #     async with db.engine.begin() as conn:
    #         await conn.run_sync(models.Base.metadata.drop_all)
    #         await conn.run_sync(models.Base.metadata.create_all)

    def get_end(substrate: SubstrateInterface):
        block_hash = substrate.get_chain_finalised_head()
        block = substrate.get_block(block_hash)
        return block["header"]["number"]

    # get the number of last block in the chain
    substrate = connect_to_substrate_node()
    try:
        end = get_end(substrate)
    except:
        substrate = connect_to_substrate_node_mst()
        end = get_end(substrate)
        substrate = connect_to_substrate_node()

    selected_events = {"swap"}
    func_map = {
        k: v for k, v in get_processing_functions().items() if k in selected_events
    }
    xor_id_int = int(XOR_ID, 16)
    xstusd_id_int = int(XSTUSD_ID, 16)
    val_id_int = int(VAL_ID, 16)
    pswap_id_int = int(PSWAP_ID, 16)
    kusd_id_int = int(KUSD_ID, 16)
    vxor_id_int = int(VXOR_ID, 16)

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
        # make sure XOR, XSTUSD, VAL and PSWAP token entries created
        # be able to import burns and buybacks
        await get_or_create_token(substrate, session, xor_id_int)
        await get_or_create_token(substrate, session, xstusd_id_int)
        await get_or_create_token(substrate, session, val_id_int)
        await get_or_create_token(substrate, session, pswap_id_int)
        for block in (range if silent or not sys.stdout.isatty() else trange)(
            begin, end
        ):
            # get events from <block> to <dataset>
            dataset = []
            try:
                block_hash, events, res, grouped_events = get_events_from_block(
                    substrate, block)
            except:
                substrate = connect_to_substrate_node_mst()
                block_hash, events, res, grouped_events = get_events_from_block(
                    substrate, block)
                substrate = connect_to_substrate_node()

            timestamp = get_timestamp(res)
            get_fee_price = get_fee_price_func(substrate, block_hash, pairs)
            process_events(dataset, func_map, res, grouped_events, get_fee_price)
            # await previous INSERT to finish if any
            if pending:
                await pending
                pending = None
            # prepare data to be INSERTed
            swaps = []
            for tx in dataset:
                try:
                    # skip transactions with invalid asset type 0x000....0
                    input_asset_id = tx.pop("input_asset_id")
                    output_asset_id = tx.pop("output_asset_id")
                    from_asset = int(input_asset_id, 16)
                    to_asset = int(output_asset_id, 16)
                    if not from_asset or not to_asset:
                        continue
                    
                    intermediate_amounts = tx.pop("intermediate_amounts")
                    
                    dex_id = tx.pop("dex_id")
                    if len(intermediate_amounts) == 0:
                        data = [
                            (
                                from_asset,
                                tx.pop("in_amount"),
                                to_asset,
                                tx.pop("out_amount"),
                            )
                        ]
                    else:
                        for idx, (asset_id, amount) in enumerate(intermediate_amounts):
                            current_asset_id = int(asset_id, 16)
                            if idx == 0:
                                data = [
                                    (
                                        from_asset,
                                        tx.pop("in_amount"),
                                        current_asset_id,
                                        amount,
                                    )
                                ]
                            else:
                                data.append(
                                    (
                                        int(intermediate_amounts[idx - 1][0], 16),  # Previous asset_id
                                        intermediate_amounts[idx - 1][1],  # Previous amount
                                        current_asset_id,
                                        amount,
                                    )
                                )

                            if idx == len(intermediate_amounts) - 1:
                                data.append(
                                    (
                                        current_asset_id,
                                        amount,
                                        to_asset,
                                        tx.pop("out_amount"),
                                    )
                            )
                    tx["filter_mode"] = tx["filter_mode"][0]
                    tx["txid"] = tx.pop("id")
                    for from_asset, from_amount, to_asset, to_amount in data:
                        tx["pair_id"] = (
                            await get_or_create_pair(
                                substrate, session, pairs, from_asset, to_asset
                            )
                        ).id
                        swaps.append(
                            (
                                dex_id,
                                from_asset,
                                to_asset,
                                Swap(
                                    block=block,
                                    from_amount=from_amount,
                                    to_amount=to_amount,
                                    **tx
                                )
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
            parsed_swaps = []
            for swap in swaps:
                dex_id = swap[0]
                if swap[0] == 0:
                    base_id = XOR_ID
                    base_id_int = xor_id_int
                if swap[0] == 1:
                    base_id = XSTUSD_ID
                    base_id_int = xstusd_id_int
                if swap[0] == 2:
                    base_id = KUSD_ID
                    base_id_int = kusd_id_int
                if swap[0] == 3:
                    base_id = VXOR_ID
                    base_id_int = vxor_id_int
                other_asset = swap[1] if swap[2] == base_id_int else swap[2]
                other_asset = "{0:#0{1}x}".format(other_asset, 66)
                if swap[1] == base_id_int:
                    input_asset_id, output_asset_id = base_id, other_asset
                elif swap[2] == base_id_int:
                    input_asset_id, output_asset_id = other_asset, base_id
                else:
                    input_asset_id, output_asset_id = "{0:#0{1}x}".format(swap[1], 66), "{0:#0{1}x}".format(swap[2], 66)
                params = [
                    dex_id,
                    input_asset_id,
                    output_asset_id,
                    "1000000000000000000",
                    "WithDesiredInput",
                    [],
                    "Disabled",
                    block_hash,
                ]
                result = substrate.rpc_request("liquidityProxy_quote", params)
                pair = pairs[swap[1], swap[2]]
                if result["result"] is not None:
                    pair.quote_price = int(result["result"]["amount_without_impact"]) / DENOM
                else:
                    pair.quote_price = None
                session.add(pair)
                parsed_swaps.append(swap[3])
            if parsed_swaps or burns:
                # save instances to DB
                if parsed_swaps:
                    session.add_all(parsed_swaps)
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
        await asyncio.sleep(decouple.config("POLL_INTERVAL", default=600, cast=int))


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
        asyncio.run(async_main(async_session,
                    args.begin, args.clean, args.silent))
