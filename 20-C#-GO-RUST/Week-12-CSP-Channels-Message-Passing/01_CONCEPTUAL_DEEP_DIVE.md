# Week 12: Communicating Sequential Processes (CSP) & Message Passing

## Why This Week Matters for Your Career Transition
"Do not communicate by sharing memory; instead, share memory by communicating." This Go proverb is a radical departure from the C# mindset. In C#, you synchronize concurrent tasks primarily using `lock`, `SemaphoreSlim`, or `ConcurrentQueue`. As you dive into Go and Rust, you will encounter the **Actor Model** and **CSP (Communicating Sequential Processes)**. By the end of this week, you will understand how Tony Hoare's 1978 CSP theory shapes Go's channels, the profound difference between buffered and unbuffered channels (it's not just capacity, it's synchronization), how the `select` statement solves non-deterministic multiplexing, and how Rust enforces strict ownership transfer across channels.

## The Baseline: C# Channels and TPL Dataflow
Historically, C# developers used `BlockingCollection<T>`. Recently, `System.Threading.Channels` brought a CSP-like pattern to .NET.
- **Channel<T>.Reader / Writer**: Provides asynchronous producers and consumers.
- **Backpressure**: Bounded channels naturally apply backpressure when the buffer is full, pausing the producer.
While C# Channels are powerful, they are bolted onto the language via libraries. The syntax is verbose (`await reader.ReadAsync()`), and there is no built-in language construct for multiplexing (waiting on multiple channels simultaneously) other than complex `Task.WhenAny` gymnastics.

## Go: CSP as the Language's Heart
Go was explicitly designed around CSP. Goroutines are the independent processes, and channels are the typed conduits between them.

### Buffered vs Unbuffered Channels
This is a massive stumbling block for C# developers.
- **Unbuffered (`make(chan int)`)**: Synchronous. The sender blocks *until the receiver is ready to take the value*. It acts as a rendezvous point.
- **Buffered (`make(chan int, 5)`)**: Asynchronous (up to a limit). The sender only blocks if the buffer is full.

### Directional Channels
Go allows you to restrict channel usage in function signatures:
- `chan<- int`: Send-only channel.
- `<-chan int`: Receive-only channel.
This guarantees at compile time that a worker function cannot accidentally read from the channel it's supposed to write to.

### The `select` Statement
The `select` statement is Go's superpower. It allows a goroutine to wait on multiple channel operations.
```go
select {
case msg1 := <-ch1:
    fmt.Println("Received", msg1)
case ch2 <- msg2:
    fmt.Println("Sent", msg2)
case <-time.After(time.Second):
    fmt.Println("Timeout")
default:
    fmt.Println("Non-blocking fallback")
}
```
If multiple channels are ready, `select` picks one pseudo-randomly (non-deterministically). This prevents starvation.

## Rust: Message Passing with Ownership
Rust channels (`std::sync::mpsc`) are MPSC: Multiple Producer, Single Consumer. 

### Channel Ownership Semantics
```rust
let (tx, rx) = std::sync::mpsc::channel();
```
- **`Sender<T>` (`tx`)**: Implements `Clone`. You can clone the sender and give it to multiple threads.
- **`Receiver<T>` (`rx`)**: Does *not* implement `Clone`. Only one thread can own the receiver. (If you need MPMC, you use the `crossbeam-channel` or `async-channel` crates).

Because Rust moves values by default, sending an object through a channel *transfers ownership* to the receiving thread. The compiler physically prevents the sender from modifying or accessing the object after it's sent. This is message passing at its most secure.

### Async Channels in Tokio
In high-throughput networked applications, you will use Tokio's channels:
- `mpsc`: Multiple producer, single consumer (bounded/unbuffered).
- `oneshot`: For sending exactly one message (often used for RPC replies).
- `broadcast`: Multi-producer, multi-consumer publish-subscribe.
- `watch`: Single producer, multi-consumer (only keeps the latest value).

## Common Misconceptions to Unlearn
1.  **"Channels are just thread-safe queues."** False. Unbuffered channels in Go are synchronization barriers. Sending a message proves the receiver is ready.
2.  **"I should use channels for everything in Go/Rust."** False. Shared memory with a `Mutex` is often much faster and simpler for basic state updates (like a global counter). Channels are for passing *control flow* or *ownership*.

## Summary Table

| Feature | C# (.NET) | Go | Rust |
| :--- | :--- | :--- | :--- |
| **Primary Primitive** | `System.Threading.Channels` | `chan T` | `std::sync::mpsc`, `crossbeam`, `tokio::sync` |
| **Language Integration**| Library-based | Core syntax (`<-`) | Library-based (macros for `select!`) |
| **Multiplexing** | `Task.WhenAny` | `select` statement | `crossbeam::select!` or `tokio::select!` |
| **Ownership Transfer**| By reference (GC) | By value (copy/pointer) | Strict ownership move |
| **Cancellation** | `CancellationToken` | `context.Context` / `done` chan | Dropping channels / `tokio::select!` cancellation |
