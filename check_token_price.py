"""Check the historical price of a token at a given date and time."""

from get_orch_income import fetch_crypto_price, human_to_unix_time

if __name__ == "__main__":
    print("== Token Price Checker ==")
    token = input("Enter token symbol (e.g., LPT, ETH): ").strip().upper()
    currency = input("Enter currency (default: EUR): ").strip().upper() or "EUR"
    date_time = input("Enter date and time (YYYY-MM-DD HH:MM:SS): ").strip()
    decimals = input("Enter number of decimals (default: 8): ").strip()
    try:
        decimals = int(decimals) if decimals else 8
        timestamp = human_to_unix_time(human_time=date_time)
        price = fetch_crypto_price(
            crypto_symbol=token,
            target_currency=currency,
            unix_timestamp=timestamp,
        )
        print(f"\n{token} price at {date_time}: {price:.{decimals}f} {currency}")
    except Exception as e:
        print(f"Error: {e}")
