from datetime import datetime
from typing import Callable, Dict, List, Optional

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


def event_params(exdict):
    # Account id
    if "account_id" in exdict.keys():
        print("account_id", "0x" + exdict["account_id"])
    # Extrinsic hash
    if "extrinsic_hash" in exdict.keys():
        print("tx hash", "0x" + exdict["extrinsic_hash"])


def should_be_processed(exdict):
    return "call_function" in exdict.keys()


def get_fees_from_event(event) -> float:
    if event["event_id"] == "FeeWithdrawn":
        return event["params"][1]["value"]
    return 0


def get_op_id(ex_dict) -> int:
    s = ex_dict["extrinsic_hash"]
    return int(s, 16) % 255 ** 8


def is_extrinsic_success(event) -> bool:
    return event["event_id"] == "ExtrinsicSuccess"


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

    for event in extrinsicEvents:
        if event["event_id"] == "SwapSuccess":
            swap_success = True
        elif event["event_id"] == "Exchange":
            input_amount = event["params"][4]["value"]
            output_amount = event["params"][5]["value"]
            swap_fee_amount = event["params"][6]["value"]
        xor_fee = max(get_fees_from_event(event), xor_fee)
    if not swap_success:
        # TODO: add swap fail handler
        return None

    for param in ex_dict["params"]:
        if param["name"] == "input_asset_id":
            input_asset_type = param["value"]
        elif param["name"] == "output_asset_id":
            output_asset_type = param["value"]
        elif param["name"] == "swap_amount":
            if "WithDesiredInput" in param["value"]:
                input_amount = param["value"]["WithDesiredInput"]["desired_amount_in"]
                output_amount = param["value"]["WithDesiredInput"]["min_amount_out"]
            else:  # then we do it by desired output
                input_amount = param["value"]["WithDesiredOutput"]["max_amount_in"]
                output_amount = param["value"]["WithDesiredOutput"][
                    "desired_amount_out"
                ]
        elif param["name"] == "selected_source_types":
            filter_mode = (
                "SMART"
                if len(param["value"]) < 1
                else param["value"][0]
                if len(param["value"]) == 1
                else param["value"]
            )
            # TODO: handle filterMode here

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
            withdraw_asset1_type = param["value"]
        elif param["name"] == "output_asset_b":
            withdraw_asset2_type = param["value"]
        elif param["name"] == "output_a_min":
            withdraw_asset1_amount = param["value"]
        elif param["name"] == "output_b_min":
            withdraw_asset2_amount = param["value"]

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
            deposit_asset1_id = event["params"][0]["value"]
            deposit_asset1_amount = event["params"][3]["value"]
        elif event["event_id"] == "Transferred" and event["event_idx"] == 3:
            deposit_asset2_id = event["params"][0]["value"]
            deposit_asset2_amount = event["params"][3]["value"]

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
            asset_id = event["params"][0]["value"]
            bridged_amt = event["params"][2]["value"]
        elif event["event_id"] == "RequestRegistered":
            ext_tx_hash = event["params"][0]["value"]

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
            outgoing_asset_id = param["value"]
        elif param["name"] == "amount":
            outgoing_asset_amt = param["value"]
        elif param["name"] == "to":
            ext_type = param["type"]
            ext_address = param["value"]

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
            asset_id = event["params"][0]["value"]
            asset_amt = event["params"][3]["value"]

    if not claim_success:
        return None
    return ClaimTx(timestamp, xor_fee_paid, asset_id, asset_amt)


def process_rewards(timestamp, extrinsicEvents, ex_dict):
    rewards = []

    for event in extrinsicEvents:
        if event["event_id"] == "Reward":
            acctId = event["params"][0]["value"]
            rewardAmt = event["params"][1]["value"]
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
            asset_id = event["params"][0]["value"]
            amount = event["params"][3]["value"]

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
            batch_amt = event["params"][1]["value"]
    if not success:
        return None

    return BondStakeTx(get_op_id(ex_dict), timestamp, fees, batch_type, batch_amt)


def get_timestamp(result) -> str:
    res = result["block"]["extrinsics"]
    s = eval(str(res[0]))["params"][0]["value"]
    tms = s.split(".")
    ts = tms[0]
    ms = int(tms[1]) / 1000 if len(tms) > 1 else 0
    return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").timestamp()) * 1000 + ms


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
