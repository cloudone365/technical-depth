# Week 11: Rosetta Stone - Thread-Safe In-Memory Cache

This week, we will build a thread-safe LRU/Expiration Cache. It must support `Set(key, value, ttl)`, `Get(key)`, and have a background thread that periodically cleans up expired items.

## 1. C#: `ConcurrentDictionary` and `Task.Run`

C# provides robust concurrent collections. We wrap `ConcurrentDictionary` and use an async background task for eviction.

```csharp
using System;
using System.Collections.Concurrent;
using System.Threading;
using System.Threading.Tasks;

public class CacheItem {
    public string Value { get; set; }
    public DateTime Expiration { get; set; }
}

public class MemoryCache {
    // WHY: ConcurrentDictionary handles internal locking per bucket.
    private readonly ConcurrentDictionary<string, CacheItem> _store = new();
    private long _hits = 0;
    private long _misses = 0;
    private long _evictions = 0;

    public MemoryCache() {
        // Start background eviction task
        Task.Run(EvictionLoop);
    }

    public void Set(string key, string value, TimeSpan ttl) {
        _store[key] = new CacheItem { 
            Value = value, 
            Expiration = DateTime.UtcNow.Add(ttl) 
        };
    }

    public string Get(string key) {
        if (_store.TryGetValue(key, out var item)) {
            if (DateTime.UtcNow < item.Expiration) {
                // WHY: Interlocked provides atomic increments for thread-safe counters.
                Interlocked.Increment(ref _hits);
                return item.Value;
            }
            // Expired on read
            _store.TryRemove(key, out _);
            Interlocked.Increment(ref _evictions);
        }
        Interlocked.Increment(ref _misses);
        return null;
    }

    private async Task EvictionLoop() {
        while (true) {
            await Task.Delay(TimeSpan.FromSeconds(5));
            var now = DateTime.UtcNow;
            foreach (var kvp in _store) {
                if (now >= kvp.Value.Expiration) {
                    if (_store.TryRemove(kvp.Key, out _)) {
                        Interlocked.Increment(ref _evictions);
                    }
                }
            }
        }
    }
}
```

## 2. Go: `sync.RWMutex` and Background Goroutines

Go requires manual locking. We use an `RWMutex` to allow concurrent readers.

```go
package main

import (
	"sync"
	"sync/atomic"
	"time"
)

type CacheItem struct {
	Value      string
	Expiration time.Time
}

type MemoryCache struct {
	// WHY: RWMutex protects the map, which is not thread-safe in Go.
	mu        sync.RWMutex
	store     map[string]CacheItem
	
	// WHY: atomic counters avoid locking overhead for simple metrics.
	hits      atomic.Int64
	misses    atomic.Int64
	evictions atomic.Int64
}

func NewMemoryCache() *MemoryCache {
	c := &MemoryCache{
		store: make(map[string]CacheItem),
	}
	// WHY: Goroutine kicked off for background eviction.
	go c.evictionLoop()
	return c
}

func (c *MemoryCache) Set(key, value string, ttl time.Duration) {
	c.mu.Lock() // Writer lock
	defer c.mu.Unlock()
	
	c.store[key] = CacheItem{
		Value:      value,
		Expiration: time.Now().Add(ttl),
	}
}

func (c *MemoryCache) Get(key string) (string, bool) {
	c.mu.RLock() // Reader lock
	item, exists := c.store[key]
	c.mu.RUnlock()

	if exists {
		if time.Now().Before(item.Expiration) {
			c.hits.Add(1)
			return item.Value, true
		}
		// WHY: We found it but it's expired. We must drop the RLock, acquire Lock, 
        // and safely delete it.
		c.mu.Lock()
		delete(c.store, key)
		c.mu.Unlock()
		c.evictions.Add(1)
	}
	
	c.misses.Add(1)
	return "", false
}

func (c *MemoryCache) evictionLoop() {
	ticker := time.NewTicker(5 * time.Second)
	for range ticker.C {
		now := time.Now()
		c.mu.Lock() // Hold writer lock while iterating
		for k, v := range c.store {
			if now.After(v.Expiration) {
				delete(c.store, k)
				c.evictions.Add(1)
			}
		}
		c.mu.Unlock()
	}
}
```

## 3. Rust: `Arc<RwLock<T>>`

In Rust, the lock owns the data. We share ownership with the background thread using `Arc`.

```rust
use std::collections::HashMap;
use std::sync::{Arc, RwLock};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::thread;
use std::time::{Duration, Instant};

struct CacheItem {
    value: String,
    expiration: Instant,
}

// WHY: Clone on the Arc increments the ref count, allowing safe sharing across threads.
#[derive(Clone)]
pub struct MemoryCache {
    // WHY: Arc allows shared ownership. RwLock protects the HashMap.
    store: Arc<RwLock<HashMap<String, CacheItem>>>,
    hits: Arc<AtomicUsize>,
    misses: Arc<AtomicUsize>,
    evictions: Arc<AtomicUsize>,
}

impl MemoryCache {
    pub fn new() -> Self {
        let cache = MemoryCache {
            store: Arc::new(RwLock::new(HashMap::new())),
            hits: Arc::new(AtomicUsize::new(0)),
            misses: Arc::new(AtomicUsize::new(0)),
            evictions: Arc::new(AtomicUsize::new(0)),
        };

        // Clone the Arcs to move into the background thread
        let store_clone = Arc::clone(&cache.store);
        let evictions_clone = Arc::clone(&cache.evictions);

        thread::spawn(move || {
            loop {
                thread::sleep(Duration::from_secs(5));
                let now = Instant::now();
                
                // WHY: We acquire a write lock. The lock guard is dropped automatically 
                // at the end of the block.
                let mut map = store_clone.write().unwrap();
                let original_len = map.len();
                map.retain(|_, v| v.expiration > now);
                let removed = original_len - map.len();
                
                evictions_clone.fetch_add(removed, Ordering::Relaxed);
            }
        });

        cache
    }

    pub fn set(&self, key: String, value: String, ttl: Duration) {
        let mut map = self.store.write().unwrap();
        map.insert(key, CacheItem {
            value,
            expiration: Instant::now() + ttl,
        });
    }

    pub fn get(&self, key: &str) -> Option<String> {
        let map = self.store.read().unwrap();
        
        if let Some(item) = map.get(key) {
            if Instant::now() < item.expiration {
                self.hits.fetch_add(1, Ordering::Relaxed);
                return Some(item.value.clone());
            }
        }
        
        self.misses.fetch_add(1, Ordering::Relaxed);
        // Note: active read-deletion is omitted here to avoid lock upgrading complexity
        None
    }
}
```

### Critical Observations for C# Developers
1. **Lock Upgrades**: In Go, deleting an expired item during a `Get` requires dropping the `RLock` and acquiring the `Lock`. Rust enforces this strictly: you cannot mutate via a read lock guard.
2. **Data Ownership**: In C# and Go, the lock and the map are separate fields. You could accidentally access `store` without holding `mu`. In Rust, `HashMap` is physically inside `RwLock`. It is impossible to access it without calling `.read()` or `.write()`.
3. **Atomics and Memory Ordering**: Rust makes memory ordering explicit (e.g., `Ordering::Relaxed`). C# `Interlocked` defaults to a full memory fence.
