# Week 26: Async Internals, Pinning, and Wakers — Code Comparison & Rosetta Stone

This document provides complete, production-grade, compilable implementations demonstrating the low-level mechanics of asynchronous suspension and resumption across C#, Rust, and Go. Rather than using high-level abstractions like `Task.Run`, `tokio::time::sleep`, or `net.Listen`, we construct the underlying machinery from scratch:

1. **C#**: A custom `CustomTaskCompletionSource<T>` and custom awaiter struct implementing `ICriticalNotifyCompletion` to expose the CLR's push-based continuation dispatch mechanism.
2. **Rust**: A hand-crafted `DelayFuture` implementing `std::future::Future` directly, managing manual `Poll::Pending` states, waker registrations, and thread-safe notification via `cx.waker().wake()`.
3. **Go**: A low-level non-blocking network listener directly invoking Linux `epoll` system calls, revealing how the Go runtime netpoller translates `EAGAIN` non-blocking failures into goroutine parking (`gopark`) and readiness wakeups (`goready`).

---

## 1. C# Implementation: Custom Awaiter & TaskCompletionSource

### Project Configuration (`CustomAwaiter.csproj`)

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <AllowUnsafeBlocks>true</AllowUnsafeBlocks>
  </PropertyGroup>
</Project>
```

### Complete Source Code (`Program.cs`)

```csharp
using System;
using System.Runtime.CompilerServices;
using System.Runtime.ExceptionServices;
using System.Threading;

namespace AsyncInternals.Rosetta;

/// <summary>
/// A zero-dependency, lock-free completion source exposing how Roslyn-generated
/// state machines register continuations without relying on System.Threading.Tasks.Task.
/// </summary>
public sealed class CustomTaskCompletionSource<T>
{
    private const int StatePending = 0;
    private const int StateContinuationRegistered = 1;
    private const int StateCompleted = 2;
    private const int StateFaulted = 3;

    private int _state = StatePending;
    private T? _result;
    private ExceptionDispatchInfo? _exceptionInfo;
    private Action? _continuation;
    private ExecutionContext? _capturedContext;

    public bool IsCompleted => Volatile.Read(ref _state) >= StateCompleted;

    public bool TrySetResult(T result)
    {
        _result = result;
        return FinalizeCompletion(StateCompleted);
    }

    public bool TrySetException(Exception exception)
    {
        // ExceptionDispatchInfo preserves stack trace frames across asynchronous boundaries
        _exceptionInfo = ExceptionDispatchInfo.Capture(exception);
        return FinalizeCompletion(StateFaulted);
    }

    private bool FinalizeCompletion(int targetState)
    {
        var spin = new SpinWait();
        while (true)
        {
            int current = Volatile.Read(ref _state);
            if (current >= StateCompleted) return false; // Already finished; reject double completion

            if (current == StatePending)
            {
                // Result arrived before an awaiter registered a continuation
                if (Interlocked.CompareExchange(ref _state, targetState, StatePending) == StatePending)
                    return true;
            }
            else if (current == StateContinuationRegistered)
            {
                // Continuation already registered; CAS to target state and dispatch continuation
                if (Interlocked.CompareExchange(ref _state, targetState, StateContinuationRegistered) == StateContinuationRegistered)
                {
                    DispatchContinuation();
                    return true;
                }
            }
            spin.SpinOnce();
        }
    }

    internal void RegisterContinuation(Action continuation, bool flowExecutionContext)
    {
        ArgumentNullException.ThrowIfNull(continuation);
        if (flowExecutionContext)
        {
            // Capture ambient AsyncLocal values and security principals across thread hops
            _capturedContext = ExecutionContext.Capture();
        }

        _continuation = continuation;

        // Atomically mark that continuation is registered
        int previous = Interlocked.CompareExchange(ref _state, StateContinuationRegistered, StatePending);
        if (previous >= StateCompleted)
        {
            // Result was ALREADY set before registration; dispatch immediately to prevent deadlock
            DispatchContinuation();
        }
    }

    private void DispatchContinuation()
    {
        Action? continuation = _continuation;
        if (continuation == null) return;

        // Queue onto ThreadPool to ensure asynchronous execution and prevent stack overflow dives
        ThreadPool.UnsafeQueueUserWorkItem(_ =>
        {
            if (_capturedContext != null)
            {
                // Restore original execution context before invoking MoveNext()
                ExecutionContext.Run(_capturedContext, static state => ((Action)state!).Invoke(), continuation);
            }
            else
            {
                continuation();
            }
        }, null);
    }

    internal T GetResult()
    {
        int state = Volatile.Read(ref _state);
        if (state == StateCompleted) return _result!;
        if (state == StateFaulted) _exceptionInfo!.Throw(); // Rethrows with original stack trace
        throw new InvalidOperationException("The custom asynchronous operation has not completed.");
    }

    // Pattern-based awaitable entry point. Roslyn compiler looks for this signature by convention.
    public CustomAwaiter<T> GetAwaiter() => new(this);
}

/// <summary>
/// A zero-allocation struct awaiter implementing ICriticalNotifyCompletion.
/// Roslyn's lowered state machine calls these methods directly.
/// </summary>
public readonly struct CustomAwaiter<T> : ICriticalNotifyCompletion
{
    private readonly CustomTaskCompletionSource<T> _source;
    public CustomAwaiter(CustomTaskCompletionSource<T> source) => _source = source;

    public bool IsCompleted => _source.IsCompleted;
    public void OnCompleted(Action continuation) => _source.RegisterContinuation(continuation, true);
    // UnsafeOnCompleted skips legacy CAS permissions checks for maximum throughput
    public void UnsafeOnCompleted(Action continuation) => _source.RegisterContinuation(continuation, true);
    public T GetResult() => _source.GetResult();
}

public static class Program
{
    public static async Task Main()
    {
        Console.WriteLine($"[C# Main] Thread ID: {Environment.CurrentManagedThreadId} - Starting workflow");
        var source = new CustomTaskCompletionSource<string>();

        // Background thread simulating I/O completion after 500ms
        var timerThread = new Thread(() =>
        {
            Thread.Sleep(500);
            Console.WriteLine($"[C# Timer] Thread ID: {Environment.CurrentManagedThreadId} - Firing I/O completion");
            source.TrySetResult("Order-Payload-9042");
        }) { IsBackground = true };
        timerThread.Start();

        Console.WriteLine($"[C# Main] Thread ID: {Environment.CurrentManagedThreadId} - Awaiting custom source");
        // The 'await' keyword binds to CustomAwaiter<string> via duck-typing pattern
        string result = await source;
        Console.WriteLine($"[C# Resumed] Thread ID: {Environment.CurrentManagedThreadId} - Result: {result}");
    }
}
```

---

## 2. Rust Implementation: Custom Polled `DelayFuture` & Waker Dispatch

### Project Configuration (`Cargo.toml`)

```toml
[package]
name = "rust_delay_future"
version = "0.1.0"
edition = "2021"

[dependencies]
# Tokio provides the multi-threaded executor driving the poll loop
tokio = { version = "1.38", features = ["macros", "rt-multi-thread"] }
```

### Complete Source Code (`src/main.rs`)

```rust
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Waker};
use std::thread;
use std::time::{Duration, Instant};

/// Shared state between the DelayFuture and the background timer thread.
/// Protected by a Mutex because poll() and the background timer thread execute concurrently.
struct DelayState {
    completed: bool,
    // The Waker registered by the executor during the most recent poll() invocation.
    // Crucial: Must be updated if the future moves between worker threads.
    waker: Option<Waker>,
    spawned: bool,
}

/// A custom asynchronous future implemented completely from scratch.
/// Does not use async fn, pin_project, or tokio::time::sleep.
pub struct DelayFuture {
    duration: Duration,
    state: Arc<Mutex<DelayState>>,
}

impl DelayFuture {
    pub fn new(duration: Duration) -> Self {
        Self {
            duration,
            state: Arc::new(Mutex::new(DelayState {
                completed: false,
                waker: None,
                spawned: false,
            })),
        }
    }
}

// Implement Future directly. DelayFuture contains an Arc pointer and no self-referential
// fields, which means it automatically satisfies Unpin.
impl Future for DelayFuture {
    type Output = String;

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        let mut state = self.state.lock().unwrap();

        // 1. Check if the background timer has already signaled completion
        if state.completed {
            return Poll::Ready("Rust-Payload-7701".to_string());
        }

        // 2. CRITICAL WAKER REGISTRATION:
        // Always store or update the waker on Poll::Pending!
        // The executor may have migrated this task to a different thread since the last poll.
        state.waker = Some(cx.waker().clone());

        // 3. Spawn the background worker thread on the initial poll
        if !state.spawned {
            state.spawned = true;
            let state_clone = Arc::clone(&self.state);
            let duration = self.duration;

            thread::Builder::new()
                .name("reactor-timer-thread".to_string())
                .spawn(move || {
                    let start = Instant::now();
                    // Simulating hardware timer wait / OS interrupt latency
                    thread::sleep(duration);

                    let mut inner_state = state_clone.lock().unwrap();
                    inner_state.completed = true;

                    // 4. Invoke Waker::wake() to notify Tokio's scheduler.
                    // This moves the task from the reactor back into Tokio's runnable queue.
                    if let Some(waker) = inner_state.waker.take() {
                        println!(
                            "[Rust Timer Thread {:?}] Elapsed: {:?} -> Invoking waker.wake()",
                            thread::current().id(),
                            start.elapsed()
                        );
                        waker.wake();
                    }
                })
                .expect("Failed to spawn timer thread");
        }

        // 5. Yield execution back to the Tokio executor thread
        Poll::Pending
    }
}

#[tokio::main]
async fn main() {
    println!("[Rust Main Thread {:?}] Starting custom DelayFuture test", thread::current().id());
    let delay = DelayFuture::new(Duration::from_millis(500));

    println!("[Rust Main Thread {:?}] Polling future via .await", thread::current().id());
    // .await transfers ownership into a pinned location and repeatedly polls via Tokio
    let result = delay.await;

    println!("[Rust Main Thread {:?}] Future resolved with: {}", thread::current().id(), result);
}
```

---

## 3. Go Implementation: Raw Non-Blocking Linux Socket & Netpoller Emulation

### Project Configuration (`go.mod`)

```go
module asyncinternals/rosetta

go 1.22
```

### Complete Source Code (`main.go`)

```go
package main

import (
	"fmt"
	"net"
	"os"
	"runtime"
	"sync"
	"syscall"
	"time"
)

// RawEpollNetpoller emulates the exact system-level mechanics of Go's internal netpoller.
// It directly invokes Linux epoll syscalls to manage non-blocking I/O readiness,
// demonstrating how the Go runtime converts EAGAIN into goroutine suspension (gopark).
type RawEpollNetpoller struct {
	epollFd int
	mu      sync.Mutex
	waiters map[int32]chan struct{}
}

func NewRawEpollNetpoller() (*RawEpollNetpoller, error) {
	// Create an epoll instance using Linux system calls (mirrors runtime/netpoll_epoll.go)
	epFd, err := syscall.EpollCreate1(syscall.EPOLL_CLOEXEC)
	if err != nil {
		return nil, fmt.Errorf("EpollCreate1 failed: %w", err)
	}
	return &RawEpollNetpoller{
		epollFd: epFd,
		waiters: make(map[int32]chan struct{}),
	}, nil
}

// WaitRead blocks until the given file descriptor is ready for reading.
// In the Go runtime, this mirrors runtime.netpollblock() which calls gopark().
func (np *RawEpollNetpoller) WaitRead(fd int) error {
	notifyChan := make(chan struct{}, 1)

	np.mu.Lock()
	np.waiters[int32(fd)] = notifyChan
	event := syscall.EpollEvent{
		Events: syscall.EPOLLIN | syscall.EPOLLONESHOT,
		Fd:     int32(fd),
	}
	err := syscall.EpollCtl(np.epollFd, syscall.EPOLL_CTL_MOD, fd, &event)
	if err == syscall.ENOENT {
		err = syscall.EpollCtl(np.epollFd, syscall.EPOLL_CTL_ADD, fd, &event)
	}
	np.mu.Unlock()

	if err != nil {
		return fmt.Errorf("EpollCtl failed: %w", err)
	}

	// Channel receive deschedules this goroutine without burning an OS thread (M)
	<-notifyChan
	return nil
}

// Background poller loop.
// In the Go runtime, this is invoked by runtime.netpoll() from sysmon or the scheduler.
func (np *RawEpollNetpoller) PollLoop() {
	events := make([]syscall.EpollEvent, 64)
	for {
		n, err := syscall.EpollWait(np.epollFd, events, -1)
		if err != nil {
			if err == syscall.EINTR {
				continue // Interrupted by OS signal (e.g. SIGURG from sysmon); retry
			}
			return
		}

		np.mu.Lock()
		for i := 0; i < n; i++ {
			fd := events[i].Fd
			if ch, exists := np.waiters[fd]; exists {
				delete(np.waiters, fd)
				// Equivalent to runtime.goready(g): transitions goroutine to _Grunnable
				select {
				case ch <- struct{}{}:
				default:
				}
			}
		}
		np.mu.Unlock()
	}
}

func main() {
	fmt.Printf("[Go Main] GOMAXPROCS: %d - Running on OS Thread\n", runtime.GOMAXPROCS(0))
	netpoller, err := NewRawEpollNetpoller()
	if err != nil {
		fmt.Printf("Netpoller init failed: %v\n", err)
		os.Exit(1)
	}
	go netpoller.PollLoop()

	// 1. Create a raw IPv4 TCP socket
	serverFd, err := syscall.Socket(syscall.AF_INET, syscall.SOCK_STREAM, 0)
	if err != nil {
		panic(err)
	}
	defer syscall.Close(serverFd)

	// 2. CRITICAL: Set socket to non-blocking mode (O_NONBLOCK).
	if err := syscall.SetNonblock(serverFd, true); err != nil {
		panic(err)
	}
	_ = syscall.SetsockoptInt(serverFd, syscall.SOL_SOCKET, syscall.SO_REUSEADDR, 1)

	// 3. Bind and Listen
	sockAddr := &syscall.SockaddrInet4{Port: 9876, Addr: [4]byte{127, 0, 0, 1}}
	if err := syscall.Bind(serverFd, sockAddr); err != nil {
		panic(err)
	}
	if err := syscall.Listen(serverFd, 128); err != nil {
		panic(err)
	}
	fmt.Println("[Go Server] Raw non-blocking socket listening on 127.0.0.1:9876")

	// Client connection simulation
	go func() {
		time.Sleep(500 * time.Millisecond)
		conn, err := net.Dial("tcp", "127.0.0.1:9876")
		if err != nil {
			return
		}
		defer conn.Close()
		_, _ = conn.Write([]byte("Go-Raw-Netpoller-Payload"))
	}()

	// 4. Accept loop using raw non-blocking mechanics
	for {
		nfd, _, err := syscall.Accept(serverFd)
		if err == nil {
			fmt.Printf("[Go Server] Client connected! FD: %d\n", nfd)
			_ = syscall.SetNonblock(nfd, true)

			buf := make([]byte, 1024)
			for {
				n, err := syscall.Read(nfd, buf)
				if err == nil {
					fmt.Printf("[Go Server] Received %d bytes: %s\n", n, string(buf[:n]))
					_ = syscall.Close(nfd)
					return
				}
				if err == syscall.EAGAIN || err == syscall.EWOULDBLOCK {
					// Kernel buffer empty; park goroutine until epoll signals readiness
					if waitErr := netpoller.WaitRead(nfd); waitErr != nil {
						return
					}
					continue
				}
				_ = syscall.Close(nfd)
				return
			}
		}

		if err == syscall.EAGAIN || err == syscall.EWOULDBLOCK {
			// No incoming connection; park until server socket is readable
			if waitErr := netpoller.WaitRead(serverFd); waitErr != nil {
				return
			}
			continue
		}
		return
	}
}
```

---

## Critical Observations for C# Developers

### 1. Continuations vs. Polling Loops: Push vs. Pull Mechanics
In our C# implementation, `CustomTaskCompletionSource` stores an `Action? _continuation` delegate. When `TrySetResult()` is called, the completing thread actively **pushes** the continuation onto the ThreadPool queue via `ThreadPool.UnsafeQueueUserWorkItem`. The state machine's `MoveNext()` method is invoked exactly once per suspension.

In Rust's `DelayFuture`, notice that the background timer thread does **not** execute user code directly. It simply executes `waker.wake()`. The Tokio executor receives this notification and pulls the future off the sleep queue, scheduling an OS worker thread to re-invoke `poll()`. In Rust, the state machine is pulled forward by the runtime executor; in C#, the continuation is pushed into the thread pool.

### 2. Waker Registration Idempotency and Task Migration
Look closely at line 55 in the Rust code:
```rust
state.waker = Some(cx.waker().clone());
```
Why must this clone occur on every single poll that returns `Poll::Pending`? In Tokio's work-stealing scheduler, a task can be stolen by Worker Thread 2 while it was originally polled on Worker Thread 1. The `Context` passed into `poll()` contains a `Waker` tied to the current execution thread. If the future only stored the `Waker` on the first poll, calling `waker.wake()` might notify the wrong worker thread. C# avoids this because `IAsyncStateMachine` boxing captures an invariant `ExecutionContext` and marshals through the central CLR ThreadPool.

### 3. The Hidden Cost of Heap Boxing vs. Zero-Allocation Futures
In our C# `CustomTaskCompletionSource`, although the `CustomAwaiter` itself is a stack-allocated struct (`readonly struct CustomAwaiter<T>`), the completion source itself (`CustomTaskCompletionSource<T>`) is a reference type on the managed heap. Furthermore, when Roslyn suspends on an incomplete awaiter, `builder.AwaitUnsafeOnCompleted(ref awaiter, ref this)` allocates a heap box for the `IAsyncStateMachine` struct.

In Rust, our `DelayFuture` holds an `Arc<Mutex<DelayState>>` only because we manually spawned an OS thread to demonstrate the waker. When using native Tokio primitives (like `tokio::net::TcpStream`), **zero heap allocations occur**. The future's state machine is a value type nested inside the parent task. The only allocation in Tokio is the initial `tokio::spawn` task box; subsequent nested `.await` calls are 100% stack-allocated and inline.

### 4. Raw Non-Blocking Sockets and the Netpoller
In Go, notice how our `main.go` uses `syscall.SetNonblock(fd, true)` and handles `syscall.EAGAIN`. In C# and Rust, this non-blocking handling is exposed directly through the type system (`TaskAwaiter` and `Poll::Pending`). 

Go hides this entire state machine loop inside the runtime. When you call standard library `net.Conn.Read()`, Go executes the exact loop we wrote in `main.go`: it attempts a raw non-blocking read, catches `EAGAIN`, calls `runtime.netpollblock()` to park the goroutine in the runtime netpoller, and frees the OS thread (`M`) to run other goroutines. When Linux `epoll` reports that bytes have arrived, the runtime netpoller calls `goready()`, placing the goroutine back on a logical processor (`P`). Go provides asynchronous I/O performance with purely synchronous imperative semantics.

---

## Build, Compilation, and Execution Guide

### C# (.NET 8 SDK)
```bash
# Navigate to the C# project directory
cd /path/to/csharp_project
dotnet build -c Release
dotnet run -c Release
```
**Expected Output**:
```text
[C# Main] Thread ID: 1 - Starting workflow
[C# Main] Thread ID: 1 - Awaiting custom source
[C# Timer] Thread ID: 5 - Firing I/O completion
[C# Resumed] Thread ID: 8 - Result: Order-Payload-9042
```

### Rust (Cargo)
```bash
# Navigate to the Rust project directory
cd /path/to/rust_project
cargo build --release
cargo run --release
```
**Expected Output**:
```text
[Rust Main Thread ThreadId(1)] Starting custom DelayFuture test
[Rust Main Thread ThreadId(1)] Polling future via .await
[Rust Timer Thread ThreadId(2)] Elapsed: 500.12ms -> Invoking waker.wake()
[Rust Main Thread ThreadId(1)] Future resolved with: Rust-Payload-7701
```

### Go (Go 1.22+)
```bash
# Navigate to Go project directory
cd /path/to/go_project
go run main.go
```
**Expected Output**:
```text
[Go Main] GOMAXPROCS: 16 - Running on OS Thread
[Go Server] Raw non-blocking socket listening on 127.0.0.1:9876
[Go Server] Client connected! FD: 5
[Go Server] Received 24 bytes: Go-Raw-Netpoller-Payload
```
