import json
import os
from datetime import datetime, timedelta
from typing import Any, Optional, Dict

class DataCache:
    """Enhanced cache manager for API and blockchain data."""
    
    def __init__(self, cache_dir: str = "cache", ttl_hours: int = 24):
        """Initialize cache with configurable TTL.
        
        Args:
            cache_dir: Directory to store cache files
            ttl_hours: Time-to-live in hours for cached data
        """
        self.cache_dir = cache_dir
        self.ttl = timedelta(hours=ttl_hours)
        os.makedirs(cache_dir, exist_ok=True)
    
    def _get_cache_file(self, orch_address: str, data_type: str) -> str:
        """Get cache file path for specific orchestrator and data type."""
        safe_address = orch_address.lower()[:8]  # Use first 8 chars of address
        return os.path.join(self.cache_dir, f"{safe_address}_{data_type}_cache.json")
    
    def save_data(self, orch_address: str, data_type: str, key: str, value: Any) -> None:
        """Save data to cache with timestamp."""
        cache_file = self._get_cache_file(orch_address, data_type)
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    cache = json.load(f)
            else:
                cache = {}
            
            cache[key] = {
                'value': value,
                'timestamp': datetime.now().isoformat()
            }
            
            with open(cache_file, 'w') as f:
                json.dump(cache, f, indent=2)
        except Exception as e:
            print(f"Error saving to cache: {e}")
    
    def get_data(self, orch_address: str, data_type: str, key: str) -> Optional[Any]:
        """Retrieve data from cache if not expired."""
        cache_file = self._get_cache_file(orch_address, data_type)
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    cache = json.load(f)
                if key in cache:
                    cached_time = datetime.fromisoformat(cache[key]['timestamp'])
                    if datetime.now() - cached_time < self.ttl:
                        return cache[key]['value']
            return None
        except Exception as e:
            print(f"Error reading from cache: {e}")
            return None
    
    def clear_cache(self, orch_address: str = None, data_type: str = None) -> None:
        """Clear specific cache or all caches."""
        if orch_address and data_type:
            cache_file = self._get_cache_file(orch_address, data_type)
            if os.path.exists(cache_file):
                os.remove(cache_file)
        else:
            # Clear all cache files
            for file in os.listdir(self.cache_dir):
                if file.endswith('_cache.json'):
                    os.remove(os.path.join(self.cache_dir, file))