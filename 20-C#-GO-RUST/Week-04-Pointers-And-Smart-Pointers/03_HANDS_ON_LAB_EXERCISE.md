# Week 04 · Hands-On Lab Exercise: High-Performance Resource Pooling & RAII Guards
### Engineering Zero-Allocation Object Pools Across C#, Go, and Rust

> **Lab Objective:** In high-throughput backend services (e.g., database connection proxies, packet routers, gRPC serialization engines), allocating and discarding heavy objects creates massive GC latency spikes.
> 
> In this lab, you will architect and benchmark three distinct resource pooling paradigms:
> 1. **C#:** `Microsoft.Extensions.ObjectPool` with `IDisposable` lifecycle guarantees.
> 2. **Go:** Bounded channel-based pooling vs. `sync.Pool` (demonstrating why `sync.Pool` is cleared during GC cycles!).
> 3. **Rust:** A thread-safe, lock-free or mutex-guarded `Pool<T>` yielding an un-bypassable RAII `PoolGuard<'a, T>` that returns items automatically on `Drop`.

---

## Lab Architecture & Timetable

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LAB WORKFLOW & TIMETABLE                        │
├────────────────────────────────────────────────────────────────────────┤
│ • Day 1-2: Go Channel-Based Pool vs. sync.Pool Exploration             │
│            Implement bounded channel pool. Observe sync.Pool GC        │
│            eviction under memory pressure. Measure latency under load. │
│                                                                        │
│ • Day 3-4: Rust Custom Pool with RAII Drop Guard                       │
│            Implement Pool<T> and PoolGuard<'a, T>.                     │
│            Implement Deref/DerefMut for transparent resource access.   │
│            Implement Drop to guarantee zero-leak resource reclamation. │
│                                                                        │
│ • Day 5:   Friday Mob Review & Cross-Language Benchmarking             │
│            Compare allocation counts, analyze cache invalidation,      │
│            complete the 7-question technical defense.                  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: Days 1–2 — Go Resource Pooling Mechanics

### 1.1 The `sync.Pool` Quirk
Many C# developers assume Go's `sync.Pool` is equivalent to .NET's `ObjectPool<T>`.

> [!WARNING]
> **The `sync.Pool` Trap:**
> `sync.Pool` is designed exclusively for short-lived, transient allocations (like scratch byte buffers). The Go runtime is **explicitly permitted to purge all items in `sync.Pool` during every STW garbage collection cycle!**
> If you store expensive, persistent objects (like database sockets, TCP connections, or TLS handshake contexts) in `sync.Pool`, a sudden GC spike will drop all your connections, triggering an avalanche of reconnections.

For stateful, bounded resources, idiomatic Go uses a **buffered channel pool**.

### 1.2 The Bounded Channel Pool Implementation (`pool.go`)
Save this into `labs/week04/go/pool.go`:

```go
package main

import (
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"time"
)

var (
	ErrPoolExhausted = errors.New("resource pool exhausted; acquire timeout")
	ErrPoolClosed    = errors.New("resource pool closed")
)

type Connection struct {
	ID        int
	CreatedAt time.Time
	Queries   int64
}

func (c *Connection) Execute(query string) string {
	atomic.AddInt64(&c.Queries, 1)
	return fmt.Sprintf("Result for [%s] via Conn #%d (Total queries: %d)", query, c.ID, c.Queries)
}

func (c *Connection) Reset() {
	// Clean connection state before returning to pool
}

type BoundedPool struct {
	resources chan *Connection
	capacity  int
	isClosed  int32
	mu        sync.Mutex
}

func NewBoundedPool(capacity int) *BoundedPool {
	p := &BoundedPool{
		resources: make(chan *Connection, capacity),
		capacity:  capacity,
	}

	// Pre-fill pool with warm connections
	for i := 1; i <= capacity; i++ {
		p.resources <- &Connection{
			ID:        i,
			CreatedAt: time.Now(),
		}
	}

	return p
}

// Acquire retrieves a connection or times out after timeout duration
func (p *BoundedPool) Acquire(timeout time.Duration) (*Connection, error) {
	if atomic.LoadInt32(&p.isClosed) == 1 {
		return nil, ErrPoolClosed
	}

	select {
	case conn := <-p.resources:
		return conn, nil
	case <-time.After(timeout):
		return nil, ErrPoolExhausted
	}
}

// Release returns the connection to the channel pool
func (p *BoundedPool) Release(conn *Connection) error {
	if conn == nil {
		return nil
	}

	if atomic.LoadInt32(&p.isClosed) == 1 {
		// Pool is closed; discard resource
		return ErrPoolClosed
	}

	conn.Reset()

	select {
	case p.resources <- conn:
		return nil
	default:
		// Pool is full (overflow guard)
		return nil
	}
}

func (p *BoundedPool) Close() {
	if atomic.CompareAndSwapInt32(&p.isClosed, 0, 1) {
		close(p.resources)
	}
}
```

---

## Part 2: Days 3–4 — The Rust Custom Pool with RAII Guard

In Go and C#, you must remember to call `pool.Release(conn)` or `using var conn = pool.Get()`. If an engineer forgets, the resource is leaked until GC or timeout.

In Rust, we can make resource leakage **physically impossible** by designing an RAII **`PoolGuard`** that wraps the resource.

### 2.1 Implementing `Pool<T>` and `PoolGuard<'a, T>` (`src/pool.rs`)

```rust
// File: src/pool.rs
use std::ops::{Deref, DerefMut};
use std::sync::{Arc, Mutex};

pub trait Resettable {
    fn reset(&mut self);
}

// Simulated heavy database connection
#[derive(Debug)]
pub struct DbConnection {
    pub id: u32,
    pub queries_executed: u64,
}

impl Resettable for DbConnection {
    fn reset(&mut self) {
        // Reset query state or transaction flags
    }
}

impl DbConnection {
    pub fn query(&mut self, sql: &str) -> String {
        self.queries_executed += 1;
        format!("Executed '{}' on Conn #{}", sql, self.id)
    }
}

// ------------------------------------------------------------------------
// THE POOL DEFINITION
// ------------------------------------------------------------------------
pub struct Pool<T: Resettable> {
    items: Arc<Mutex<Vec<T>>>,
    max_capacity: usize,
}

impl<T: Resettable> Clone for Pool<T> {
    fn clone(&self) -> Self {
        Self {
            items: Arc::clone(&self.items),
            max_capacity: self.max_capacity,
        }
    }
}

impl<T: Resettable> Pool<T> {
    pub fn new(max_capacity: usize, factory: impl Fn(u32) -> T) -> Self {
        let mut initial_items = Vec::with_capacity(max_capacity);
        for i in 1..=(max_capacity as u32) {
            initial_items.push(factory(i));
        }

        Self {
            items: Arc::new(Mutex::new(initial_items)),
            max_capacity,
        }
    }

    // Acquire returns an un-bypassable RAII Guard!
    pub fn acquire(&self) -> Option<PoolGuard<T>> {
        let mut lock = self.items.lock().unwrap();
        lock.pop().map(|resource| PoolGuard {
            resource: Some(resource),
            pool: Arc::clone(&self.items),
        })
    }

    pub fn available_count(&self) -> usize {
        self.items.lock().unwrap().len()
    }
}

// ------------------------------------------------------------------------
// THE RAII POOL GUARD: The Inviolable Safety Net
// ------------------------------------------------------------------------
pub struct PoolGuard<T: Resettable> {
    resource: Option<T>,
    pool: Arc<Mutex<Vec<T>>>,
}

// Implement Deref so caller can use &T methods transparently
impl<T: Resettable> Deref for PoolGuard<T> {
    type Target = T;
    fn deref(&self) -> &Self::Target {
        self.resource.as_ref().unwrap()
    }
}

// Implement DerefMut so caller can mutate T transparently
impl<T: Resettable> DerefMut for PoolGuard<T> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        self.resource.as_mut().unwrap()
    }
}

// THE DROP TRAIT: Automatically returns resource to pool when guard leaves scope!
impl<T: Resettable> Drop for PoolGuard<T> {
    fn drop(&mut self) {
        if let Some(mut item) = self.resource.take() {
            item.reset();
            let mut lock = self.pool.lock().unwrap();
            lock.push(item);
            // Lock drops here; item is instantly returned to the pool!
        }
    }
}
```

### 2.2 Using the Pool Concurrently (`src/main.rs`)

```rust
// File: src/main.rs
mod pool;
use pool::{DbConnection, Pool};
use std::thread;
use std::time::Duration;

fn main() {
    println!("=== RUST RAII RESOURCE POOL LAB ===");

    // Create a pool of 3 DB connections
    let pool = Pool::new(3, |id| DbConnection {
        id,
        queries_executed: 0,
    });

    println!("Initial pool available: {}", pool.available_count());

    let mut handles = Vec::new();

    // Spawn 6 worker threads competing for 3 connections
    for worker_id in 1..=6 {
        let worker_pool = pool.clone();

        let handle = thread::spawn(move || {
            loop {
                // Attempt to acquire connection
                if let Some(mut conn) = worker_pool.acquire() {
                    // Use connection via DerefMut
                    let res = conn.query("SELECT * FROM users");
                    println!("[Worker {}] {}", worker_id, res);

                    thread::sleep(Duration::from_millis(50));

                    // conn DROPS HERE!
                    // RAII Drop immediately pushes connection back into worker_pool!
                    break;
                } else {
                    println!("[Worker {}] Pool empty, waiting...", worker_id);
                    thread::sleep(Duration::from_millis(20));
                }
            }
        });

        handles.push(handle);
    }

    for h in handles {
        h.join().unwrap();
    }

    println!("\nAll workers completed.");
    println!("Final pool available: {} (Expected: 3)", pool.available_count());
}
```

---

## Part 3: Friday Mob Review & Defense Protocol

Gather the 5-engineer team. Run the multi-threaded pool benchmarks and inspect memory output.

### 7 Mandatory Technical Defense Questions

#### 1. Why does Go's `sync.Pool` clear its contents during GC, and when should it be avoided?
* **Expected Answer:** `sync.Pool` is designed as a cache for transient memory allocations to reduce GC allocation rate. The runtime deliberately flushes `sync.Pool` during GC cycles to prevent stale memory bloating. It must **never** be used for persistent stateful resources (like database sockets or TCP connections) because a GC sweep would abruptly terminate all connections.

#### 2. In Rust's `PoolGuard<T>`, how does `Deref` and `DerefMut` provide "smart pointer" behavior?
* **Expected Answer:** `Deref` defines target type `Target = T` and implements `deref(&self) -> &T`. When a caller calls a method on `guard.query()`, the Rust compiler uses **Deref Coercion** to transparently look through the `PoolGuard` struct to the underlying `DbConnection`, allowing the guard to act as a seamless proxy.

#### 3. Why did we use `self.resource.take()` in `PoolGuard::drop()`?
* **Expected Answer:** `drop(&mut self)` takes a mutable reference to `self`. In safe Rust, you cannot move a value out of a borrowed reference (`self.resource`). By wrapping `T` in an `Option<T>`, `self.resource.take()` extracts the inner `T`, leaving `None` in its place, allowing us to safely move ownership of `T` back into the pool.

#### 4. How does `Arc<Mutex<Vec<T>>>` handle multi-threaded synchronization in our Rust pool?
* **Expected Answer:** `Arc` provides thread-safe shared ownership of the allocation block across threads using atomic reference counts (`LOCK XADD`). `Mutex` provides mutually exclusive access to the `Vec<T>`, ensuring that only one thread can push or pop resources at any instant, preventing data races.

#### 5. What is the difference between `Microsoft.Extensions.ObjectPool` and our Rust `Pool<T>`?
* **Expected Answer:** .NET's `ObjectPool<T>` requires the caller to explicitly call `pool.Return(item)`. If an exception bypasses `Return()`, the item is lost to the pool and must be cleaned up by the Garbage Collector. In Rust, `PoolGuard` implements `Drop`, guaranteeing that whether the function succeeds, returns early, or panics, the resource is automatically returned to the pool.

#### 6. Why did our Go `BoundedPool` use a buffered channel instead of a slice with a mutex?
* **Expected Answer:** A buffered channel provides built-in thread-safe FIFO queuing, non-blocking lock-free fast-path operations, and native integration with the `select` statement. This allows clients to implement timeouts (`case <-time.After(timeout)`) and cancellation contexts without manual condition variables.

#### 7. What is a circular reference leak in Rust, and why does the compiler allow it with `Rc`?
* **Expected Answer:** If Node A holds an `Rc<RefCell<Node>>` to Node B, and Node B holds an `Rc<RefCell<Node>>` to Node A, their strong counts never hit zero, permanently leaking memory. The Rust compiler allows this because reference counting is a runtime mechanism, and Rust's safety guarantees promise **memory safety (no use-after-free, no data races)**, but not the mathematical impossibility of memory leaks.

---

## Lab Sign-off Checklist

Each engineer must verify and sign off:
* [ ] **Channel Pool Concurrency:** I have verified that Go's buffered channel pool handles concurrent workers without data races.
* [ ] **`sync.Pool` Limitations:** I can explain why `sync.Pool` cannot be used for stateful database connections.
* [ ] **RAII Guard Architecture:** I have implemented `Deref`, `DerefMut`, and `Drop` on a custom smart pointer guard in Rust.
* [ ] **Safe Option Extraction:** I understand how `Option::take()` moves values out of a `Drop` method without unsafe code.
* [ ] **Cycle Prevention:** I can explain how `Weak<T>` prevents reference counting memory leaks in cyclical graphs.
