"""Unified caching utilities for this project."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Optional, Dict


class CacheStore:
    """Generic JSON file-backed cache with in-memory layer, TTL, and atomic writes.

    - One JSON file per group under a base directory.
    - Entries stored as { key: {"value": Any, "timestamp": iso-str} }.
    - TTL is enforced on reads (lazy expiry). Atomic writes prevent corruption.
    """

    def __init__(self, base_dir: str, ttl: Optional[timedelta] = None):
        """Initialize the cache store.

        Args:
            base_dir: Directory where cache JSON files will be stored.
            ttl: Optional time-to-live for each entry. If None, entries never expire
                (PriceCache case). If provided, entries older than the TTL are treated
                as missing when read.
        """
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        self._memory: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._locks: Dict[str, RLock] = {}

    def _sanitize(self, name: str) -> str:
        return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in name)

    def _file_for(self, group: str) -> Path:
        return self.base / f"{self._sanitize(group)}.json"

    def _lock(self, group: str) -> RLock:
        if group not in self._locks:
            self._locks[group] = RLock()
        return self._locks[group]

    def _now_iso(self) -> str:
        return datetime.now().isoformat()

    def _expired(self, iso_ts: Optional[str]) -> bool:
        if self.ttl is None:
            return False
        if not iso_ts:
            return True
        try:
            ts = datetime.fromisoformat(iso_ts)
        except Exception:
            return True
        return datetime.now() - ts > self.ttl

    def _load(self, group: str) -> Dict[str, Dict[str, Any]]:
        """Load a group's JSON file into memory if not already loaded.

        Args:
            group: Logical group name (file stem) to load.

        Returns:
            A dictionary mapping keys to entry dicts of shape {"value": Any,
            "timestamp": str}.
        """
        if group in self._memory:
            return self._memory[group]
        fp = self._file_for(group)
        data: Dict[str, Dict[str, Any]] = {}
        if fp.exists():
            try:
                with open(fp, "r") as f:
                    raw = json.load(f)
                    if isinstance(raw, dict):
                        data = raw
            except Exception:
                # Backup corrupted file and start fresh.
                try:
                    fp.rename(fp.with_suffix(".corrupted"))
                except Exception:
                    pass
                data = {}
        self._memory[group] = data
        return data

    def get(self, group: str, key: str) -> Optional[Any]:
        """Retrieve a cached value.

        Args:
            group: Group/file identifier.
            key: Entry key within the group.

        Returns:
            The cached value if present and not expired; otherwise None.
        """
        with self._lock(group):
            data = self._load(group)
            item = data.get(key)
            if not item:
                return None
            if self._expired(item.get("timestamp")):
                return None
            return item.get("value")

    def save(self, group: str, key: str, value: Any) -> None:
        """Persist a value in the cache.

        Args:
            group: Group/file identifier.
            key: Entry key.
            value: JSON-serializable value to store.
        """
        with self._lock(group):
            data = self._load(group)
            data[key] = {"value": value, "timestamp": self._now_iso()}
            self._atomic_write(group, data)

    def _atomic_write(self, group: str, data: Dict[str, Dict[str, Any]]):
        """Write data atomically for a group.

        Args:
            group: Group/file identifier.
            data: Full data dictionary to serialize for the group.
        """
        fp = self._file_for(group)
        tmp = fp.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, separators=(",", ":"), sort_keys=True)
            os.replace(tmp, fp)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass

    def clear(self, group: Optional[str] = None) -> None:
        """Clear cached data.

        Args:
            group: If provided, only that group's file and in-memory entry are
                removed. If None, all JSON cache files under base_dir are
                deleted and in-memory data cleared.
        """
        if group is not None:
            fp = self._file_for(group)
            try:
                if fp.exists():
                    fp.unlink()
            except Exception:
                pass
            self._memory.pop(group, None)
            self._locks.pop(group, None)
            return
        for path in self.base.glob("*.json"):
            try:
                path.unlink()
            except Exception:
                pass
        self._memory.clear()
        self._locks.clear()


class DataCache:
    """Enhanced cache manager for API and blockchain data (TTL-based)."""

    def __init__(self, cache_dir: str = ".cache/data", ttl_hours: int = 24):
        """Initialize a DataCache wrapper.

        Args:
            cache_dir: Directory where data cache files are stored.
            ttl_hours: Time-to-live (hours) for entries; expired entries are
                treated as misses.
        """
        self.ttl = timedelta(hours=ttl_hours)
        self.store = CacheStore(base_dir=cache_dir, ttl=self.ttl)

    def _group(self, orch_address: str, data_type: str) -> str:
        """Compute the group (file stem) for an address + data type.

        Args:
            orch_address: Orchestrator / wallet address.
            data_type: Logical data category (e.g., 'pending_stake').

        Returns:
            File stem used for caching (without extension).
        """
        safe_address = orch_address.lower()[:8]
        return f"{safe_address}_{data_type}_cache"

    def save_data(
        self, orch_address: str, data_type: str, key: str, value: Any
    ) -> None:
        """Save a value under (address, data_type, key).

        Args:
            orch_address: Orchestrator / wallet address.
            data_type: Logical data category.
            key: Entry key within that category.
            value: JSON-serializable value to cache.
        """
        self.store.save(
            group=self._group(orch_address, data_type), key=key, value=value
        )

    def get_data(self, orch_address: str, data_type: str, key: str) -> Optional[Any]:
        """Retrieve a cached value.

        Args:
            orch_address: Orchestrator / wallet address.
            data_type: Logical data category.
            key: Entry key.

        Returns:
            Cached value or None if missing/expired.
        """
        return self.store.get(group=self._group(orch_address, data_type), key=key)

    def clear_cache(self, orch_address: str = None, data_type: str = None) -> None:
        """Clear cached data.

        Args:
            orch_address: If provided with data_type, clears only that file.
            data_type: Category for the provided address.
                If both None, clears all data cache files.
        """
        if orch_address and data_type:
            self.store.clear(group=self._group(orch_address, data_type))
        else:
            self.store.clear()


class PriceCache:
    """Price cache persisting one JSON per crypto/currency pair."""

    def __init__(self, cache_dir: str = ".cache/prices"):
        """Initialize a PriceCache (no TTL).

        Args:
            cache_dir: Directory for price cache files.
        """
        self._store = CacheStore(
            base_dir=cache_dir, ttl=None
        )  # Historical prices shouldn't expire; no TTL

    def _group(self, crypto: str, currency: str) -> str:
        """Compute group/file stem for a price pair.

        Args:
            crypto: Symbol of the crypto asset (e.g., 'ETH').
            currency: Fiat/target currency symbol (e.g., 'EUR').

        Returns:
            File stem used for caching (without extension).
        """
        return f"{crypto}_{currency}_prices"

    def get_cached_price(
        self, crypto: str, currency: str, timestamp: int
    ) -> float | None:
        """Get a cached historical price.

        Args:
            crypto: Cryptocurrency symbol (e.g., 'ETH').
            currency: Target currency symbol (e.g., 'EUR').
            timestamp: Unix timestamp for the desired historical price.

        Returns:
            The cached price as float, or None if not cached.
        """
        key = str(int(timestamp))
        value = self._store.get(group=self._group(crypto, currency), key=key)
        return None if value is None else float(value)

    def save_price(self, crypto: str, currency: str, timestamp: int, price: float):
        """Cache a historical price value.

        Args:
            crypto: Cryptocurrency symbol.
            currency: Target currency symbol.
            timestamp: Unix timestamp of the price.
            price: The price value to store.
        """
        key = str(int(timestamp))
        self._store.save(
            group=self._group(crypto, currency), key=key, value=float(price)
        )

    def get_or_fetch(
        self,
        crypto: str,
        currency: str,
        timestamp: int,
        fetch_fn: Any,
    ) -> float:
        """Return cached price or fetch + cache if missing.

        Args:
            crypto: Cryptocurrency symbol.
            currency: Target currency symbol.
            timestamp: Unix timestamp.
            fetch_fn: Callable with signature (crypto: str, currency: str,
                timestamp: int) -> float that retrieves the price.

        Returns:
            The price as a float (cached or freshly fetched).
        """
        cached = self.get_cached_price(crypto, currency, timestamp)
        if cached is not None:
            return cached
        price = float(fetch_fn(crypto, currency, timestamp))
        self.save_price(crypto, currency, timestamp, price)
        return price
