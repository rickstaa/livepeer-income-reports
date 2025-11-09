import json
import os
from datetime import datetime
from pathlib import Path

class PriceCache:
    def __init__(self, cache_dir: str = "price_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

    def _get_cache_file(self, crypto: str, currency: str) -> Path:
        return self.cache_dir / f"{crypto}_{currency}_prices.json"

    def get_cached_price(self, crypto: str, currency: str, timestamp: int) -> float | None:
        cache_file = self._get_cache_file(crypto, currency)
        if not cache_file.exists():
            return None
        
        try:
            with open(cache_file, 'r') as f:
                cache = json.load(f)
                return cache.get(str(timestamp))
        except Exception as e:
            print(f"Error reading cache: {e}")
            return None

    def save_price(self, crypto: str, currency: str, timestamp: int, price: float):
        cache_file = self._get_cache_file(crypto, currency)
        cache = {}
        
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cache = json.load(f)
            except Exception:
                pass

        cache[str(timestamp)] = price
        
        with open(cache_file, 'w') as f:
            json.dump(cache, f)