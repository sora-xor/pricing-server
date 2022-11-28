from datetime import datetime
from typing import Callable, Dict, List, Optional
import logging

from data_models import (
    BondStakeTx,
    ClaimTx,
    Deposit,
    InBridgeTx,
    OutBridgeTx,
    SoraOp,
    Swap,
    TransferTx,
    Withdraw,
)

XOR_ID = "0x0200000000000000000000000000000000000000000000000000000000000000"
VAL_ID = "0x0200040000000000000000000000000000000000000000000000000000000000"
PSWAP_ID = "0x0200050000000000000000000000000000000000000000000000000000000000"
XOR_ACCOUNT = (
    #"0x54734f90f971a02c609b2d684e61b5574e35ac9942579a2635aada58e5d836a7"  # noqa
    "cnTQ1kbv7PBNNQrEb1tZpmK7ftiv4yCCpUQy1J2y7Y54Taiaw"  # noqa
)

CURRENCIES = "Currencies"
DEPOSITED = "Deposited"


def get_value(attribute, name = "value"):
    logging.info(">>> get_value: attribute = %s, name = %s", attribute, name)
    if isinstance(attribute, dict):
        logging.info(">>> return get_value %s = %s", name, attribute[name])
        return attribute[name]
    else:
        logging.info(">>> return get_value = %s", attribute)
        return attribute


def get_by_key_or_index(attribute, key, index: int):
    logging.info(">>> get_by_key_or_index: attribute = %s, key = %s, index = %i", attribute, key, index)
    if isinstance(attribute, dict):
        logging.info(">>> return get_by_key_or_index by key = %s, value = %s", key, attribute[key])
        return attribute[key]
    else:
        logging.info(">>> return get_by_key_or_index by index = %i, value = %s", index, attribute[index])
        return attribute[index]


def print_event(event):
    if event["event_id"] == "Exchange":
        logging.info(">>> event = %s", event)


def get_fees_from_event(event) -> float:
    if event["event_id"] == "FeeWithdrawn":
        return get_value(event["event"]["attributes"][1])
    return 0


def get_op_id(ex_dict) -> int:
    s = ex_dict["extrinsic_hash"]
    return int(s, 16)


def is_extrinsic_success(event) -> bool:
    return event["event_id"] == "ExtrinsicSuccess"


def set_xor_amount(value, current_value):
    if current_value is None or value > current_value:
        return value
    else:
        return current_value


def process_swap_transaction(timestamp, extrinsicEvents, ex_dict):
    # verify that the swap was a success
    swap_success = False

    input_asset_type = None
    output_asset_type = None
    input_amount = None
    output_amount = None

    swap_fee_amount = None
    xor_fee = 0

    filter_mode = None
    xor_amount = None

    for event in extrinsicEvents:
        print_event(event)
        if event["event_id"] == "SwapSuccess":
            swap_success = True
        elif event["event_id"] == "ExtrinsicFailed":
            swap_success = False
        elif event["event_id"] == "Endowed":
            dest, amount = event["event"]["attributes"]
            if get_value(dest) == XOR_ACCOUNT:
                xor_amount = set_xor_amount(get_value(amount), xor_amount)
        elif event["event_id"] == "Transfer" and get_value(event["attributes"][1]) == XOR_ACCOUNT:
            xor_amount = set_xor_amount(
                get_value(event['attributes'][3]), xor_amount)
        elif event["event_id"] == "Exchange":
            input_amount = get_value(event["event"]["attributes"][4])
            output_amount = get_value(event["event"]["attributes"][5])
            swap_fee_amount = get_value(event["event"]["attributes"][6])
        xor_fee = max(get_fees_from_event(event), xor_fee)
    if not swap_success:
        # TODO: add swap fail handler
        return None

    for param in ex_dict["call"]["call_args"]:
        if param["name"] == "input_asset_id":
            input_asset_type = get_value(get_value(param), "code")
        elif param["name"] == "output_asset_id":
            output_asset_type = get_value(get_value(param), "code")
        elif param["name"] == "swap_amount":
            if "WithDesiredInput" in get_value(param):
                input_amount = get_by_key_or_index(get_value(param)["WithDesiredInput"], "desired_amount_in", 0)
                output_amount = get_by_key_or_index(get_value(param)["WithDesiredInput"], "min_amount_out", 1)
            else:  # then we do it by desired output
                output_amount = get_by_key_or_index(get_value(param)["WithDesiredOutput"], "desired_amount_out", 0)
                input_amount = get_by_key_or_index(get_value(param)["WithDesiredOutput"], "max_amount_in", 1)
        elif param["name"] == "selected_source_types":
            filter_mode = get_value(param) or ["SMART"]

    if input_asset_type != XOR_ID and output_asset_type != XOR_ID:
        assert xor_amount is not None, ex_dict
    return Swap(
        get_op_id(ex_dict),
        timestamp,
        xor_fee,
        input_asset_type,
        output_asset_type,
        input_amount,
        output_amount,
        filter_mode,
        swap_fee_amount,
        xor_amount,
    )


def process_withdraw_transaction(timestamp, extrinsicEvents, ex_dict):
    withdraw_asset1_type = None
    withdraw_asset2_type = None
    withdraw_asset1_amount = None
    withdraw_asset2_amount = None

    fee_paid = 0
    success = False

    for event in extrinsicEvents:
        success = success or is_extrinsic_success(event)
        fee_paid = max(fee_paid, get_fees_from_event(event))

    for param in ex_dict["params"]:
        if param["name"] == "output_asset_a":
            withdraw_asset1_type = get_value(param)
        elif param["name"] == "output_asset_b":
            withdraw_asset2_type = get_value(param)
        elif param["name"] == "output_a_min":
            withdraw_asset1_amount = get_value(param)
        elif param["name"] == "output_b_min":
            withdraw_asset2_amount = get_value(param)

    return Withdraw(
        timestamp,
        fee_paid,
        withdraw_asset1_type,
        withdraw_asset2_type,
        withdraw_asset1_amount,
        withdraw_asset2_amount,
    )


def process_deposit_transaction(timestamp, extrinsicEvents, ex_dict):
    deposit_asset1_id = None
    deposit_asset2_id = None
    deposit_asset1_amount = None
    deposit_asset2_amount = None

    success = False
    xor_fee_paid = 0

    for event in extrinsicEvents:
        success = success or is_extrinsic_success(event)
        xor_fee_paid = max(xor_fee_paid, get_fees_from_event(event))

        if event["event_id"] == "Transferred" and event["event_idx"] == 2:
            deposit_asset1_id = get_value(event["params"][0])
            deposit_asset1_amount = get_value(event["params"][3])
        elif event["event_id"] == "Transferred" and event["event_idx"] == 3:
            deposit_asset2_id = get_value(event["params"][0])
            deposit_asset2_amount = get_value(event["params"][3])

    if not success:
        # TODO: process other events
        return None

    return Deposit(
        timestamp,
        xor_fee_paid,
        deposit_asset1_id,
        deposit_asset2_id,
        deposit_asset1_amount,
        deposit_asset2_amount,
    )


def process_in_bridge_tx(timestamp, extrinsicEvents, ex_dict):
    bridge_success = False
    asset_id = None
    bridged_amt = None
    ext_tx_hash = None  # tx hash on the external chain

    xor_fee_paid = 0

    for event in extrinsicEvents:
        bridge_success = bridge_success or is_extrinsic_success(event)
        xor_fee_paid = max(xor_fee_paid, get_fees_from_event(event))

        if event["event_id"] == "Deposited":
            asset_id = get_value(event["params"][0])
            bridged_amt = get_value(event["params"][2])
        elif event["event_id"] == "RequestRegistered":
            ext_tx_hash = get_value(event["params"][0])

    if not bridge_success:
        return None
    return InBridgeTx(timestamp, xor_fee_paid, asset_id, bridged_amt, ext_tx_hash)


def process_out_bridge_tx(timestamp, extrinsicEvents, ex_dict):
    bridge_success = False
    outgoing_asset_id = None
    outgoing_asset_amt = None
    ext_address = None
    ext_type = None

    xor_fee_paid = 0

    for param in ex_dict["params"]:
        if param["name"] == "asset_id":
            outgoing_asset_id = get_value(param)
        elif param["name"] == "amount":
            outgoing_asset_amt = get_value(param)
        elif param["name"] == "to":
            ext_type = param["type"]
            ext_address = get_value(param)

    for event in extrinsicEvents:
        # TODO: should add logic here to collect the tx fee data
        bridge_success = bridge_success or is_extrinsic_success(event)
        xor_fee_paid = max(xor_fee_paid, get_fees_from_event(event))

    if not bridge_success:
        return None

    return OutBridgeTx(
        timestamp,
        xor_fee_paid,
        outgoing_asset_id,
        outgoing_asset_amt,
        ext_address,
        ext_type,
    )


def process_claim(timestamp, extrinsicEvents, ex_dict):
    claim_success = False
    xor_fee_paid = 0

    asset_id = None
    asset_amt = None

    for event in extrinsicEvents:
        claim_success = claim_success or is_extrinsic_success(event)
        xor_fee_paid = max(xor_fee_paid, get_fees_from_event(event))

        if event["event_id"] == "Transferred" and event["event_idx"] == 1:
            asset_id = get_value(event["params"][0])
            asset_amt = get_value(event["params"][3])

    if not claim_success:
        return None
    return ClaimTx(timestamp, xor_fee_paid, asset_id, asset_amt)


def process_rewards(timestamp, extrinsicEvents, ex_dict):
    rewards = []

    for event in extrinsicEvents:
        if event["event_id"] == "Reward":
            acctId = get_value(event["params"][0])
            rewardAmt = get_value(event["params"][1])
            rewards.append((acctId, rewardAmt))
    # TODO: add return on rewards
    return None


def process_transfers(timestamp, extrinsicEvents, ex_dict):
    success = False
    asset_id = None
    amount = None
    fees = 0

    for event in extrinsicEvents:
        success = success or is_extrinsic_success(event)
        fees = max(fees, get_fees_from_event(event))
        if event["event_id"] == "Transferred" and event["event_idx"] == 2:
            asset_id = get_value(event["params"][0])
            amount = get_value(event["params"][3])

    if not success:
        return None

    return TransferTx(timestamp, fees, asset_id, amount)


def process_batch_all(timestamp, extrinsicEvents, ex_dict):
    success = False
    fees = 0

    batch_type = None
    batch_amt = None

    for event in extrinsicEvents:
        success = success or is_extrinsic_success(event)
        fees = max(fees, get_fees_from_event(event))

        if event["event_id"] == "Bonded":
            batch_type = "BOND STAKE"
            batch_amt = get_value(event["params"][1])
    if not success:
        return None

    return BondStakeTx(get_op_id(ex_dict), timestamp, fees, batch_type, batch_amt)


def get_timestamp(result) -> str:
    res = result["block"]["extrinsics"]
    s = get_value(res[0].value["call"]["call_args"][0])
    timestamp = ""
    if isinstance(s, int):
        timestamp = s
    else:
        tms = s.split(".")
        ts = tms[0]
        ms = int(tms[1]) / 1000 if len(tms) > 1 else 0
        timestamp = int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").timestamp()) * 1000 + ms
    return timestamp


def get_processing_functions() -> Dict[
    str, Callable[[str, List, Dict], Optional[SoraOp]]
]:
    return {
        "swap": process_swap_transaction,
        "withdraw_liquidity": process_withdraw_transaction,
        "deposit_liquidity": process_deposit_transaction,
        "as_multi": process_in_bridge_tx,
        "transfer_to_sidechain": process_out_bridge_tx,
        "claim": process_claim,
        "batch": process_rewards,
        "transfer": process_transfers,
        "batch_all": process_batch_all,
    }
