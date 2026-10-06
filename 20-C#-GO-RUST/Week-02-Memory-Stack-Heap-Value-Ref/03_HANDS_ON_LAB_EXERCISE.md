# Week 02 · Hands-On Lab Exercise: False Sharing, Cache Contention & "Lost Mutations"
### Hardware-Sympathetic Systems Engineering in C#, Go, and Rust

> **Lab Objective:** Build practical intuition for how hardware interacts with software:
> 1. **The "Lost Mutation" Value Trap:** Debug and fix subtle bugs caused by value semantics in Go and C# collection iterations, analyzing the assembly differences between index access and range copies.
> 2. **The False Sharing / Cache Line Bouncing Lab:** Write a multi-threaded counter that triggers violent cache line invalidation across CPU cores. Measure the dramatic performance drop, and fix it using hardware alignment and padding techniques (`#[repr(align(64))]` in Rust, `[StructLayout]` in C#, and explicit byte padding in Go).
> 3. **Friday Mob Review:** Defend your findings with hardware metrics, explain the MESI cache coherence protocol, and complete the certification checklist.

---

## Lab Architecture & Timetable

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LAB WORKFLOW & TIMETABLE                        │
├────────────────────────────────────────────────────────────────────────┤
│ • Day 1-2: Value Semantics & The "Lost Mutation" Bug                   │
│            Reproduce value copy bug in Go range loops and C# structs.  │
│            Dissect stack frames, compare index vs pointer fixes.       │
│                                                                        │
│ • Day 3-4: Multi-Threaded False Sharing & Cache Line Bouncing          │
│            Build concurrent counter across 2 CPU cores.                │
│            Observe 6x-10x slowdown caused by 64-byte cache sharing.    │
│            Apply hardware alignment: #[repr(align(64))] & FieldOffset. │
│                                                                        │
│ • Day 5:   Friday Mob Review & Hardware Metric Defense                 │
│            Analyze L1 cache hit rates, discuss MESI protocol,          │
│            certify team on hardware memory anatomy.                    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: Days 1–2 — The "Lost Mutation" Bug & Pass-by-Value Traps

### 1.1 The C# Developer's Mental Model Trap
In C#, when you have a `List<CustomerClass>`, the list contains 8-byte references to heap objects:
```csharp
var list = new List<CustomerClass> { new CustomerClass { Balance = 100 } };
foreach (var c in list) {
    c.Balance = 200; // Modifies the heap object! The list sees the change.
}
```
If you switch `CustomerClass` to `CustomerStruct`, the exact same loop fails to compile in C# with error `CS1612: Cannot modify the return value of 'List<T>.this[int]' because it is not a variable`.

In **Go**, the compiler does not stop you! The `for _, item := range slice` syntax creates a **local copy of the struct on every iteration**. Mutating `item` silently modifies the temporary stack copy, leaving the original slice untouched!

### 1.2 The Buggy Go Code (`lost_mutation.go`)
Save this file into `labs/week02/go/lost_mutation.go`:

```go
package main

import (
	"fmt"
	"unsafe"
)

type ServerConfig struct {
	Port       int
	MaxClients int
	IsActive   bool
}

func main() {
	configs := []ServerConfig{
		{Port: 8080, MaxClients: 100, IsActive: false},
		{Port: 8081, MaxClients: 200, IsActive: false},
	}

	fmt.Println("--- BEFORE MUTATION ---")
	fmt.Printf("Config 0: %+v (Addr: 0x%X)\n", configs[0], uintptr(unsafe.Pointer(&configs[0])))
	fmt.Printf("Config 1: %+v (Addr: 0x%X)\n\n", configs[1], uintptr(unsafe.Pointer(&configs[1])))

	// SUBTLE BUG: 'cfg' is an 8-byte/16-byte VALUE COPY pushed to the local stack frame!
	for _, cfg := range configs {
		fmt.Printf("[Inside Loop] 'cfg' local copy address: 0x%X\n", uintptr(unsafe.Pointer(&cfg)))
		cfg.IsActive = true // Modifies ONLY the local stack copy!
	}

	fmt.Println("\n--- AFTER MUTATION ---")
	fmt.Printf("Config 0: %+v\n", configs[0])
	fmt.Printf("Config 1: %+v\n", configs[1])

	if !configs[0].IsActive {
		fmt.Println("\n\x1b[31m[FAILED] Mutation was LOST! Slice elements remained inactive.\x1b[0m")
	}
}
```

Run the code:
```bash
go run lost_mutation.go
```
Observe the address output: `&cfg` is at a completely different stack address than `&configs[0]`.

### 1.3 The Two Solutions: Index vs. Pointer
There are two ways to fix this in Go:
1. **Fix A (Index Access - Preferred):** `configs[i].IsActive = true`.  
   *Why this wins:* Keeps `configs` as a contiguous flat array of structs in memory (`[]ServerConfig`). Preserves L1 cache prefetching!
2. **Fix B (Pointer Slice):** `configs := []*ServerConfig{ ... }`.  
   *The trade-off:* Allows mutating `cfg.IsActive = true`, but scatters each `ServerConfig` across the heap, destroying cache locality.

```go
// Fix A: Indexing preserves contiguous memory layout
for i := range configs {
    configs[i].IsActive = true // Mutates directly in slice backing array!
}
```

---

## Part 2: Days 3–4 — The False Sharing & Cache Contention Lab

### 2.1 The Hardware Physics: MESI Protocol & Cache Lines
CPUs synchronize memory across cores using the **MESI (Modified, Exclusive, Shared, Invalid)** protocol.
* When Core 0 and Core 1 both read variables that reside in the same **64-byte cache line**, both cores mark that line as **Shared (S)**.
* When Core 0 writes to its variable, Core 0 must transition the cache line to **Modified (M)**. To do this, it broadcasts an **Invalidate message** across the CPU interconnect bus.
* Core 1's cache controller marks its local copy of the cache line as **Invalid (I)**.
* When Core 1 attempts to write to its completely independent variable, it experiences a **Cache Miss**, stalls execution for tens of nanoseconds, re-reads the cache line from L3 or DRAM, and invalidates Core 0!
* This continuous tug-of-war is called **Cache Line Bouncing (False Sharing)**.

```
       Core 0                                Core 1
┌──────────────────┐                  ┌──────────────────┐
│ writes counter_a │                  │ writes counter_b │
└────────┬─────────┘                  └────────┬─────────┘
         │                                     │
         ▼                                     ▼
 ┌───────────────┐                     ┌───────────────┐
 │ L1 Cache: [M] │                     │ L1 Cache: [I] │ (Invalidated!)
 └───────┬───────┘                     └───────┬───────┘
         │        Shared 64-Byte Line          │
         └─────────────► ◄─────────────────────┘
                 (Bus Invalidation Storm)
```

---

### 2.2 The Rust False Sharing Benchmark (`src/main.rs`)

Save this in a new Cargo project (`cargo new false_sharing_lab --bin`):

```rust
// File: src/main.rs
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Instant;

// UNPADDED STRUCT: counter_a and counter_b share the SAME 64-byte cache line!
// AtomicU64 is 8 bytes. Size = 16 bytes. Both sit in the first 16 bytes of one cache line.
struct UnpaddedCounters {
    counter_a: AtomicU64, // 8 bytes (offset 0)
    counter_b: AtomicU64, // 8 bytes (offset 8)
}

// HARDWARE-ALIGNED STRUCT: Forces counter_b onto a separate 64-byte cache line!
#[repr(align(64))]
struct CacheAlignedCounter {
    value: AtomicU64,
}

struct PaddedCounters {
    counter_a: CacheAlignedCounter, // Occupies Cache Line 0 (64 bytes)
    counter_b: CacheAlignedCounter, // Occupies Cache Line 1 (64 bytes)
}

const ITERATIONS: u64 = 100_000_000;

fn benchmark_unpadded() {
    let counters = Arc::new(UnpaddedCounters {
        counter_a: AtomicU64::new(0),
        counter_b: AtomicU64::new(0),
    });

    let c1 = Arc::clone(&counters);
    let c2 = Arc::clone(&counters);

    let start = Instant::now();

    // Core 0 updates counter_a
    let t1 = thread::spawn(move || {
        for _ in 0..ITERATIONS {
            c1.counter_a.fetch_add(1, Ordering::Relaxed);
        }
    });

    // Core 1 updates counter_b
    let t2 = thread::spawn(move || {
        for _ in 0..ITERATIONS {
            c2.counter_b.fetch_add(1, Ordering::Relaxed);
        }
    });

    t1.join().unwrap();
    t2.join().unwrap();

    println!("[Unpadded / False Sharing] Elapsed: {:?}", start.elapsed());
}

fn benchmark_padded() {
    let counters = Arc::new(PaddedCounters {
        counter_a: CacheAlignedCounter { value: AtomicU64::new(0) },
        counter_b: CacheAlignedCounter { value: AtomicU64::new(0) },
    });

    let c1 = Arc::clone(&counters);
    let c2 = Arc::clone(&counters);

    let start = Instant::now();

    let t1 = thread::spawn(move || {
        for _ in 0..ITERATIONS {
            c1.counter_a.value.fetch_add(1, Ordering::Relaxed);
        }
    });

    let t2 = thread::spawn(move || {
        for _ in 0..ITERATIONS {
            c2.counter_b.value.fetch_add(1, Ordering::Relaxed);
        }
    });

    t1.join().unwrap();
    t2.join().unwrap();

    println!("[Padded / Aligned (64B)]   Elapsed: {:?}", start.elapsed());
}

fn main() {
    println!("Running False Sharing Benchmark (100 Million Iterations per Core)...");
    benchmark_unpadded();
    benchmark_padded();
}
```

Run in release mode:
```bash
cargo run --release
```

**Expected Empirical Output:**
```text
Running False Sharing Benchmark (100 Million Iterations per Core)...
[Unpadded / False Sharing] Elapsed: 1.842s
[Padded / Aligned (64B)]   Elapsed: 0.281s

-> The Padded / Aligned version is 6.55x FASTER!
```

---

### 2.3 The Go False Sharing Benchmark (`false_sharing_test.go`)

```go
// File: labs/week02/go/false_sharing_test.go
package main

import (
	"sync"
	"sync/atomic"
	"testing"
)

type UnpaddedCounters struct {
	CounterA uint64 // 8 bytes
	CounterB uint64 // 8 bytes (shares cache line with CounterA!)
}

type PaddedCounters struct {
	CounterA uint64
	_        [56]byte // 56 bytes padding to complete 64-byte cache line!
	CounterB uint64
	_        [56]byte // 56 bytes padding
}

const OpsPerGoroutine = 50_000_000

func BenchmarkFalseSharing(b *testing.B) {
	for i := 0; i < b.N; i++ {
		var counters UnpaddedCounters
		var wg sync.WaitGroup
		wg.Add(2)

		go func() {
			defer wg.Done()
			for j := 0; j < OpsPerGoroutine; j++ {
				atomic.AddUint64(&counters.CounterA, 1)
			}
		}()

		go func() {
			defer wg.Done()
			for j := 0; j < OpsPerGoroutine; j++ {
				atomic.AddUint64(&counters.CounterB, 1)
			}
		}()

		wg.Wait()
	}
}

func BenchmarkPaddedCacheLines(b *testing.B) {
	for i := 0; i < b.N; i++ {
		var counters PaddedCounters
		var wg sync.WaitGroup
		wg.Add(2)

		go func() {
			defer wg.Done()
			for j := 0; j < OpsPerGoroutine; j++ {
				atomic.AddUint64(&counters.CounterA, 1)
			}
		}()

		go func() {
			defer wg.Done()
			for j := 0; j < OpsPerGoroutine; j++ {
				atomic.AddUint64(&counters.CounterB, 1)
			}
		}()

		wg.Wait()
	}
}
```

Run the benchmark:
```bash
go test -bench=Benchmark -benchtime=1x
```

**Expected Go Output:**
```text
BenchmarkFalseSharing-12        1    982310400 ns/op (0.98s)
BenchmarkPaddedCacheLines-12    1    164102100 ns/op (0.16s)  <-- 6.0x FASTER!
```

---

### 2.4 The C# (.NET 8) False Sharing Solution

In C#, you achieve cache line alignment using the `StructLayout` and `FieldOffset` attributes:

```csharp
using System.Runtime.InteropServices;
using System.Threading;

// Struct explicitly sized to 128 bytes (two 64-byte cache lines)
[StructLayout(LayoutKind.Explicit, Size = 128)]
public struct PaddedCountersCSharp
{
    [FieldOffset(0)]  // Start of Cache Line 0
    public long CounterA;

    [FieldOffset(64)] // Start of Cache Line 1 (Zero False Sharing!)
    public long CounterB;
}
```

---

## Part 3: Friday Mob Review & Defense Protocol

Gather the 5-engineer team. Project the terminal benchmark numbers on the screen.

### 7 Mandatory Technical Defense Questions

#### 1. Why does adding 56 bytes of dead padding *improve* execution speed?
* **Expected Answer:** While padding wastes memory, it isolates independent atomic variables into distinct 64-byte CPU cache lines. This prevents the hardware MESI cache coherence protocol from constantly invalidating L1 cache lines between cores, eliminating bus contention and pipeline stalls.

#### 2. Why did modifying `cfg.IsActive = true` in the Go range loop fail to mutate the original slice?
* **Expected Answer:** The Go `for i, v := range slice` statement copies the value at index `i` into a temporary local variable `v` allocated in the function's stack frame. Mutating `v` modifies only that local copy. The backing array in the slice is never updated unless accessed directly by index (`slice[i]`).

#### 3. How does Rust's `#[repr(align(64))]` differ from Go's explicit `_ [56]byte` padding?
* **Expected Answer:** `#[repr(align(64))]` instructs the LLVM compiler to set the struct's base memory address to a multiple of 64 bytes in addition to padding the size. Go's `_ [56]byte` guarantees size separation between fields, but does not guarantee that the base struct itself starts at a 64-byte boundary unless explicitly allocated on a cache-aligned boundary.

#### 4. In x86-64 assembly, what exact instructions represent stack allocation and deallocation?
* **Expected Answer:**
  * Allocation: `sub rsp, N` (subtracts $N$ bytes from the stack pointer).
  * Deallocation: `add rsp, N` or `mov rsp, rbp; pop rbp; ret` (restores the stack pointer).

#### 5. Why does iterating over a `List<CustomerClass>` in C# cause more L1 cache misses than an array of `CustomerStruct`?
* **Expected Answer:** An array of `CustomerStruct` is laid out contiguously in memory; loading one element loads the next several elements into the 64-byte L1 cache line automatically (spatial locality). A `List<CustomerClass>` contains pointers to objects scattered across the managed heap; every dereference requires accessing an arbitrary memory address, causing repeated L1/L2 cache misses.

#### 6. What is the MESI cache coherence protocol, and which state causes thread stalls?
* **Expected Answer:** MESI stands for Modified, Exclusive, Shared, Invalid. When a core writes to a shared line, other cores' lines enter the **Invalid (I)** state. Any subsequent read or write by another core results in a cache miss stall until the line is reloaded from L3/DRAM.

#### 7. If Go preserves source-code struct field order, how should you order fields to minimize memory?
* **Expected Answer:** Always order fields from largest alignment requirement to smallest (e.g., `int64` / pointers [8B], followed by `int32` [4B], `int16` [2B], and `bool`/`byte` [1B]). This prevents the compiler from inserting alignment padding between mismatched fields.

---

## Lab Sign-off Checklist

Each engineer must verify and sign off:
* [ ] **Value Semantics Verification:** I have reproduced and fixed the Go lost-mutation bug using index access.
* [ ] **Cache Line Physics:** I can explain what a 64-byte cache line is and how spatial locality works.
* [ ] **False Sharing Proof:** I have personally executed the multi-threaded false sharing benchmark and observed a 5x+ speedup when cache lines are aligned.
* [ ] **Struct Alignment Math:** I can calculate the memory size and padding of an unaligned struct by hand.
* [ ] **Stack vs Heap Mechanics:** I understand why stack allocation is a single subtraction instruction while heap allocation requires allocator synchronization.
