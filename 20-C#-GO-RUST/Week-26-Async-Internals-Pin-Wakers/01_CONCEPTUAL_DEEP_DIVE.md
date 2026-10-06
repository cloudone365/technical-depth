# Week 26: Async Internals, Pinning, and Wakers — Conceptual Deep Dive

## Why This Week Matters for Your Career Transition

Senior C# engineers live in an asynchronous environment sculpted by over a decade of runtime refinement. When you write `async Task<OrderResult> ProcessOrderAsync(OrderId id)`, you intuitively understand that the Roslyn compiler transforms your method into a state machine struct, that `await` unpacks completed tasks synchronously or registers continuations asynchronously, and that `SynchronizationContext` or the CLR ThreadPool schedules resumption. However, the CLR abstracts away the brutal mechanical realities of memory stability and hardware thread multiplexing. The .NET garbage collector transparently manages the lifetime of state machines boxed to the heap, relocating them during generation compactions while updating internal object references without your awareness or intervention. 

When you transition to Go and Rust, this comfortable abstraction vanishes, replaced by two radically divergent systems engineering philosophies. Go rejects `async/await` syntax entirely, providing the illusion of synchronous execution over an M:N green-thread scheduler (the G-M-P model) backed by dynamically resizable contiguous stacks, cooperative runtime hooks, and Unix signal-based asynchronous preemption (`SIGURG`). Rust, pursuing zero-cost abstractions and bare-metal control, rejects both managed garbage collection and runtime stack copying. Rust compiles `async fn` blocks into zero-allocation stack-allocated state machine structs. However, because local variables referenced across `.await` points yield self-referential data structures, moving such a struct in memory causes fatal use-after-free corruption. To guarantee memory safety at compile time without a GC, Rust was forced to invent `Pin<P>`, `Unpin`, and the `Waker` notification contract. Understanding these underlying runtime mechanics is the defining threshold separating junior syntax translators from senior systems architects who can debug reactor stalls, eliminate allocation bottlenecks, and design safe concurrent engines.

---

## Why Async is a State Machine: The Fundamental Problem of Stack Frames Across Suspension Points

To understand why compilers lower asynchronous functions into state machines, one must examine the physical reality of the CPU execution stack. In standard synchronous execution, every function call pushes an activation record (a stack frame) onto the thread's call stack. This frame stores:
1. The return address pointing to the caller's next instruction.
2. The function's incoming arguments.
3. All local variables and intermediate evaluation temporaries.
4. Saved registers that must be restored when the function returns.

When a function executes an I/O operation—such as waiting for a database response or an incoming TCP packet—a purely synchronous thread must block inside the kernel. The operating system places the OS thread into a wait state, descheduling it from the physical CPU core. While simple, this model collapses under modern scale: an OS thread in Linux or Windows incurs significant memory overhead (typically 1MB to 8MB reserved virtual memory for its stack) and significant kernel scheduling latency during thread context switches (saving CPU register state, flushing translation lookaside buffers (TLBs), and navigating scheduler run queues).

```
Synchronous Blocking (Thread Stack Bound):
Core 0: [ OS Thread Stack: FrameMain -> FrameService -> FrameSocketRead (BLOCKED) ]
Result: Core 0 must context-switch to another OS thread; 1MB stack memory pinned in kernel.

Asynchronous Suspension (Stack Frame Evacuation):
Core 0: [ FrameMain -> ProcessAsync() initiates read -> Returns incomplete handle ]
State:  Local variables & execution index evacuated to State Machine (Heap or Parent Future).
Result: OS Thread immediately returns to ThreadPool/Reactor to run other tasks.
```

To achieve high concurrency, the runtime cannot allow an OS thread to sit idle with an allocated call stack. When an execution path reaches a suspension point, the physical CPU stack frame must be dismantled so the thread can perform other work. However, the suspended routine's execution state—its instruction pointer, local variables, loop counters, and temporary values—must survive across the suspension interval. 

Compilers solve this by transforming the imperative control flow into a **state machine**. The activation record is moved from the volatile thread call stack into a persistent data structure. The compiler generates an explicit state index variable (an integer representing the current suspension point) and rewrites every local variable into a field of the state machine struct. When the asynchronous operation yields, the state machine saves the next resumption index and yields control back to the caller or scheduler. When the external event completes, the scheduler re-invokes the state machine's execution method (such as `MoveNext()` in C# or `poll()` in Rust), which switches on the state index and jumps directly to the saved instruction point, restoring access to the preserved local fields.

---

## C# Task Architecture: The CLR's Async Machine Under the Hood

### Lowered `IAsyncStateMachine` and `MoveNext()`

When Roslyn compiles an `async Task<T>` method, it generates an internal struct implementing `System.Runtime.CompilerServices.IAsyncStateMachine`. Consider this C# method:

```csharp
public async Task<int> CalculateTotalAsync(string accountId)
{
    var account = await FetchAccountAsync(accountId);
    var balance = account.Balance;
    var risk = await EvaluateRiskAsync(balance);
    return balance - risk;
}
```

The Roslyn compiler synthesizes a state machine struct containing:
- An integer field `<>1__state` initialized to `-1`.
- A builder field `<>t__builder` of type `AsyncTaskMethodBuilder<int>`.
- The parameter `string accountId`.
- Hoisted local variables: `Account account`, `int balance`, and `int risk`.
- Awaiter fields to hold the intermediate awaiter structs (e.g., `TaskAwaiter<Account>`).

The core logic is compiled into the `MoveNext()` method, structured around a state dispatch switch:

```csharp
public void MoveNext()
{
    int num = this.<>1__state;
    int result;
    try
    {
        TaskAwaiter<Account> awaiter1;
        TaskAwaiter<int> awaiter2;
        if (num != 0)
        {
            if (num == 1)
            {
                awaiter2 = this.<>u__2;
                this.<>u__2 = default;
                this.<>1__state = -1;
                goto PostAwait2;
            }
            awaiter1 = FetchAccountAsync(this.accountId).GetAwaiter();
            if (!awaiter1.IsCompleted)
            {
                this.<>1__state = 0;
                this.<>u__1 = awaiter1;
                this.<>t__builder.AwaitUnsafeOnCompleted(ref awaiter1, ref this);
                return;
            }
        }
        else
        {
            awaiter1 = this.<>u__1;
            this.<>u__1 = default;
            this.<>1__state = -1;
        }

        this.account = awaiter1.GetResult();
        this.balance = this.account.Balance;

        awaiter2 = EvaluateRiskAsync(this.balance).GetAwaiter();
        if (!awaiter2.IsCompleted)
        {
            this.<>1__state = 1;
            this.<>u__2 = awaiter2;
            this.<>t__builder.AwaitUnsafeOnCompleted(ref awaiter2, ref this);
            return;
        }

    PostAwait2:
        this.risk = awaiter2.GetResult();
        result = this.balance - this.risk;
    }
    catch (Exception exception)
    {
        this.<>1__state = -2;
        this.<>t__builder.SetException(exception);
        return;
    }
    this.<>1__state = -2;
    this.<>t__builder.SetResult(result);
}
```

```
C# Execution Flow:
[ Caller invokes CalculateTotalAsync ]
       │
       ▼
[ State machine struct instantiated on Thread Stack ]
       │
       ├─► awaiter.IsCompleted == true? ──► YES ──► Continue on Stack (Zero Alloc)
       │
       ▼ NO
[ MoveNext() sets <>1__state = 0 ]
       │
       ▼
[ builder.AwaitUnsafeOnCompleted() ]
       │
       ▼
[ CLR Boxes State Machine to Managed Heap ] ──► Returns Task<T> to caller
       │
       ▼
[ Network I/O completes via IOCP ] ──────────► ThreadPool invokes MoveNext() on Heap Box
```

### The Allocation Boundary: `AwaitUnsafeOnCompleted()` and Boxing

Notice that the synthesized state machine begins its life as a **value type (struct) on the thread's stack**. If every awaited task in the sequence completes synchronously (for instance, data is already cached in memory), `awaiter.IsCompleted` returns `true`. The method executes completely synchronously on the caller's stack frame without allocating a single byte on the managed heap.

However, the instant `awaiter.IsCompleted` evaluates to `false`, the method must suspend. The stack frame is about to unwind as the thread returns to its caller. To prevent the state machine's fields from being destroyed with the stack frame, `AsyncTaskMethodBuilder<T>.AwaitUnsafeOnCompleted(ref awaiter, ref this)` is called. The CLR allocates an object on the managed heap and **boxes** the value-type state machine into this heap instance. The continuation delegate passed to the awaiter retains a strong reference to this boxed heap object. When the asynchronous operation finishes, the ThreadPool invokes `MoveNext()` directly on the heap-allocated instance.

### `ExecutionContext` and `AsyncLocal<T>`

In .NET, execution context represents the ambient environment of an execution flow, encapsulating security principals (`IPrincipal`), synchronization scopes, and ambient user data managed by `AsyncLocal<T>`.

When an asynchronous suspension occurs, the runtime must decide whether to propagate this context across thread transitions:
- `AwaitOnCompleted()` captures the ambient `ExecutionContext` via `ExecutionContext.Capture()` and ensures that when the continuation fires on an arbitrary ThreadPool worker thread, `ExecutionContext.Run()` restores the identical ambient state. This guarantees that `AsyncLocal<T>` values (such as distributed tracing correlation IDs or tenant contexts) seamlessly follow the logical asynchronous call path.
- `AwaitUnsafeOnCompleted()` skips code access security (CAS) checks (historical .NET Framework baggage), but in modern .NET Core / .NET 8+, it still captures and flows the `ExecutionContext`. The "Unsafe" naming denotes that it does not flow legacy `SecurityContext` permissions, offering an optimization while maintaining `AsyncLocal<T>` integrity.

### `ValueTask<T>` and the `IValueTaskSource` Internal Tagged Union

Because `Task<T>` is a managed reference type (`class`), returning an incomplete asynchronous operation necessitates allocating a `Task<T>` object on the heap, even if the result is computed almost instantaneously. To eliminate heap pressure in high-throughput pipelines (such as Kestrel processing millions of HTTP/2 requests), .NET introduced `ValueTask<T>`.

`ValueTask<T>` is defined internally as a discriminatory tagged union (a struct):

```csharp
public readonly struct ValueTask<TResult> : IEquatable<ValueTask<TResult>>
{
    internal readonly object? _obj;        // Either null, a Task<TResult>, or an IValueTaskSource<TResult>
    internal readonly TResult _result;     // Inline value for synchronous completion
    internal readonly short _token;        // Version token to prevent ABA reuse bugs
    internal readonly bool _continueOnCapturedContext;
}
```

When an operation completes synchronously, `_obj` remains `null`, `_result` holds the computed value directly on the stack, and no heap allocation occurs. When the operation must suspend, `_obj` stores an instance of `IValueTaskSource<TResult>`. The runtime can use a pooled, reusable implementation of `IValueTaskSource` (such as `ManualResetValueTaskSourceCore<T>`). Once the operation completes and `GetResult(_token)` is called, the underlying object resets its internal state and returns to an object pool. The `_token` field acts as an architectural guardrail: if consumer code attempts to `await` the same `ValueTask` twice or inspect it after reuse, the token mismatches and throws an `InvalidOperationException`, preventing memory corruption in pooled objects.

---

## Rust Futures: Self-Referential Structs and the Memory Relocation Problem

### Stackless Futures Without a Garbage Collector

Rust's asynchronous model adheres strictly to the principle of zero-cost abstractions: you do not pay for runtime machinery or heap allocations you do not need. Unlike C#, which defaults to boxing state machines onto the managed heap upon suspension, Rust compiles an `async fn` or `async` block into an anonymous value-type struct that implements the `std::future::Future` trait.

A Rust future is **completely stack-allocated** by default. If you nest five async function calls within each other:
```rust
async fn leaf() -> i32 { 42 }
async fn mid() -> i32 { leaf().await + 1 }
async fn root() -> i32 { mid().await * 2 }
```
The compiler composes these into a single nested composite struct: `RootFuture(MidFuture(LeafFuture))`. The size of this composite struct is calculated at compile time. It can live entirely on the stack of the thread driving it, or be moved to the heap via an explicit `Box::pin(root())` if thread-boundary transfer (e.g., `tokio::spawn`) is required.

### The Self-Referential Struct Catastrophe

Because Rust allows references (borrows) to exist across suspension boundaries, the stackless future design introduces an existential threat to memory safety: **self-referential structs**.

Consider an asynchronous block that borrows data across an `.await` suspension point:

```rust
async fn process_socket_data(stream: &mut TcpStream) {
    let mut buffer: [u8; 1024] = [0u8; 1024];
    let slice: &[u8] = &buffer[0..512]; // Internal reference to local stack memory!
    
    // Suspension point: local variables must be preserved in the Future struct
    stream.read_exact(slice).await;
    println!("First byte: {}", slice[0]);
}
```

When the compiler transforms `process_socket_data` into a state machine struct, both `buffer` and `slice` become fields within that same struct:

```rust
// Conceptual compiler-generated state machine struct
struct ProcessSocketDataFuture {
    state: usize,
    buffer: [u8; 1024],
    slice: *const [u8], // Points directly to self.buffer!
}
```

Now, trace what occurs in physical memory if this struct is relocated:

```
Step 1: Future initialized on Stack at Memory Address 0x1000
┌─────────────────────────────────────────────────────────────┐
│ ProcessSocketDataFuture at 0x1000                           │
│ ┌──────────────────────────────────┐                        │
│ │ buffer: [u8; 1024] at 0x1008     │ ◄──────────┐           │
│ └──────────────────────────────────┘            │           │
│ ┌──────────────────────────────────┐            │           │
│ │ slice (pointer): 0x1008          │ ───────────┘           │
│ └──────────────────────────────────┘ (points to self.buffer)│
└─────────────────────────────────────────────────────────────┘

Step 2: Caller moves the Future to the Heap (e.g., Box::new) at Address 0x5000
┌─────────────────────────────────────────────────────────────┐
│ ProcessSocketDataFuture at 0x5000                           │
│ ┌──────────────────────────────────┐                        │
│ │ buffer: [u8; 1024] at 0x5008     │                        │
│ └──────────────────────────────────┘                        │
│ ┌──────────────────────────────────┐                        │
│ │ slice (pointer): 0x1008          │ ───────────┐           │
│ └──────────────────────────────────┘            │           │
└─────────────────────────────────────────────────┼───────────┘
                                                  ▼
                         CRITICAL MEMORY HAZARD: [ 0x1008 ]
                         Points to abandoned, reused stack memory!
                         Use-After-Free / Memory Corruption!
```

In standard Rust, **every type is movable by default**. Moves are raw memory copies (`memcpy`). If `ProcessSocketDataFuture` is passed by value to another function, pushed into a `Vec`, or boxed onto the heap, its bytes are copied to the new memory destination. The field `buffer` moves from address `0x1008` to `0x5008`. However, the pointer field `slice` still holds the literal address `0x1008`. The moment the executor wakes this future and invokes its poll method, `slice[0]` reads from memory address `0x1008`, which now belongs to another function's stack frame. This is a catastrophic **use-after-free and memory corruption vulnerability**.

### Why C# Does Not Suffer From This Problem

C# engineers often wonder why this issue never surfaces in .NET. The CLR avoids self-referential corruption through two distinct mechanisms:
1. **Roslyn Language Constraints**: C# forbids taking references (`ref` or `in`) to local variables across an `await` boundary. If you attempt to declare `ref int x = ref local; await Task.Yield();`, the Roslyn compiler emits error `CS4007: 'Instance of type 'ref int' cannot be preserved across 'await' expressions'`.
2. **Garbage Collector Memory Virtualization**: Any references stored in an `IAsyncStateMachine` box are managed object references (GC handles/pointers). When the .NET GC compacts the heap and relocates an object, the runtime engine updates all active pointer references across the entire process to point to the object's new physical location. Rust has no GC runtime to trace and update pointers.

---

## The Mechanics of `Pin<P>`: Absolute Address Invariance Guaranteed at Compile Time

Because Rust cannot dynamically fix up pointers at runtime, it must enforce a strict invariant: **once a self-referential future begins executing, its physical location in memory must never change until it is dropped**. This invariant is enforced by `Pin<P>`.

### The Definition of `Pin<P>` and the `Unpin` Marker Trait

`Pin` is not an allocator, nor does it automatically place data on the heap. `Pin<P>` is a pointer-wrapping struct defined in `core::pin`:

```rust
pub struct Pin<P> {
    pointer: P,
}
```

Here, `P` is a pointer type, such as `&mut T`, `Box<T>`, or `NonNull<T>`. Whether a pinned pointer allows access to its underlying value depends on a built-in auto trait: `Unpin`.
- **`Unpin`**: The vast majority of Rust types (`i32`, `String`, `Vec<T>`, normal structs) do not care about being moved in memory. They implement `Unpin` automatically. For any type where `T: Unpin`, `Pin<P>` provides no restrictions. You can freely dereference `Pin<&mut T>` to `&mut T`, swap it with `std::mem::swap`, or move it anywhere.
- **`!Unpin` (Negative Trait Bound)**: Types that contain internal self-references (such as compiler-generated async state machines or custom intrusive data structures) explicitly opt out of `Unpin` by implementing `!Unpin` (via `PhantomPinned`). 

When `T` is `!Unpin`, `Pin<&mut T>` **locks down the type system**:
1. Safe code cannot obtain an unpinned `&mut T` reference.
2. Because you cannot get an `&mut T`, you cannot invoke `std::mem::swap`, `std::mem::replace`, or assignment operators that would move the underlying value out of its memory address.
3. The memory address of `T` is guaranteed to remain invariant until `Drop::drop` has completed execution on that memory.

### Pinning Contracts and the Future Signature

The fundamental definition of the `Future` trait in Rust reflects this requirement:

```rust
pub trait Future {
    type Output;
    // Notice self is NOT &mut Self! It is Pin<&mut Self>
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>;
}
```

An executor cannot poll a future by taking a standard `&mut Future`. It must provide a `Pin<&mut Future>`. This guarantees to the future's compiler-generated state machine that internal self-referential pointers formed during execution will remain permanently valid.

```rust
// Pinning via Heap Allocation (Safe, standard practice for spawned tasks)
let unpinned_future = process_socket_data(&mut stream);
let pinned_future: Pin<Box<dyn Future<Output = ()>>> = Box::pin(unpinned_future);

// Pinning on the Stack via unsafe Pin::new_unchecked
let mut future = process_socket_data(&mut stream);
let mut pinned_future = unsafe {
    // CONTRACT: The programmer guarantees that `future` will never be moved 
    // out of this stack location until it is dropped!
    Pin::new_unchecked(&mut future)
};
```

### Pin Projection and the `pin-project` Macro

When building custom futures or composite types that wrap inner futures, you encounter the problem of **pin projection**: if you have a `Pin<&mut ParentStruct>`, how do you safely obtain a `Pin<&mut ChildField>` to poll an inner future without violating pinning rules? The systems-standard solution is the `pin_project` macro from the `pin-project` crate:

```rust
use pin_project::pin_project;
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};

#[pin_project]
pub struct TimedWrapper<F> {
    #[pin] // Structurally pinned field: projects to Pin<&mut F>
    inner_future: F,
    start_time: std::time::Instant, // Unpinned field: projects to &mut Instant
}

impl<F: Future> Future for TimedWrapper<F> {
    type Output = F::Output;

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        let this = self.project();
        // this.inner_future is Pin<&mut F> -> safe to call .poll()!
        // this.start_time is &mut Instant   -> safe to mutate normally
        match this.inner_future.poll(cx) {
            Poll::Pending => Poll::Pending,
            Poll::Ready(val) => {
                println!("Execution took: {:?}", this.start_time.elapsed());
                Poll::Ready(val)
            }
        }
    }
}
```

---

## The Waker Contract: Event-Driven Polling and Tokio's Reactor Mechanics

### Pull-Based vs. Push-Based Concurrency Models

To fully understand how Rust executes asynchronous code, one must contrast push-based and pull-based paradigms:
- **C# / .NET (Push Model)**: When you await an uncompleted task, you pass a callback continuation (`Action`) to the awaiter via `OnCompleted()`. When the background thread or OS I/O Completion Port (IOCP) signals completion, the runtime directly invokes that continuation or enqueues it to the ThreadPool. The runtime actively pushes execution forward.
- **Rust / Tokio (Pull Model)**: The runtime executor does not register arbitrary continuation delegates. Instead, the executor drives a future by repeatedly **polling** it via `poll(Pin<&mut Self>, &mut Context)`. If the future cannot finish immediately, it returns `Poll::Pending`. The executor then sets the future aside.

```
C# Push Model:
[ Task Suspension ] ──Register Action──► [ IOCP / Timer Engine ]
                                                 │
                                           I/O Completes
                                                 │
                                                 ▼
[ ThreadPool ] ◄──────Enqueue Action─────────────┘

Rust Pull Model:
[ Executor ] ─────────► poll(Pin<&mut Future>, cx) ────► Returns Poll::Pending
     ▲                                                         │
     │                                                Future stores Waker
     │                                                in Reactor (epoll)
     │                                                         │
     │                                                   Socket Ready!
     │                                                         │
     └────────────────── waker.wake() ─────────────────────────┘
```

The pull model introduces a critical question: if a future returns `Poll::Pending`, how does the executor know when to poll it again? If the executor polled constantly in a tight loop, it would pin CPU cores at 100% utilization. The bridge is the **`Waker`**.

### The Anatomy of `std::task::Waker` and `RawWakerVTable`

A `Waker` is an object that handles notification. When a future returns `Poll::Pending`, it captures a clone of the `Waker` from the `Context`:
```rust
let waker = cx.waker().clone();
```
The future transfers this `Waker` to the subsystem responsible for detecting readiness (e.g., `epoll`, a hardware timer thread, or an atomic channel). When the event occurs, the subsystem calls `waker.wake()`. Calling `wake()` signals the runtime executor that the corresponding future is now capable of making progress. The executor places that specific future back onto its runnable queue and schedules an OS worker thread to invoke `poll()` once more.

To achieve zero allocation and C-ABI compatibility, the Rust standard library implements `Waker` via manual vtable dispatch without requiring standard trait objects (`dyn Trait`):

```rust
pub struct RawWaker {
    data: *const (),
    vtable: &'static RawWakerVTable,
}

pub struct RawWakerVTable {
    clone: unsafe fn(*const ()) -> RawWaker,
    wake: unsafe fn(*const ()),
    wake_by_ref: unsafe fn(*const ()),
    drop: unsafe fn(*const ()),
}
```

This vtable layout allows executor authors (such as the Tokio team) to embed the task's reference counter, atomic state flags, and run-queue intrusive linked-list pointers directly inside a single heap allocation (`Task<T>`). When `waker.wake()` is invoked, it executes the custom function pointer in the vtable, executing an atomic compare-and-swap (CAS) to mark the task as scheduled, followed by pushing the task pointer onto a lock-free work-stealing deque.

### Tokio's Reactor Mechanics: From Epoll to Worker Execution

In Tokio, asynchronous I/O is orchestrated through a specialized subsystem known as the **Reactor** (backed by the `mio` crate):
1. **Registration**: When a `tokio::net::TcpStream` is created, its underlying Linux file descriptor is registered with Linux `epoll` using non-blocking mode (`O_NONBLOCK`).
2. **First Poll**: A worker thread polls the future reading from the stream. The future issues a non-blocking `read()` syscall.
3. **EAGAIN / EWOULDBLOCK**: The kernel reports no data is currently buffered. The future extracts the `Waker` from `Context` and stores it inside the Reactor's registration table, keyed by the socket's file descriptor and interest mask (`EPOLLIN`). The future returns `Poll::Pending`.
4. **Reactor Wait**: A dedicated reactor thread (or an idle worker thread) blocks inside `epoll_wait()`.
5. **Kernel Notification**: When network packets arrive at the NIC, the kernel wakes the reactor thread from `epoll_wait()`.
6. **Wake Dispatch**: The reactor looks up the registered file descriptor, extracts the corresponding `Waker`, and calls `waker.wake()`.
7. **Rescheduling**: The `Waker` implementation pushes the task into the worker thread's local run queue.
8. **Repoll**: A Tokio worker thread pulls the task from the queue and calls `poll()`. This time, the non-blocking `read()` succeeds, returning `Poll::Ready(Ok(bytes_read))`.

---

## Go G-M-P Scheduler Internals: Stack Allocation, Preemption, and the Netpoller

Go approaches high-concurrency systems from an entirely different direction. Instead of transforming functions into stackless state machines via compiler lowering, Go maintains **stackful coroutines** known as **goroutines**. To Go application developers, code appears purely synchronous, blocking, and sequential. The complex machinery of suspension, multiplexing, and readiness notification is hidden inside the Go runtime scheduler.

```
The Go G-M-P Architecture:
┌──────────────────┐       ┌──────────────────┐
│ Machine (M0)     │       │ Machine (M1)     │  (OS Kernel Threads)
└────────┬─────────┘       └────────┬─────────┘
         │                          │
┌────────┴─────────┐       ┌────────┴─────────┐
│ Processor (P0)   │       │ Processor (P1)   │  (Logical Resource: GOMAXPROCS)
│ Local Run Queue: │       │ Local Run Queue: │
│ [ G2 ] [ G3 ]    │       │ [ G4 ] [ G5 ]    │
└────────┬─────────┘       └────────┬─────────┘
         │                          │
┌────────┴─────────┐       ┌────────┴─────────┐
│ Goroutine (G1)   │       │ Goroutine (G6)   │  (User Stackful Coroutines)
│ Stack: 2KB -> 4KB│       │ Stack: 2KB       │
└──────────────────┘       └──────────────────┘
```

### The G-M-P Structural Model

The Go scheduler balances three core entities:
- **G (Goroutine)**: Represents the execution context of a user routine. It contains its own call stack, instruction pointer (PC), stack bounds, and internal scheduling status (e.g., `_Grunnable`, `_Grunning`, `_Gwaiting`).
- **M (Machine)**: Represents an operating system thread managed by the OS kernel. An M executes code by binding to a logical processor `P`.
- **P (Processor)**: Represents a logical context required to execute Go user code. The number of `P` instances defaults to the machine's CPU core count (`GOMAXPROCS`). Each `P` owns a 256-element lock-free **Local Run Queue** of runnable goroutines, eliminating global lock contention.

### Dynamic Stack Resizing: Contiguous Stack Allocation

A standard Linux OS thread requires a large stack (1MB to 8MB) because the kernel cannot easily predict how deep function calls will go or how large stack arrays will be. A modern web service hosting 500,000 concurrent OS threads would consume 500GB to 4TB of physical RAM purely for stack storage, crashing the machine. Go solves this by starting each Goroutine with a microscopic stack of **just 2 Kilobytes**.

How does a 2KB stack handle deep recursion or large local allocations without overflowing?
1. **The Stack Check Preamble**: When the Go compiler generates assembly for any function, it inserts a tiny prologue at the beginning:
   ```assembly
   MOVQ (TLS), R14         // Load current goroutine pointer (g)
   CMPQ SP, 16(R14)        // Compare current SP with g.stackguard0
   JBE  call_morestack     // If SP <= stackguard0, trigger stack growth!
   ```
2. **Dynamic Stack Copying**: If the current stack space is insufficient, the function jumps to `runtime.morestack`. The Go runtime allocates a **new contiguous memory block twice the size** of the previous stack (e.g., growing from 2KB to 4KB, 8KB, up to 1GB on 64-bit architectures).
3. **Pointer Fixup**: The runtime copies the entire contents of the old stack to the new memory block. Because stack variables may hold pointers to other variables located on that same stack, the runtime traverses the active stack frames using compiler-generated type maps and **adjusts every internal pointer** to reflect the new memory offsets. The old stack memory is freed, and execution resumes transparently.

### Cooperative Preemption vs. Asynchronous Signal Preemption (`SIGURG`)

For years, Go relied strictly on **cooperative preemption points**. A running goroutine would only yield control if it called another function (triggering `morestack`), performed network or file I/O, acquired a channel or mutex lock, or called `runtime.Gosched()`.

This introduced a vulnerability: **the non-allocating tight loop starvation bug**.
```go
func computePrimes() {
    for {
        // Tight computational loop with no function calls and no allocations
    }
}
```
Prior to Go 1.14, if `computePrimes()` was scheduled on a `P`, it would run continuously without ever invoking `morestack` or a runtime syscall. It would monopolize the underlying OS thread `M` indefinitely. If `GOMAXPROCS=1`, the entire application would freeze: garbage collection could never complete (because GC stop-the-world phases wait for all goroutines to reach a safe point), and other goroutines would starve forever.

#### The Go 1.14+ Solution: Asynchronous Preemption via `SIGURG`

To permanently resolve this architectural defect, Go 1.14 introduced signal-based asynchronous preemption:
1. **The `sysmon` Thread**: The Go runtime maintains an ambient operating system thread named `sysmon` (system monitor) that runs continuously without a `P`.
2. **Starvation Detection**: Every 20 microseconds to 10 milliseconds, `sysmon` inspects the state of all `P` processors. If it detects that a goroutine has been continuously running on a `P` for more than **10 milliseconds**, it marks the goroutine for preemption.
3. **Signal Injection**: `sysmon` issues an OS kernel signal—specifically **`SIGURG`** (urgent out-of-band data signal)—directly to the OS thread `M` executing the stubborn goroutine. `SIGURG` was deliberately chosen because it is not used by standard libc functions and does not interfere with standard application debugging signals.
4. **Signal Interception**: The operating system interrupts the thread's execution and transfers control to the Go runtime's registered signal handler: `runtime.sighandler`.
5. **Instruction Pointer Rewiring**: If the runtime determines that the current execution point is at a valid safe point (where CPU registers and stack frames can be accurately traced by the garbage collector), `sighandler` modifies the saved thread execution context:
   - It pushes the address of `runtime.asyncPreempt` onto the thread's stack.
   - It updates the thread's program counter (PC) to point directly to `runtime.asyncPreempt`.
6. **Descheduling**: When the kernel resumes the thread, the thread does not continue the tight loop; it jumps into `asyncPreempt`. This function saves all floating-point and integer CPU registers, changes the goroutine state from `_Grunning` to `_Grunnable`, pushes it to the global run queue, and invokes `runtime.schedule()` to execute the next waiting goroutine!

```
Go 1.14+ Asynchronous Preemption Mechanism:
┌─────────────────┐
│  sysmon Thread  │ ──► Inspects P0: Has G1 been running > 10ms?
└────────┬────────┘
         │ YES
         ▼
[ Send OS Signal: pthread_kill(M0, SIGURG) ]
         │
         ▼
┌─────────────────┐
│   Thread M0     │ ──► OS interrupts tight CPU loop ──► invokes runtime.sighandler
└────────┬────────┘
         │
         ▼
[ sighandler modifies saved CPU registers: rewires Program Counter to runtime.asyncPreempt ]
         │
         ▼
[ Kernel returns to user-space: Thread immediately jumps to runtime.asyncPreempt ]
         │
         ▼
[ Saves CPU registers -> Transitions G1 to _Grunnable -> Enqueues G1 -> Runs G2 ]
```

### Go's Netpoller: Bridging Synchronous Code to OS Epoll

When a Go developer writes `n, err := conn.Read(buffer)`, the call appears completely synchronous. Behind the scenes, the Go runtime sets all network file descriptors to non-blocking mode (`O_NONBLOCK`). 
1. When `conn.Read()` executes, it attempts an immediate raw non-blocking system call.
2. If data is present in the kernel buffer, the call succeeds instantly, returning data with zero scheduler intervention.
3. If the kernel returns `EAGAIN` or `EWOULDBLOCK`, the goroutine does not block the OS thread `M`.
4. Instead, the runtime invokes `runtime.netpollblock()`. The goroutine is transitioned to the `_Gwaiting` state and decoupled from its processor `P`.
5. The runtime registers the socket file descriptor with the internal **netpoller** (which manages an OS-level `epoll` instance on Linux, `kqueue` on macOS, or `IOCP` on Windows).
6. The thread `M` immediately retrieves another runnable goroutine from `P`'s local run queue, remaining 100% productive.
7. Concurrently, the netpoller periodically calls `epoll_wait()`. When the socket becomes readable, the netpoller locates the sleeping goroutine and invokes `runtime.goready(g)`.
8. The goroutine's status shifts back to `_Grunnable`, and it is pushed into a `P`'s run queue, ready to resume execution right where it suspended.

---

## Common Misconceptions to Unlearn

### Misconception 1: "Rust futures execute in the background as soon as you create them, just like C# Tasks."
**Reality**: In C#, calling an `async` method immediately begins its execution. When you invoke `var task = ProcessAsync();`, the method runs synchronously until its first incomplete `await`. In Rust, futures are **entirely inert (cold)**. Calling `let f = process_async();` executes zero instructions inside the function body; it merely constructs the state machine struct on the current stack frame. If you do not pass it to an executor via `tokio::spawn(f)` or `.await` it, the future will never execute, will never make network calls, and will eventually be dropped without doing anything.

### Misconception 2: "Pinning in Rust automatically allocates memory on the heap."
**Reality**: `Pin` is a compile-time type wrapper that constrains pointer dereferencing; it has nothing to do with memory allocation. You can safely pin data to the physical stack frame using the `tokio::pin!` or `core::pin::pin!` macros. The compiler guarantees that the memory address of the stack-pinned struct remains invariant by disallowing moves for the remainder of that scope. Heap allocation via `Box::pin()` is merely the most convenient way to obtain a pinned pointer when transferring a future across thread boundaries.

### Misconception 3: "Go doesn't have an async/await keyword, so it must be spawning heavyweight OS threads for every goroutine."
**Reality**: Goroutines are user-space green threads multiplexed across an M:N scheduler. Each goroutine begins with an allocation of just 2KB of virtual memory (compared to 1MB–8MB for an OS thread stack). An application can effortlessly host 1,000,000 active goroutines simultaneously on a standard server without exhausting system memory.

### Misconception 4: "Using C#'s ValueTask eliminates all asynchronous allocation overhead."
**Reality**: `ValueTask<T>` only avoids allocation when the underlying operation **completes synchronously**. If the operation returns incomplete (`IsCompleted == false`), `ValueTask<T>` must either wrap a newly allocated `Task<T>` or rely on an `IValueTaskSource` implementation. If your architecture does not explicitly maintain complex, thread-safe object pools for `IValueTaskSource`, a suspended `ValueTask` incurs heap allocation overhead comparable to a standard `Task`.

### Misconception 5: "Rust's poll() loop constantly burns CPU cycles spinning to check if an I/O operation is ready."
**Reality**: A future's `poll()` method is only invoked when there is high probability that progress can be made. The executor never busy-spins. When a future returns `Poll::Pending`, it yields control completely. The executor sleeps inside OS-level readiness primitives (`epoll_wait`, `kqueue`, or timer interrupts) until the registered `Waker` notifies it to wake up.

---

## Comparative Architecture Summary

| Architectural Dimension | C# (.NET 8+) | Go (1.22+) | Rust (Tokio) |
| :--- | :--- | :--- | :--- |
| **Coroutine Model** | Stackless (Compiler-generated state machine structs) | Stackful (Goroutines with independent execution stacks) | Stackless (Compiler-generated anonymous state machine structs) |
| **Execution Activation** | **Hot**: Starts synchronously upon function invocation | **Hot**: Begins execution upon `go func()` invocation | **Cold**: Completely inert until polled by an executor |
| **Default Allocation** | Stack initially; **Boxes to Managed Heap** on suspension (`Task<T>`) | Tiny **2KB dynamic stack**; expands contiguously on demand | **Zero heap allocation**; lives on stack unless explicitly boxed (`Box::pin`) |
| **Memory Relocation Handling** | Transparently managed by GC compaction and pointer fixups | Handled by runtime stack copier (updates internal stack pointers) | Prevented at compile time via **`Pin<P>`** address invariance |
| **I/O Readiness Multiplexing** | OS thread pool driven by Windows IOCP / Linux epoll | Built-in runtime **Netpoller** (`epoll` / `kqueue`) | External async runtime reactor (Tokio / `mio` over `epoll` / `kqueue`) |
| **Task Resumption Model** | **Push**: Completing thread enqueues continuation `Action` | **Push**: Netpoller transitions G to runnable and schedules it | **Pull**: Subsystem signals `Waker.wake()`; Executor re-polls |
| **Preemption Architecture** | **Cooperative** at `await` suspension points | **Cooperative** (`morestack`) + **Asynchronous** via OS signal (`SIGURG`) | **Cooperative** strictly at `.await` yield points |
| **CPU Starvation Defense** | None: Long synchronous CPU loops block ThreadPool workers | Runtime `sysmon` thread injects `SIGURG` every 10ms to interrupt | None: Long computational loops block Tokio worker threads (requires `spawn_blocking`) |
| **Runtime Overhead** | Heavy: Managed GC engine, JIT compilation, metadata | Moderate: Managed GC engine, runtime scheduler, netpoller | **Zero**: No runtime included; user chooses minimal library runtime |
