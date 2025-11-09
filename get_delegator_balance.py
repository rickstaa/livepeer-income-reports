"""Retrieve the delegator LPT and ETH balance on Arbitrum on a given timestamp."""

import sys
from web3 import Web3
from tabulate import tabulate
import pandas as pd
from pandas import ExcelWriter
from datetime import datetime

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from get_orch_income import (
    fetch_crypto_price,
    human_to_unix_time,
    fetch_block_number_by_timestamp,
    BONDING_MANAGER_CONTRACT,
    LPT_TOKEN_CONTRACT,
    ARB_CLIENT,
    GRAPHQL_CLIENT,
)
from gql import gql
from price_cache import PriceCache
from cache_manager import DataCache

# Initialize caches
PRICE_CACHE = PriceCache()
DATA_CACHE = DataCache()

CURRENT_ROUND_QUERY = """
query GetCurrentRound {
  protocols(first: 1) {
    currentRound {
      id
    }
  }
}
"""
UNBONDING_LOCKS_QUERY = """
query GetUnbondingLocks($delegator: String!) {
    unbondingLocks(where: {delegator: $delegator}) {
        amount
        withdrawRound
    }
}
"""


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    retry=retry_if_exception_type(Exception),
)
def fetch_current_round() -> int:
    """Fetch current round with caching."""
    cache_key = "current_round"
    cached_data = DATA_CACHE.get_data("global", "rounds", cache_key)
    if cached_data is not None:
        return cached_data

    try:
        query = gql(CURRENT_ROUND_QUERY)
        result = GRAPHQL_CLIENT.execute(query)
        current_round = int(result["protocols"][0]["currentRound"]["id"])
        DATA_CACHE.save_data("global", "rounds", cache_key, current_round)
        return current_round
    except Exception as e:
        print(f"Error fetching current round: {e}")
        return 0


def fetch_unbonding_locks_info(wallet_address: str) -> dict:
    """Fetch unbonding locks info for a delegator.

    Returns:
        Dict with locked_lpt, withdrawable_lpt amounts and detailed locks data.
    """
    try:
        current_round = fetch_current_round()

        query = gql(UNBONDING_LOCKS_QUERY)
        result = GRAPHQL_CLIENT.execute(
            query, variable_values={"delegator": wallet_address.lower()}
        )
        locks = result.get("unbondingLocks", [])

        locked_lpt = withdrawable_lpt = 0.0
        detailed_locks = []
        for lock in locks:
            amount = float(lock["amount"])
            withdraw_round = int(lock["withdrawRound"])
            is_withdrawable = withdraw_round <= current_round
            if is_withdrawable:
                withdrawable_lpt += amount
                status = "Withdrawable"
            else:
                locked_lpt += amount
                status = f"Locked"
            detailed_locks.append(
                {"amount": amount, "withdraw_round": withdraw_round, "status": status}
            )
        return {
            "locked_lpt": locked_lpt,
            "withdrawable_lpt": withdrawable_lpt,
            "current_round": current_round,
            "detailed_locks": detailed_locks,
        }
    except Exception as e:
        print(f"Error fetching unbonding locks for {wallet_address}: {e}")
        return {
            "locked_lpt": 0.0,
            "withdrawable_lpt": 0.0,
            "current_round": 0,
            "detailed_locks": [],
        }


def fetch_eth_balance(wallet_address: str, block_hash: str) -> float:
    """Fetch the ETH balance of a wallet at a specific block.

    Args:
        wallet_address: The wallet address to check.
        block_hash: The block hash to check the balance at.

    Returns:
        The ETH balance in the wallet at the specified block.
    """
    balance_wei = ARB_CLIENT.eth.get_balance(
        wallet_address, block_identifier=block_hash
    )
    return balance_wei / 10**18


def fetch_lpt_balance(wallet_address: str, block_hash: str) -> float:
    """Fetch the unbonded LPT balance of a wallet at a specific block.

    Args:
        wallet_address: The wallet address to check.
        block_hash: The block hash to check the balance at.

    Returns:
        The unbonded LPT balance in the wallet at the specified block.
    """
    return (
        LPT_TOKEN_CONTRACT.functions.balanceOf(wallet_address).call(
            block_identifier=block_hash
        )
        / 10**18
    )


def fetch_pending_fees(wallet_address: str, block_hash: str) -> float:
    """Fetch the pending fees for a delegator at a specific round.

    Args:
        wallet_address: The wallet address to check.
        block_hash: The block hash to check the pending fees at.

    Returns:
        The pending fees in ETH for the delegator at the specified round.
    """
    return (
        BONDING_MANAGER_CONTRACT.functions.pendingFees(wallet_address, 0).call(
            block_identifier=block_hash
        )
        / 10**18
    )


def fetch_pending_rewards(wallet_address: str, block_hash: str) -> float:
    """Fetch the pending rewards for a delegator at a specific round.

    Args:
        wallet_address: The wallet address to check.
        block_hash: The block hash to check the pending rewards at.

    Returns:
        The pending rewards in LPT for the delegator at the specified round.
    """
    return (
        BONDING_MANAGER_CONTRACT.functions.pendingStake(wallet_address, 0).call(
            block_identifier=block_hash
        )
        / 10**18
    )


def fetch_delegator_balances(
    wallet_addresses: list, timestamp: int, currency="EUR"
) -> tuple[dict, dict]:
    """Generate a balance report for delegator wallets (single or multiple).

    Args:
        wallet_addresses: List of wallet addresses to check.
        timestamp: The timestamp to check the balances at.
        currency: The currency for the report (default is EUR).

    Returns:
        A tuple of (balance_dict, unbonding_data_dict) containing the aggregated
        balances and detailed unbonding locks data.
    """
    block_hash = fetch_block_number_by_timestamp(timestamp=timestamp)

    # Fetch balances for each wallet and sum them.
    total_eth_balance = 0.0
    total_lpt_unbonded_balance = 0.0
    total_eth_unclaimed_fees = 0.0
    total_lpt_bonded_balance = 0.0
    total_locked_lpt = 0.0
    total_withdrawable_lpt = 0.0
    unbonding_data = {}
    for wallet_address in wallet_addresses:
        print(f"Fetching balances for {wallet_address}...")
        eth_balance = fetch_eth_balance(
            wallet_address=wallet_address, block_hash=block_hash
        )
        lpt_unbonded_balance = fetch_lpt_balance(
            wallet_address=wallet_address, block_hash=block_hash
        )
        eth_unclaimed_fees = fetch_pending_fees(
            wallet_address=wallet_address, block_hash=block_hash
        )
        lpt_bonded_balance = fetch_pending_rewards(
            wallet_address=wallet_address, block_hash=block_hash
        )
        unbonding_info = fetch_unbonding_locks_info(wallet_address)

        unbonding_data[wallet_address] = unbonding_info

        total_eth_balance += eth_balance
        total_lpt_unbonded_balance += lpt_unbonded_balance
        total_eth_unclaimed_fees += eth_unclaimed_fees
        total_lpt_bonded_balance += lpt_bonded_balance
        total_locked_lpt += unbonding_info["locked_lpt"]
        total_withdrawable_lpt += unbonding_info["withdrawable_lpt"]

    # Fetch prices once (same for all wallets at the given timestamp).
    eth_price = fetch_crypto_price(
        crypto_symbol="ETH", target_currency=currency, unix_timestamp=timestamp
    )
    lpt_price = fetch_crypto_price(
        crypto_symbol="LPT", target_currency=currency, unix_timestamp=timestamp
    )

    # Calculate values.
    eth_value = total_eth_balance * eth_price
    lpt_unbonded_value = total_lpt_unbonded_balance * lpt_price
    eth_unclaimed_fees_value = total_eth_unclaimed_fees * eth_price
    lpt_bonded_value = total_lpt_bonded_balance * lpt_price
    locked_lpt_value = total_locked_lpt * lpt_price
    withdrawable_lpt_value = total_withdrawable_lpt * lpt_price

    # Calculate total wallet value.
    total_wallet_value = (
        eth_value
        + lpt_unbonded_value
        + eth_unclaimed_fees_value
        + lpt_bonded_value
        + locked_lpt_value
        + withdrawable_lpt_value
    )

    balance_dict = {
        "eth_balance": total_eth_balance,
        "eth_value": eth_value,
        "eth_price": eth_price,
        "lpt_unbonded_balance": total_lpt_unbonded_balance,
        "lpt_unbonded_value": lpt_unbonded_value,
        "lpt_price": lpt_price,
        "eth_unclaimed_fees": total_eth_unclaimed_fees,
        "eth_unclaimed_fees_value": eth_unclaimed_fees_value,
        "lpt_bonded_balance": total_lpt_bonded_balance,
        "lpt_bonded_value": lpt_bonded_value,
        "locked_lpt": total_locked_lpt,
        "locked_lpt_value": locked_lpt_value,
        "withdrawable_lpt": total_withdrawable_lpt,
        "withdrawable_lpt_value": withdrawable_lpt_value,
        "total_wallet_value": total_wallet_value,
    }

    return balance_dict, unbonding_data


def create_balance_table(
    date_time: str, wallet_addresses: list, balances: dict, currency: str, current_round: int
) -> list:
    """Create a table for the delegator balance report.

    Args:
        date_time: The date and time of the report.
        wallet_addresses: List of wallet addresses included in the report.
        balances: A dictionary containing the balances and their values.
        currency: The currency for the report.
        current_round: The current round number.

    Returns:
        A list of lists representing the table rows.
    """
    if len(wallet_addresses) == 1:
        wallet_display = wallet_addresses[0]
    else:
        wallet_display = f"{len(wallet_addresses)} wallets (summed)"

    return [
        [
            "Wallet Address(es)",
            "",
            wallet_display,
        ],
        [
            "Timestamp",
            "",
            date_time,
        ],
        [
            "Current Round",
            "",
            str(current_round),
        ],
        [
            "ETH Price",
            "",
            f"{balances['eth_price']:.2f} {currency}",
        ],
        [
            "LPT Price",
            "",
            f"{balances['lpt_price']:.2f} {currency}",
        ],
        [
            "ETH",
            f"{balances['eth_balance']:.4f} ETH",
            f"{balances['eth_value']:.2f} {currency}",
        ],
        [
            "LPT (unbonded)",
            f"{balances['lpt_unbonded_balance']:.4f} LPT",
            f"{balances['lpt_unbonded_value']:.2f} {currency}",
        ],
        [
            "LPT (bonded)",
            f"{balances['lpt_bonded_balance']:.4f} LPT",
            f"{balances['lpt_bonded_value']:.2f} {currency}",
        ],
        [
            "LPT (locked in unbonding)",
            f"{balances['locked_lpt']:.4f} LPT",
            f"{balances['locked_lpt_value']:.2f} {currency}",
        ],
        [
            "LPT (withdrawable)",
            f"{balances['withdrawable_lpt']:.4f} LPT",
            f"{balances['withdrawable_lpt_value']:.2f} {currency}",
        ],
        [
            "ETH (unclaimed)",
            f"{balances['eth_unclaimed_fees']:.4f} ETH",
            f"{balances['eth_unclaimed_fees_value']:.2f} {currency}",
        ],
        [
            "Total Wallet Value",
            "-",
            f"{balances['total_wallet_value']:.2f} {currency}",
        ],
    ]


def create_unbonding_locks_table(wallet_addresses: list, unbonding_data: dict) -> list:
    """Create a detailed table of unbonding locks per wallet using already fetched data.

    Args:
        wallet_addresses: List of wallet addresses to check.
        unbonding_data: Dictionary containing already fetched unbonding data per wallet.

    Returns:
        A list of lists representing the unbonding locks table rows.
    """
    table = []

    try:
        for wallet_address in wallet_addresses:
            wallet_unbonding_info = unbonding_data.get(wallet_address, {})
            detailed_locks = wallet_unbonding_info.get("detailed_locks", [])

            # Format wallet address: show first 8 and last 6 characters for better visibility
            if len(wallet_address) > 20:
                wallet_display = f"{wallet_address[:8]}...{wallet_address[-6:]}"
            else:
                wallet_display = wallet_address

            if not detailed_locks:
                table.append(
                    [wallet_display, "-", "0.0000", "No unbonding locks"]
                )
            else:
                for lock in detailed_locks:
                    table.append(
                        [
                            wallet_display,
                            str(lock["withdraw_round"]),
                            f"{lock['amount']:.4f}",
                            lock["status"],
                        ]
                    )

    except Exception as e:
        print(f"Error creating unbonding locks table: {e}")
        table.append(["Error", "-", "-", "Failed to process unbonding locks"])

    return table


if __name__ == "__main__":
    print("== Delegator Arbitrum LPT/ETH Balance Report ==")

    wallet_input = input(
        "Enter delegator wallet address(es) (comma-separated for multiple): "
    ).strip()
    if not wallet_input:
        print("Wallet address is required.")
        sys.exit(1)
    wallet_addresses = [addr.strip().lower() for addr in wallet_input.split(",")]
    checksum_addresses = []
    for addr in wallet_addresses:
        try:
            checksum_addresses.append(Web3.to_checksum_address(addr))
        except Exception as e:
            print(f"Invalid wallet address: {addr}")
            sys.exit(1)
    print(f"Processing {len(checksum_addresses)} wallet(s)...")

    date_time = input("Enter date and time (YYYY-MM-DD HH:MM:SS): ").strip()
    timestamp = human_to_unix_time(human_time=date_time)
    currency = input("Enter currency (default: EUR): ").strip().upper() or "EUR"

    print("Generating balance report...")
    balances, unbonding_data = fetch_delegator_balances(
        wallet_addresses=checksum_addresses, timestamp=timestamp, currency=currency
    )
    
    # Get current round from unbonding data (it's the same for all wallets)
    current_round = 0
    if unbonding_data:
        first_wallet_data = next(iter(unbonding_data.values()))
        current_round = first_wallet_data.get("current_round", 0)
    
    table = create_balance_table(
        date_time=date_time,
        wallet_addresses=wallet_addresses,
        balances=balances,
        currency=currency,
        current_round=current_round,
    )

    print(
        tabulate(
            table,
            headers=["Metric", "Amount", "Value"],
            tablefmt="grid",
        )
    )

    # Display unbonding locks table using already fetched data
    print("\n== Unbonding Locks Details ==")
    unbonding_table = create_unbonding_locks_table(checksum_addresses, unbonding_data)
    print(
        tabulate(
            unbonding_table,
            headers=["Wallet Address", "Withdraw Round", "Amount (LPT)", "Status"],
            tablefmt="grid",
        )
    )

    print("\nExporting data to Excel...")
    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_filename = (
        f"delegator_balance_{wallet_addresses[0][:8]}_{current_time}.xlsx"
        if len(wallet_addresses) == 1
        else f"delegators_balance_{current_time}.xlsx"
    )

    df = pd.DataFrame(table, columns=["Metric", "Amount", "Value"])
    unbonding_df = pd.DataFrame(
        unbonding_table[1:], columns=unbonding_table[0]
    )  # Skip header row

    with ExcelWriter(excel_filename) as writer:
        df.to_excel(writer, sheet_name="delegator balance", index=False)
        unbonding_df.to_excel(writer, sheet_name="unbonding locks", index=False)
    print(f"Export completed: {excel_filename}")
