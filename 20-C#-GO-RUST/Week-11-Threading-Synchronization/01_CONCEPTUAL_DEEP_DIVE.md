# Week 11: Multi-Threading & Shared-Memory Synchronization

## Why This Week Matters for Your Career Transition
For a C# developer, threading often means relying on `Task`, `ConcurrentDictionary`, or throwing a `lock (obj)` block around critical sections without a second thought. You are accustomed to a garbage-collected heap where threads freely access shared memory, and reentrant locks silently save you from deadlocks. As you transition, you will realize this model is deeply flawed. By the end of this week, you will understand why Go explicitly bans reentrant locks, how Rust uses the type system (`Send` and `Sync`) to make data races mathematically impossible at compile time, and why locking *data* (Rust) is fundamentally superior to locking *code* (C# and Go).

## The Baseline: C# and the CLR ThreadPool
C# relies heavily on the CLR ThreadPool and OS threads. 
### Locks and Reentrancy
In C#, `lock(obj)` syntactic sugar expands to `Monitor.Enter`. This lock is **reentrant**, meaning if Thread A holds the lock, Thread A can enter it again without deadlocking. While this seems convenient, reentrancy hides invariant violations. If you hold a lock, it implies the data is in an intermediate, invalid state. If you call another method that re-enters the lock, it observes that invalid state.
### Concurrent Collections
`ConcurrentDictionary` in C# uses fine-grained locking (lock striping) per bucket. It is highly optimized, but obscures the underlying memory barriers from the developer.

## Go: Concurrency as a First-Class Citizen
Go's concurrency model (goroutines) maps thousands of lightweight green threads onto a few OS threads via the Go runtime scheduler.

### `sync.Mutex` and the Ban on Reentrancy
Go's `sync.Mutex` is explicitly **non-reentrant**. If a goroutine tries to lock a mutex it already holds, it deadlocks and panics. This forces you to write cleaner, strictly layered code where lock acquisition happens at the boundaries of your APIs, not hidden deep in nested function calls.

### The Race Detector (`go test -race`)
Because Go (like C#) allows shared memory, data races are entirely possible. Go provides a legendary tool: the race detector. It instruments your code at compile time to track memory accesses and panics if two goroutines access the same memory concurrently without synchronization.

### Other Sync Primitives
- **`sync.WaitGroup`**: Essential for fork-join patterns.
- **`sync.Once`**: Guarantees initialization logic runs exactly once, safely.
- **`sync.Pool`**: Used strictly to reduce Garbage Collector pressure by reusing objects.

## Rust: Fearless Concurrency via the Type System
Rust completely changes the paradigm. In C# and Go, you protect *code* with locks. In Rust, you protect *data*.

### The `Send` and `Sync` Marker Traits
These two traits are the bedrock of Rust's thread safety:
- **`Send`**: Types that can safely be *moved* to another thread.
- **`Sync`**: Types that can safely be *shared* between threads (i.e., `&T` is `Send`).
If you try to pass an `Rc<T>` (single-threaded reference counted pointer) across threads, the compiler rejects it because `Rc` does not implement `Send`.

### `Arc<T>` and `Mutex<T>`
To share state, you wrap it in an `Arc` (Atomic Reference Counted pointer). To mutate it, you wrap it in a `Mutex`.
```rust
let data = Arc::new(Mutex::new(HashMap::new()));
```
Notice that the `HashMap` is *inside* the `Mutex`. You cannot access the map without calling `.lock().unwrap()`. The lock returns a `MutexGuard`, a smart pointer that dereferences to the map. When the guard goes out of scope, the lock is automatically dropped.

## Common Misconceptions to Unlearn
1.  **"Locks should be reentrant."** Reentrant locks hide design flaws. If you need reentrancy, your concurrency boundaries are wrong.
2.  **"I don't need locks if I just read data."** In C#/Go, unsynchronized reads while another thread writes can lead to torn reads or visibility issues (stale CPU caches).
3.  **"Rust's thread safety is just about avoiding deadlocks."** False. Rust prevents *data races*, but it **does not** prevent deadlocks. You can easily deadlock Rust code.

## Summary Table

| Feature | C# (.NET) | Go | Rust |
| :--- | :--- | :--- | :--- |
| **Default Lock** | `Monitor` (via `lock`) | `sync.Mutex` | `std::sync::Mutex` |
| **Reentrancy** | Yes | No | No |
| **Protects...** | Code block | Code block | The Data itself |
| **Data Race Prevention**| Developer Discipline | `go test -race` (Runtime) | Type System (`Send`/`Sync`) (Compile Time) |
| **Shared Pointers** | Garbage Collected | Garbage Collected | `Arc<T>` (Atomic Ref Count) |
