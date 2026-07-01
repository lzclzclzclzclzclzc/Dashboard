/**
 * Simple in-memory TTL cache with retry-after-on-failure.
 *
 *   const cache = new Cache();
 *   const value = await cache.get("key", 900, 5000, async () => { ... });
 */
class Cache {
  constructor() {
    this.store = new Map();
  }

  async get(key, ttlMs, retryMs, fn) {
    const now = Date.now();
    const entry = this.store.get(key);
    if (entry) {
      if (now < entry.retryAfter) return entry.value;
      if (now - entry.at < ttlMs) return entry.value;
    }
    try {
      const value = await fn();
      this.store.set(key, { at: now, value, retryAfter: now });
      return value;
    } catch (err) {
      console.error(`[cache] ${key} error: ${err.message}`);
      this.store.set(key, { at: now, value: null, retryAfter: now + retryMs });
      return null;
    }
  }
}

module.exports = Cache;
