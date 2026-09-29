# Week 28 · Hands-On Lab: Concurrency Stress & Memory Order Verification
### Pillar 4: Advanced Systems Engineering & Performance

> **Format:** Team Lab (Mon–Tue: Go Reference & Benchmarking → Wed–Thu: Rust SPMC & Miri Verification → Friday: Mob Review & Sign-Off)  
> **Hardware Target:** 8-core x86-64 or ARM64 workstation/instance  
> **Workload:** 100,000,000 messages transferred under extreme concurrent contention  
> **Deliverable:** Working SPSC/SPMC ring buffers, passing Miri weak-memory verification and Go race detection.

---

## Lab Objective

The primary trap of lock-free programming is that concurrency bugs rarely manifest as deterministic crashes in local test suites. On x86-64 machines, the hardware's Total Store Order (TSO) hides missing memory barriers. 

In this lab, your team will:
1. Benchmark a complete Single-Producer Multi-Consumer (SPMC) ring buffer transferring 100,000,000 messages across 8 CPU cores in Go.
2. Implement a lock-free SPMC queue in Rust using atomic Compare-And-Swap (`CAS`) loops and cache-line padding.
3. Use **Go's Race Detector** (`-race`) and **Rust's Miri interpreter** (`-Zmiri-weak-memory-emulation`) to mathematically detect data races and prove memory ordering correctness.
4. Profile hardware cache invalidations using Linux `perf` and analyze false sharing down to the bus level.
5. Conduct a Friday Mob Review dissecting cache invalidation storms and assembly-level barrier emission.

---

## Day 1–2: High-Throughput Concurrency Stress Lab (Go Reference)

In an SPMC queue, a single producer thread publishes data, while multiple concurrent consumer threads contend to read messages. Because multiple consumers read simultaneously, updating `tail` cannot be a simple atomic write; it requires a **Compare-And-Swap (CAS)** loop to atomically claim the next available slot.

### Complete Go Reference Implementation (`spmc_stress_test.go`)

```go
package main

import (
	"fmt"
	"runtime"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

const (
	TotalStressMessages uint64 = 100_000_000
	StressQueueCapacity uint64 = 65536
	NumConsumers               = 7 // 1 Producer + 7 Consumers = 8 active cores
)

type Message struct {
	Sequence uint64
	Payload  uint64
}

// SpmcRingBuffer implements a Single-Producer Multi-Consumer bounded queue.
// Head and tail are padded with [56]byte / [64]byte to eliminate false sharing.
type SpmcRingBuffer struct {
	_pad0   [64]byte
	buffer  []Message
	mask    uint64
	_pad1   [56]byte
	head    uint64 // Solely updated by Producer
	_pad2   [56]byte
	tail    uint64 // Contended by multiple Consumers via CAS
	_pad3   [56]byte
}

func NewSpmcRingBuffer(capacity uint64) *SpmcRingBuffer {
	if capacity < 2 || (capacity&(capacity-1)) != 0 {
		panic("capacity must be a power of two")
	}
	return &SpmcRingBuffer{
		buffer: make([]Message, capacity),
		mask:   capacity - 1,
	}
}

func (q *SpmcRingBuffer) Enqueue(msg Message) bool {
	// Producer owns head. Non-atomic local read is safe for head.
	currentHead := q.head
	currentTail := atomic.LoadUint64(&q.tail)

	if currentHead-currentTail >= uint64(len(q.buffer)) {
		return false // Queue is full
	}

	q.buffer[currentHead&q.mask] = msg

	// Atomic store acts as a Release barrier, publishing slot data to all consumers.
	atomic.StoreUint64(&q.head, currentHead+1)
	return true
}

func (q *SpmcRingBuffer) Dequeue() (Message, bool) {
	for {
		currentTail := atomic.LoadUint64(&q.tail)
		currentHead := atomic.LoadUint64(&q.head)

		if currentTail >= currentHead {
			return Message{}, false // Queue is empty
		}

		// Read payload speculatively before claiming slot
		msg := q.buffer[currentTail&q.mask]

		// Atomically claim the slot. If another consumer claimed it first, retry.
		if atomic.CompareAndSwapUint64(&q.tail, currentTail, currentTail+1) {
			return msg, true
		}

		// CPU pause to prevent interconnect saturation during contention
		runtime.Gosched()
	}
}

func Test100MMessageStress(t *testing.T) {
	runtime.GOMAXPROCS(8)
	fmt.Printf("=== SPMC 100M Message Concurrency Stress Lab ===\n")
	fmt.Printf("Cores: 8 (1 Producer, %d Consumers) | Capacity: %d\n", NumConsumers, StressQueueCapacity)

	q := NewSpmcRingBuffer(StressQueueCapacity)

	var wg sync.WaitGroup
	var totalConsumed uint64
	var checksumSum uint64

	consumerCounts := make([]uint64, NumConsumers)
	startSignal := make(chan struct{})
	startTime := time.Now()

	// Launch 7 Consumers
	for i := 0; i < NumConsumers; i++ {
		wg.Add(1)
		go func(consumerID int) {
			defer wg.Done()
			<-startSignal

			var localCount uint64
			var localChecksum uint64

			for {
				if msg, ok := q.Dequeue(); ok {
					localCount++
					localChecksum += msg.Payload

					if atomic.AddUint64(&totalConsumed, 1) == TotalStressMessages {
						// Last message processed
					}
				} else {
					if atomic.LoadUint64(&totalConsumed) >= TotalStressMessages {
						break
					}
					runtime.Gosched()
				}
			}

			consumerCounts[consumerID] = localCount
			atomic.AddUint64(&checksumSum, localChecksum)
		}(i)
	}

	// Launch 1 Producer
	wg.Add(1)
	go func() {
		defer wg.Done()
		close(startSignal) // Trigger consumers

		for seq := uint64(1); seq <= TotalStressMessages; seq++ {
			msg := Message{Sequence: seq, Payload: seq}
			for !q.Enqueue(msg) {
				runtime.Gosched()
			}
		}
	}()

	wg.Wait()
	elapsed := time.Since(startTime)
	throughput := float64(TotalStressMessages) / elapsed.Seconds()

	expectedChecksum := (TotalStressMessages * (TotalStressMessages + 1)) / 2
	if checksumSum != expectedChecksum {
		t.Fatalf("DATA CORRUPTION: Expected checksum %d, got %d", expectedChecksum, checksumSum)
	}

	fmt.Printf("Processed %d messages in %v\n", TotalStressMessages, elapsed)
	fmt.Printf("Throughput: %.2f million msgs/sec\n", throughput/1e6)
	fmt.Printf("Checksum Validated: %d\n", checksumSum)
	for i, count := range consumerCounts {
		fmt.Printf("  Consumer %d handled %d messages (%.1f%%)\n", i, count, float64(count)/float64(TotalStressMessages)*100)
	}
}
```

### Profiling Hardware Cache Contention with Linux `perf`

To observe the real microarchitectural impact of false sharing and atomic bus locking during this stress test, run the Linux `perf` profiler:

```bash
# Measure CPU cycles, instructions, and L1 cache misses:
perf stat -e cycles,instructions,cache-misses,L1-dcache-load-misses,bus-cycles \
    go test -v -run Test100MMessageStress

# Advanced: Detect HITM (Hit Modified cache lines) indicating False Sharing:
# If head and tail are NOT padded, HITM percentage will surge to over 40% of all cache misses!
perf c2c record -- go test -v -run Test100MMessageStress
perf c2c report --stdio
```

```
=================================================
            Shared Data Cache Line Table          
=================================================
# Total  ----- LLC Load Hitm -----  Store    Data address
# Alloc  Total  RmtHitM  LclHitM    Refcnt   
# .....  .....  .......  .......    .......  ..................
   1     41829    28104    13725     100M    0x00007f9c80041040  <-- Unpadded Head/Tail!
```

---

## Day 3–4: Rust Port & Race Detection with Miri

In Days 3–4, your team will port the SPMC lock-free queue to Rust, prove its correctness with Miri, and observe how weak memory reorderings cause data races when memory barriers are intentionally weakened.

### The 3 Specific Rust Concepts You Will Fight

1. **Managing `UnsafeCell` Without Aliasing Violations:**  
   In Rust, obtaining a `&mut T` reference to a slot while multiple consumers inspect the queue violates Rust's fundamental aliasing rule: *aliasing XOR mutability*. You must store elements inside `Box<[UnsafeCell<MaybeUninit<T>>]>` and interact strictly via raw pointers (`*const T` / `*mut T`). Never convert a slot pointer into a `&mut T` while other threads could be executing loads.

2. **The CAS Loop: `compare_exchange_weak` vs. `compare_exchange`:**  
   On x86-64, both compile to `LOCK CMPXCHG`. But on ARM64, `compare_exchange` requires an inner loop to retry on spurious LL/SC failures. In a spin-wait loop, always use `compare_exchange_weak`. It permits spurious failures, resulting in tighter machine code on ARM64 architectures.

3. **Proving Memory Synchronization Edges:**  
   In an SPMC queue, the Consumer's CAS operation on `tail` must use `Ordering::AcqRel` on success:
   - **Acquire:** Synchronizes with the Producer's `Release` store to ensure the payload data is visible.
   - **Release:** Synchronizes with subsequent consumers, guaranteeing that the slot reservation is committed.
   - On failure, the CAS must use `Ordering::Acquire` or `Ordering::Relaxed` to reload the current `tail`.

### Rust SPMC Skeleton (`spmc_queue.rs`)

```rust
use std::cell::UnsafeCell;
use std::mem::MaybeUninit;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Instant;

#[repr(align(64))]
pub struct CachePadded<T>(pub T);

pub struct SpmcQueue<T> {
    buffer: Box<[UnsafeCell<MaybeUninit<T>>]>,
    mask: usize,
    head: CachePadded<AtomicUsize>,
    tail: CachePadded<AtomicUsize>,
}

unsafe impl<T: Send> Send for SpmcQueue<T> {}
unsafe impl<T: Send> Sync for SpmcQueue<T> {}

impl<T> SpmcQueue<T> {
    pub fn new(capacity: usize) -> Self {
        assert!(capacity >= 2 && capacity.is_power_of_two());
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
        let current_head = self.head.0.load(Ordering::Relaxed);
        let current_tail = self.tail.0.load(Ordering::Acquire);

        if current_head.wrapping_sub(current_tail) >= self.buffer.len() {
            return Err(item);
        }

        let slot_idx = current_head & self.mask;
        unsafe {
            (*self.buffer[slot_idx].get()).write(item);
        }

        // Publish head: payload write above must happen-before consumer reads it.
        self.head.0.store(current_head.wrapping_add(1), Ordering::Release);
        Ok(())
    }

    pub fn try_pop(&self) -> Option<T> {
        let mut current_tail = self.tail.0.load(Ordering::Acquire);

        loop {
            let current_head = self.head.0.load(Ordering::Acquire);
            if current_tail >= current_head {
                return None;
            }

            // Execute CAS to advance tail. AcqRel synchronizes with both Producer and Consumers.
            match self.tail.0.compare_exchange_weak(
                current_tail,
                current_tail.wrapping_add(1),
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => {
                    let slot_idx = current_tail & self.mask;
                    // Safety: Slot successfully reserved exclusively by this thread.
                    let item = unsafe {
                        (*self.buffer[slot_idx].get()).assume_init_read()
                    };
                    return Some(item);
                }
                Err(actual_tail) => {
                    current_tail = actual_tail;
                    std::hint::spin_loop();
                }
            }
        }
    }
}

// Multi-threaded 100M message stress harness in Rust
pub fn run_rust_spmc_stress(total_messages: usize, num_consumers: usize) {
    let queue = Arc::new(SpmcQueue::<u64>::new(65536));
    let start = Instant::now();
    let mut handles = Vec::new();

    for _ in 0..num_consumers {
        let q = Arc::clone(&queue);
        handles.push(thread::spawn(move || {
            let mut count = 0usize;
            let mut checksum = 0u64;
            while count < (total_messages / num_consumers) {
                if let Some(val) = q.try_pop() {
                    checksum += val;
                    count += 1;
                } else {
                    std::hint::spin_loop();
                }
            }
            (count, checksum)
        }));
    }

    let q_prod = Arc::clone(&queue);
    let prod_handle = thread::spawn(move || {
        for i in 1..=total_messages as u64 {
            let mut item = i;
            loop {
                match q_prod.try_push(item) {
                    Ok(()) => break,
                    Err(ret) => {
                        item = ret;
                        std::hint::spin_loop();
                    }
                }
            }
        }
    });

    prod_handle.join().unwrap();
    let mut total_checksum = 0u64;
    for h in handles {
        let (_, c) = h.join().unwrap();
        total_checksum += c;
    }
    println!("Completed Rust SPMC in {:?}", start.elapsed());
    println!("Total Checksum: {}", total_checksum);
}
```

### Verifying with Rust Miri

Miri executes Rust code by interpreting its Mid-level Intermediate Representation (MIR). It mathematically simulates the abstract machine, detecting undefined behavior, memory leaks, and weak memory violations regardless of the host hardware.

```bash
# Install Miri via rustup
rustup +nightly component add miri

# 1. Run Miri with weak memory emulation:
cargo +nightly miri test -- -Zmiri-weak-memory-emulation

# 2. Sabotage the implementation:
# In try_push, change Ordering::Release to Ordering::Relaxed.
# In try_pop, change Ordering::Acquire to Ordering::Relaxed.
# Re-run Miri:
cargo +nightly miri test

# EXPECTED RESULT:
# Miri halts with: "Data race detected between (1) Read on thread `<unnamed>` and (2) Write on thread `<unnamed>`"
# Miri proves that without Acquire-Release, the payload load can observe uninitialized memory.
```

---

## Friday Mob Review

Gather your engineering team. Compile your benchmark numbers and discuss the architectural realities of lock-free systems.

### Benchmark Results Table (100M Messages, 8 Cores)

| Configuration | Implementation | Throughput (Msgs/sec) | Avg Latency | CPU Utilization |
| :--- | :--- | :--- | :--- | :--- |
| **SPSC (1P : 1C)** | C# (.NET 8 Padded) | [ ] | [ ] | 200% (2 Cores) |
| **SPSC (1P : 1C)** | Go (1.22+ Padded) | [ ] | [ ] | 200% (2 Cores) |
| **SPSC (1P : 1C)** | Rust (Release/Acquire) | [ ] | [ ] | 200% (2 Cores) |
| **SPMC (1P : 7C)** | Go (`sync/atomic` CAS) | [ ] | [ ] | 800% (8 Cores) |
| **SPMC (1P : 7C)** | Rust (`AcqRel` CAS) | [ ] | [ ] | 800% (8 Cores) |
| **Unpadded SPSC** | Rust (False Sharing) | [ ] | [ ] | 200% (Degraded) |

---

### 7 In-Depth Technical Discussion Questions & Answers

#### 1. Why does removing cache line padding degrade throughput by 5x to 10x?
**Answer:** When `head` and `tail` occupy the same 64-byte cache line, modifying `head` triggers a MESI `BusRdX` invalidation across the processor interconnect. The core running the consumer has its L1 data cache line transitioned to `Invalid`. When the consumer updates `tail`, it invalidates the producer's cache line. The cache line constantly bounces back and forth across the ring bus/mesh fabric, saturating the interconnect and stalling CPU pipelines on L1 cache misses.

#### 2. What is the microarchitectural difference between `compare_exchange` and `compare_exchange_weak`?
**Answer:** On x86-64, both compile to `LOCK CMPXCHG`. However, on RISC architectures like ARM64 and RISC-V, atomic updates are implemented using Load-Linked / Store-Conditional (`LDXR`/`STXR`). A store-conditional can fail spuriously due to context switches, interrupts, or adjacent cache line snooping. `compare_exchange` wraps the operation in an internal loop to eliminate spurious failures. In a software spin-loop that already handles retries, `compare_exchange_weak` generates fewer instructions and avoids redundant nested loops.

#### 3. Why did the sabotaged `Relaxed` ring buffer pass on Intel x86-64 but crash on AWS Graviton3 (ARM64)?
**Answer:** The x86-64 architecture enforces Total Store Order (TSO) in hardware. Under TSO, stores are kept in FIFO order in the Store Buffer and loads are not reordered with loads. Consequently, x86 hardware provides Acquire semantics on every load and Release semantics on every store for free. ARM64 is weakly ordered; its out-of-order execution engine allows loads to be reordered past earlier loads, and stores past earlier stores. Without explicit Acquire/Release barriers, ARM64 hardware reads payload memory before the producer's store is retired.

#### 4. How does Rust's Miri detect weak memory concurrency bugs that hardware never triggers during testing?
**Answer:** Miri does not run compiled machine code on physical silicon. It interprets the compiler's Mid-level Intermediate Representation (MIR) against an abstract mathematical operational semantics. When weak memory emulation is enabled (`-Zmiri-weak-memory-emulation`), Miri maintains an explicit graph of happens-before synchronization edges. If a thread reads a memory location without a valid synchronization edge connecting it to the prior write, Miri flags a Data Race deterministically, regardless of host architecture.

#### 5. What physical CPU events occur when executing `Thread.SpinWait(1)` or `std::hint::spin_loop()`?
**Answer:** Both primitives emit the x86 `PAUSE` instruction (or ARM `YIELD`). On modern Intel/AMD processors, `PAUSE` introduces a small hardware pipeline delay (12 to 140 cycles). This prevents the CPU's speculative execution engine from mispredicting loop exits, which would trigger an expensive pipeline flush. Furthermore, in Simultaneous Multi-Threading (Hyper-Threading), `PAUSE` releases execution pipeline resources to the sibling logical thread sharing the physical core.

#### 6. Why does Go mandate Sequential Consistency for all `sync/atomic` primitives instead of exposing Acquire/Release?
**Answer:** Go prioritizes language simplicity and safety over micro-optimizations. The Go design team concluded that fine-grained memory orderings (Acquire, Release, Relaxed) are widely misunderstood and cause subtle, catastrophic concurrency bugs that are nearly impossible to debug. By forcing all atomic operations to `SeqCst`, Go guarantees that any program that is sequentially correct will execute safely across all CPU architectures without weak-memory surprises.

#### 7. Why must a consumer in an SPMC queue claim the `tail` index via CAS before reading the slot payload?
**Answer:** In an SPMC queue, multiple consumers are racing for the same messages. If Consumer A reads the slot payload *before* successfully claiming the tail index via CAS, Consumer B could claim the slot, read the payload, and return it to the Producer. The Producer could then overwrite that slot with a new message before Consumer A's CAS completes, resulting in Consumer A reading corrupted or duplicated data. The atomic CAS on `tail` grants exclusive ownership of that slot.

---

## Sign-Off Checklist

Every team member must be able to answer "Yes" to all 7 criteria before lab sign-off:

- [ ] 1. I can explain what a Store Buffer and Invalidate Queue are and why they cause cores to observe memory writes out of order.
- [ ] 2. I can identify False Sharing with a profiler and eliminate it using 64-byte (or 128-byte) cache-line padding.
- [ ] 3. I understand why an atomic `Release` store must be paired with an atomic `Acquire` load to create a valid happens-before edge.
- [ ] 4. I know why `Thread.SpinWait` and `std::hint::spin_loop` emit the `PAUSE` instruction and why raw `while(true) {}` harms hyperthreaded cores.
- [ ] 5. I can run `cargo miri test` with weak memory emulation to verify concurrent Rust data structures.
- [ ] 6. I understand why x86-64 hides concurrency bugs that cause catastrophic crashes on ARM64 Graviton instances.
- [ ] 7. I understand the exact microarchitectural tradeoff Go makes by enforcing Sequential Consistency across all `sync/atomic` operations.

---

## Stretch Goals for Fast Learners

1. **Dmitry Vyukov's Bounded MPMC Queue:**  
   Upgrade your SPMC ring buffer to a full Multi-Producer Multi-Consumer (MPMC) bounded queue. Implement per-slot sequence numbers (`sequence: AtomicUsize`) that alternate between indicating slot-empty and slot-ready states, eliminating consumer-producer contention:
   ```rust
   struct Cell<T> {
       sequence: AtomicUsize,
       value: UnsafeCell<MaybeUninit<T>>,
   }
   // Push: wait until cell.sequence == pos, then CAS cell.sequence to pos + 1
   // Pop: wait until cell.sequence == pos + 1, then CAS cell.sequence to pos + mask + 1
   ```

2. **Assembly Disassembly Inspection:**  
   Use `cargo asm` (or `objdump -d` / `dotnet-dump`) to inspect the generated machine instructions for your Rust, Go, and C# atomic stores. Compare the instructions generated for x86-64 (`MOV` vs `LOCK CMPXCHG`) against ARM64 (`LDAR` / `STLR` vs `LDXR` / `STXR`).
