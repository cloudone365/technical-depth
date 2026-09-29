# Week 28 · Code Comparison Rosetta Stone: Lock-Free SPSC Ring Buffer
### Pillar 4: Advanced Systems Engineering & Performance

> **Project:** A production-grade, bounded, lock-free Single-Producer Single-Consumer (SPSC) ring buffer transferring 50,000,000 messages between dedicated CPU cores.  
> **Key Mechanical Invariant:** The Producer writes payload data into an unshared slot and uses a **Release store** on the `head` pointer. The Consumer uses an **Acquire load** on the `head` pointer to establish a synchronization edge, reads the payload, and uses a **Release store** on the `tail` pointer to return the slot.

---

## Architecture Overview & False Sharing Prevention

A lock-free Single-Producer Single-Consumer ring buffer operates using two monotonically increasing 64-bit indices:
- `head`: The write index, updated exclusively by the Producer thread.
- `tail`: The read index, updated exclusively by the Consumer thread.

```
       Producer Thread                                Consumer Thread
    (Owns & Writes 'head')                         (Owns & Writes 'tail')
             |                                               |
             v                                               v
    +-----------------+                             +-----------------+
    |   head (64-bit) |                             |   tail (64-bit) |
    +-----------------+                             +-----------------+
    |  64-byte Cache  |                             |  64-byte Cache  |
    |  Line Padding   |                             |  Line Padding   |
    +-----------------+                             +-----------------+
             \                                               /
              +-------------> [ BUFFER SLOTS ] <------------+
                             Slot: [index & mask]
```

### Eliminating False Sharing
A standard CPU cache line is 64 bytes. If `head` (8 bytes) and `tail` (8 bytes) reside on the **same physical cache line**, every update by the Producer invalidates the Consumer's L1 cache line, and vice versa. This phenomenon—**False Sharing**—can degrade lock-free throughput by 5x to 10x.
- In C#, we use `[StructLayout(LayoutKind.Explicit)]` with `[FieldOffset]` to separate indices by 64 bytes.
- In Go, we place `[64]byte` padding arrays between struct fields.
- In Rust, we wrap atomic indices in a `#[repr(align(64))]` cache-padded structure.

---

## 1. C# (.NET 8) Implementation

### Project Configuration (`SpscRingBuffer.csproj`)
```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <AllowUnsafeBlocks>true</AllowUnsafeBlocks>
    <Optimize>true</Optimize>
  </PropertyGroup>
</Project>
```

### Complete Source Code (`Program.cs`)
```csharp
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;

namespace LockFreeSpsc
{
    // Explicit layout guarantees _head and _tail reside on separate 64-byte cache lines.
    [StructLayout(LayoutKind.Explicit, Size = 192)]
    public sealed class SpscRingBuffer<T>
    {
        [FieldOffset(0)]
        private readonly T?[] _buffer;

        [FieldOffset(8)]
        private readonly ulong _mask;

        // Head pointer occupied exclusively by Producer. Padded by 64 bytes.
        [FieldOffset(64)]
        private ulong _head;

        // Tail pointer occupied exclusively by Consumer. Padded by 64 bytes.
        [FieldOffset(128)]
        private ulong _tail;

        public SpscRingBuffer(int capacity)
        {
            // Capacity must be power-of-two for bitwise AND masking instead of IDIV.
            if (capacity < 2 || (capacity & (capacity - 1)) != 0)
                throw new ArgumentException("Capacity must be a power of 2", nameof(capacity));

            _buffer = new T[capacity];
            _mask = (ulong)(capacity - 1);
            _head = 0;
            _tail = 0;
        }

        public bool TryEnqueue(T item)
        {
            // Producer is sole writer of _head; unsynchronized read is safe.
            ulong currentHead = _head;

            // Volatile.Read guarantees Acquire barrier on ARM64; loads cannot hoist before it.
            ulong currentTail = Volatile.Read(ref _tail);

            if (currentHead - currentTail >= (ulong)_buffer.Length)
                return false; // Buffer full

            // Write payload before publishing head index.
            _buffer[currentHead & _mask] = item;

            // Volatile.Write issues Release barrier; payload is flushed before head updates.
            Volatile.Write(ref _head, currentHead + 1);
            return true;
        }

        public bool TryDequeue(out T? item)
        {
            // Consumer is sole writer of _tail.
            ulong currentTail = _tail;

            // Acquire load on _head synchronizes with Producer's Volatile.Write.
            ulong currentHead = Volatile.Read(ref _head);

            if (currentHead <= currentTail)
            {
                item = default;
                return false; // Buffer empty
            }

            // Read payload safely.
            item = _buffer[currentTail & _mask];

            // Clear reference in managed heap to prevent memory loitering.
            _buffer[currentTail & _mask] = default;

            // Release store on _tail informs Producer that slot is vacated.
            Volatile.Write(ref _tail, currentTail + 1);
            return true;
        }
    }

    public static class Program
    {
        private const int TotalMessages = 50_000_000;
        private const int BufferCapacity = 16384;

        public static void Main()
        {
            Console.WriteLine($"=== C# (.NET 8) Lock-Free SPSC Ring Buffer Benchmark ===");
            Console.WriteLine($"Messages: {TotalMessages:N0} | Buffer Capacity: {BufferCapacity:N0}");

            var ringBuffer = new SpscRingBuffer<long>(BufferCapacity);
            var stopwatch = new Stopwatch();

            var consumerThread = new Thread(() =>
            {
                long receivedCount = 0;
                long checksum = 0;

                while (receivedCount < TotalMessages)
                {
                    if (ringBuffer.TryDequeue(out long val))
                    {
                        checksum += val;
                        receivedCount++;
                    }
                    else
                    {
                        // Emits x86 PAUSE instruction to avoid pipeline thrashing during spin.
                        Thread.SpinWait(1);
                    }
                }

                stopwatch.Stop();
                double elapsedSec = stopwatch.Elapsed.TotalSeconds;
                Console.WriteLine($"[Consumer] Completed in {stopwatch.ElapsedMilliseconds} ms");
                Console.WriteLine($"[Consumer] Throughput: {TotalMessages / elapsedSec:N0} msgs/sec");
                Console.WriteLine($"[Consumer] Checksum: {checksum}");
            })
            { Priority = ThreadPriority.Highest, IsBackground = false };

            var producerThread = new Thread(() =>
            {
                Thread.Sleep(50); // Allow consumer thread to initialize
                stopwatch.Start();

                for (long i = 1; i <= TotalMessages; i++)
                {
                    while (!ringBuffer.TryEnqueue(i))
                    {
                        Thread.SpinWait(1);
                    }
                }
            })
            { Priority = ThreadPriority.Highest, IsBackground = false };

            consumerThread.Start();
            producerThread.Start();

            producerThread.Join();
            consumerThread.Join();
        }
    }
}
```

---

## 2. Go (1.22+) Implementation

### Project Configuration (`go.mod`)
```go
module spsc_ringbuffer

go 1.22
```

### Complete Source Code (`main.go`)
```go
package main

import (
	"fmt"
	"runtime"
	"sync/atomic"
	"time"
)

const (
	TotalMessages  uint64 = 50_000_000
	BufferCapacity uint64 = 16384
)

// SpscRingBuffer uses [56]byte / [64]byte padding arrays to ensure head and tail
// inhabit distinct 64-byte L1 cache lines, eliminating False Sharing.
type SpscRingBuffer[T any] struct {
	_pad0  [64]byte
	buffer []T
	mask   uint64
	_pad1  [56]byte // 64 - sizeof(mask:8) = 56 bytes
	head   uint64   // Modified exclusively by Producer
	_pad2  [56]byte // 64 - sizeof(head:8) = 56 bytes
	tail   uint64   // Modified exclusively by Consumer
	_pad3  [56]byte // 64 - sizeof(tail:8) = 56 bytes
}

func NewSpscRingBuffer[T any](capacity uint64) *SpscRingBuffer[T] {
	if capacity < 2 || (capacity&(capacity-1)) != 0 {
		panic("capacity must be a power of two")
	}
	return &SpscRingBuffer[T]{
		buffer: make([]T, capacity),
		mask:   capacity - 1,
	}
}

func (rb *SpscRingBuffer[T]) Enqueue(item T) bool {
	// Producer owns head.
	currentHead := rb.head

	// Load tail with Sequential Consistency (Go sync/atomic default).
	currentTail := atomic.LoadUint64(&rb.tail)

	if currentHead-currentTail >= uint64(len(rb.buffer)) {
		return false // Buffer full
	}

	rb.buffer[currentHead&rb.mask] = item

	// Store head with Sequential Consistency. Flushes payload write to cache.
	atomic.StoreUint64(&rb.head, currentHead+1)
	return true
}

func (rb *SpscRingBuffer[T]) Dequeue() (T, bool) {
	// Consumer owns tail.
	currentTail := rb.tail

	// Load head with Sequential Consistency to synchronize with Producer.
	currentHead := atomic.LoadUint64(&rb.head)

	if currentHead <= currentTail {
		var zero T
		return zero, false // Buffer empty
	}

	item := rb.buffer[currentTail&rb.mask]

	// Zero reference in slice to prevent GC memory retention.
	var zero T
	rb.buffer[currentTail&rb.mask] = zero

	// Store tail with Sequential Consistency, notifying Producer slot is freed.
	atomic.StoreUint64(&rb.tail, currentTail+1)
	return item, true
}

func main() {
	runtime.GOMAXPROCS(runtime.NumCPU())

	fmt.Printf("=== Go (1.22+) Lock-Free SPSC Ring Buffer Benchmark ===\n")
	fmt.Printf("Messages: %d | Buffer Capacity: %d\n", TotalMessages, BufferCapacity)

	rb := NewSpscRingBuffer[uint64](BufferCapacity)
	startChan := make(chan struct{})
	doneChan := make(chan time.Duration)

	go func() {
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()

		<-startChan
		startTime := time.Now()
		var receivedCount, checksum uint64 = 0, 0

		for receivedCount < TotalMessages {
			if val, ok := rb.Dequeue(); ok {
				checksum += val
				receivedCount++
			} else {
				runtime.Gosched()
			}
		}

		elapsed := time.Since(startTime)
		fmt.Printf("[Consumer] Checksum: %d\n", checksum)
		doneChan <- elapsed
	}()

	go func() {
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()

		close(startChan)
		for i := uint64(1); i <= TotalMessages; i++ {
			for !rb.Enqueue(i) {
				runtime.Gosched()
			}
		}
	}()

	elapsed := <-doneChan
	throughput := float64(TotalMessages) / elapsed.Seconds()
	fmt.Printf("[Consumer] Completed in %v\n", elapsed)
	fmt.Printf("[Consumer] Throughput: %.0f msgs/sec\n", throughput)
}
```

---

## 3. Rust (2021 Edition) Implementation

### Project Configuration (`Cargo.toml`)
```toml
[package]
name = "spsc_ringbuffer"
version = "0.1.0"
edition = "2021"

[profile.release]
opt-level = 3
lto = "fat"
codegen-units = 1
panic = "abort"
```

### Complete Source Code (`src/main.rs`)
```rust
use std::cell::UnsafeCell;
use std::mem::MaybeUninit;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Instant;

// Custom 64-byte alignment wrapper preventing False Sharing between head and tail.
#[repr(align(64))]
struct CachePadded<T>(pub T);

/// High-performance, zero-allocation SPSC Bounded Queue.
pub struct SpscRingBuffer<T> {
    buffer: Box<[UnsafeCell<MaybeUninit<T>>]>,
    mask: usize,
    head: CachePadded<AtomicUsize>,
    tail: CachePadded<AtomicUsize>,
}

unsafe impl<T: Send> Send for SpscRingBuffer<T> {}
unsafe impl<T: Send> Sync for SpscRingBuffer<T> {}

impl<T> SpscRingBuffer<T> {
    pub fn new(capacity: usize) -> Self {
        assert!(capacity >= 2 && capacity.is_power_of_two(), "Capacity must be power of two");
        let mut storage = Vec::with_capacity(capacity);
        for _ in 0..capacity {
            storage.push(UnsafeCell::new(MaybeUninit::uninit()));
        }

        Self {
            buffer: storage.into_boxed_slice(),
            mask: capacity - 1,
            head: CachePadded(AtomicUsize::new(0)),
            tail: CachePadded(AtomicUsize::new(0)),
        }
    }

    pub fn try_push(&self, item: T) -> Result<(), T> {
        // Relaxed load: Producer owns head exclusively; no concurrent write occurs.
        let current_head = self.head.0.load(Ordering::Relaxed);

        // Acquire load on tail synchronizes with Consumer's Release store on tail.
        let current_tail = self.tail.0.load(Ordering::Acquire);

        if current_head.wrapping_sub(current_tail) >= self.buffer.len() {
            return Err(item); // Buffer full
        }

        let slot_idx = current_head & self.mask;
        unsafe {
            // Write directly to unshared slot without lock overhead.
            (*self.buffer[slot_idx].get()).write(item);
        }

        // Release store on head: Guarantees payload write is globally visible
        // before updated head index is published to Consumer.
        self.head.0.store(current_head.wrapping_add(1), Ordering::Release);
        Ok(())
    }

    pub fn try_pop(&self) -> Option<T> {
        // Relaxed load: Consumer owns tail exclusively.
        let current_tail = self.tail.0.load(Ordering::Relaxed);

        // Acquire load on head synchronizes with Producer's Release store on head.
        let current_head = self.head.0.load(Ordering::Acquire);

        if current_head <= current_tail {
            return None; // Buffer empty
        }

        let slot_idx = current_tail & self.mask;
        let item = unsafe {
            // Read payload; Acquire load ensures Producer write has completed.
            (*self.buffer[slot_idx].get()).assume_init_read()
        };

        // Release store on tail: Ensures read is complete before Producer overwrites slot.
        self.tail.0.store(current_tail.wrapping_add(1), Ordering::Release);
        Some(item)
    }
}

impl<T> Drop for SpscRingBuffer<T> {
    fn drop(&mut self) {
        // Drain any remaining active elements to run their Drop destructors cleanly.
        while self.try_pop().is_some() {}
    }
}

fn main() {
    const TOTAL_MESSAGES: usize = 50_000_000;
    const BUFFER_CAPACITY: usize = 16384;

    println!("=== Rust (2021) Lock-Free SPSC Ring Buffer Benchmark ===");
    println!("Messages: {} | Buffer Capacity: {}", TOTAL_MESSAGES, BUFFER_CAPACITY);

    let ring_buffer = Arc::new(SpscRingBuffer::<u64>::new(BUFFER_CAPACITY));
    let rb_consumer = Arc::clone(&ring_buffer);
    let rb_producer = Arc::clone(&ring_buffer);

    let consumer_handle = thread::spawn(move || {
        let mut received_count = 0;
        let mut checksum = 0u64;
        let start = Instant::now();

        while received_count < TOTAL_MESSAGES {
            if let Some(val) = rb_consumer.try_pop() {
                checksum += val;
                received_count += 1;
            } else {
                std::hint::spin_loop();
            }
        }

        let elapsed = start.elapsed();
        println!("[Consumer] Completed in {:?}", elapsed);
        println!("[Consumer] Throughput: {:.0} msgs/sec", TOTAL_MESSAGES as f64 / elapsed.as_secs_f64());
        println!("[Consumer] Checksum: {}", checksum);
    });

    let producer_handle = thread::spawn(move || {
        thread::sleep(std::time::Duration::from_millis(10));
        for i in 1..=TOTAL_MESSAGES as u64 {
            let mut item = i;
            loop {
                match rb_producer.try_push(item) {
                    Ok(()) => break,
                    Err(returned_item) => {
                        item = returned_item;
                        std::hint::spin_loop();
                    }
                }
            }
        }
    });

    producer_handle.join().unwrap();
    consumer_handle.join().unwrap();
}
```

---

## 4. Exact Build and Run Commands

Execute these benchmarks in Release mode to enable full compiler optimizations, inlining, and dead-code elimination.

```bash
# -------------------------------------------------------------
# 1. C# (.NET 8)
# -------------------------------------------------------------
cd csharp
dotnet build -c Release
dotnet run -c Release --no-build

# -------------------------------------------------------------
# 2. Go (1.22+)
# -------------------------------------------------------------
cd go
go build -o spsc_go main.go
./spsc_go

# -------------------------------------------------------------
# 3. Rust (2021 Edition)
# -------------------------------------------------------------
cd rust
cargo build --release
./target/release/spsc_ringbuffer

# -------------------------------------------------------------
# Advanced: Core Pinning to Eliminate NUMA & Scheduling Noise
# -------------------------------------------------------------
# Pin on Linux to Core 0 (Producer) and Core 2 (Consumer):
taskset -c 0,2 ./target/release/spsc_ringbuffer
```

---

## 5. Critical Observations for C# Developers

### 1. Cache Line Alignment Control: Declarative vs. Explicit
In C#, preventing false sharing requires `[StructLayout(LayoutKind.Explicit, Size = 192)]` coupled with explicit `[FieldOffset(N)]` attributes. If an engineer forgets this or adds a new field without recalculating offsets, memory overlap or false sharing occurs silently. 

In Go, there is no compiler attribute for alignment; developers must manually insert unexported `_ [64]byte` filler fields.

In Rust, alignment is a first-class language feature: `#[repr(align(64))]` can be applied directly to a generic struct wrapper (`CachePadded<T>`). The compiler enforces alignment rules automatically, ensuring that the allocator respects the 64-byte boundary both on the stack and the heap.

### 2. Assembly Instruction Emission & Fence Overhead
- **Rust:** `self.head.0.store(val, Ordering::Release)` compiles on x86-64 to a standard `MOV [rdi], rax`. Because x86 hardware provides Total Store Order, no CPU fence instruction is required! On ARM64, it compiles to a single `STLR` instruction.
- **C#:** `Volatile.Write(ref _head, val)` is recognized as an intrinsic by RyuJIT. On x86, it lowers to a standard `MOV`. On ARM64, it emits a `STLR` or `DMB ISHLD`. However, if you use `Interlocked.Exchange`, C# emits `LOCK CMPXCHG` or `LOCK XCHG`, stalling the core pipeline.
- **Go:** `atomic.StoreUint64(&rb.head, val)` is strictly sequentially consistent (`SeqCst`). On x86-64, Go emits an `XCHG` instruction (which implies a hardware `LOCK` prefix) to drain the store buffer, generating significantly higher bus contention than Rust's zero-cost Release `MOV`.

### 3. Memory Safety, `MaybeUninit`, and Resource Leaks
In C#, creating `new T[capacity]` initializes all elements to their default values (`null` or zero). When an object reference is dequeued, C# code must write `_buffer[idx] = default;` to clear the reference; otherwise, the garbage collector will keep the object alive in memory (a "loitering reference" leak).

In Rust, we utilize `Box<[UnsafeCell<MaybeUninit<T>>]>`. The memory is allocated uninitialized without calling constructors. When popping an element, `assume_init_read()` transfers ownership of `T` directly to the caller via bitwise copy without running destructors on the slot. To prevent memory leaks when the ring buffer is dropped, we implement the `Drop` trait to explicitly drain and drop any unconsumed elements.

### 4. The Physics of Spin-Waiting: `Thread.SpinWait` vs. `std::hint::spin_loop`
When the ring buffer is temporarily full or empty, high-throughput systems cannot yield to the OS kernel (`Thread.Sleep` or syscall mutexes take 1,000 to 5,000 nanoseconds, destroying throughput). Instead, they spin-wait.

- In Rust, `std::hint::spin_loop()` lowers directly to the x86 `PAUSE` instruction (or ARM `YIELD`). The `PAUSE` instruction delays the core pipeline by ~12 to 140 cycles (depending on Intel vs AMD generation), avoiding memory order violation penalties when exiting the loop.
- In C#, `Thread.SpinWait(iterations)` issues the same `PAUSE` instruction in a small loop.
- In Go, `runtime.Gosched()` yields the current goroutine to the Go runtime scheduler. While this prevents CPU starvation, it introduces runtime context switching overhead, causing Go's latency variance to be higher than Rust's native hardware spin loop.
