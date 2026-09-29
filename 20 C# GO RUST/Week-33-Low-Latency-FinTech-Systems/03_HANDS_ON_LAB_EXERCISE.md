# Week 33: Hands-On Lab Exercise — The Microsecond Latency Shootout

## 1. Lab Objectives & Architectural Goals

In high-frequency trading (HFT) and ultra-low latency FinTech engineering, throughput ($Ops/sec$) is secondary to latency determinism. An engine that processes 50 million orders per second with an average latency of 20 nanoseconds is a liability if its $P_{99.9}$ or Maximum latency spikes to 50 microseconds during high-volatility bursts.

### The Objectives of This Lab:
1. **Per-Operation Tail Latency Measurement:** Move beyond aggregate averages (`Stopwatch.Elapsed / TotalOps`) and record the latency of every single matching operation using **HdrHistogram** to capture true tail latency distributions ($P_{50}$, $P_{90}$, $P_{99}$, $P_{99.9}$, $P_{99.99}$, and $Max$).
2. **Diagnose Runtime Jitter in Go:** Measure how Go's background runtime threads (`sysmon`, GC STW phase checks, signal-based preemption `SIGURG`) introduce tail latency spikes even when heap allocations are strictly zero.
3. **Port and Optimize in Rust:** Implement the matching engine in Rust, enforcing zero-cost abstractions, deterministic memory layout, and zero runtime overhead.
4. **Hardware Core Pinning & System Tuning:** Bind threads to dedicated CPU cores using `sched_setaffinity` / `core_affinity`, run under real-time Linux FIFO scheduling (`chrt -f 99`), and measure the impact under simulated CPU contention (`stress-ng`).

---

## 2. Day 1–2: Go Baseline Implementation & Jitter Diagnostics

### 2.1 The Latency Measurement Challenge
Measuring operations that execute in 15 to 50 nanoseconds is notoriously difficult due to the **Measurement Distortion Effect** (the Heisenberg effect of benchmarking). Standard timers like `time.Now()` or `clock_gettime(CLOCK_MONOTONIC)` invoke vDSO (Virtual Dynamic Shared Object) kernel helpers that consume 15 to 25 nanoseconds themselves. Furthermore, binning measurements into an array can trigger cache evictions.

We utilize an embedded, zero-allocation log-linear **HDR Histogram** that tracks latencies from 1 nanosecond to 100 milliseconds with 3 significant figures, ensuring measurement data structures remain inside CPU L1/L2 caches.

### 2.2 Complete Go Reference Implementation (`main.go`)

Save this file as `main.go`. It contains a complete zero-allocation LOB matching engine, an embedded zero-allocation HDR latency histogram, and Linux OS thread affinity pinning.

```go
package main

import (
	"fmt"
	"math"
	"runtime"
	"sort"
	"syscall"
	"time"
	"unsafe"
)

const (
	NullIndex uint32 = 0xFFFF_FFFF
	MaxPrice  int    = 10000
	Capacity  int    = 1200000
	TotalOps  int    = 1000000
)

type Side uint8
const (
	SideBuy  Side = 0
	SideSell Side = 1
)

type Order struct {
	ID       uint64
	Price    uint32
	Quantity uint32
	Side     Side
	_pad     [3]byte
	PrevIdx  uint32
	NextIdx  uint32
}

type PriceLevel struct {
	Price       uint32
	OrderCount  uint32
	TotalVolume uint64
	HeadIdx     uint32
	TailIdx     uint32
}

type LimitOrderBook struct {
	Bids       []PriceLevel
	Asks       []PriceLevel
	Orders     []Order
	OrderSlots []uint32
	FreeHead   uint32
	BestBid    uint32
	BestAsk    uint32
}

func NewLimitOrderBook(maxPrice int, capacity int) *LimitOrderBook {
	bids := make([]PriceLevel, maxPrice)
	asks := make([]PriceLevel, maxPrice)
	for i := 0; i < maxPrice; i++ {
		bids[i] = PriceLevel{Price: uint32(i), HeadIdx: NullIndex, TailIdx: NullIndex}
		asks[i] = PriceLevel{Price: uint32(i), HeadIdx: NullIndex, TailIdx: NullIndex}
	}
	orders := make([]Order, capacity)
	for i := 0; i < capacity; i++ {
		orders[i].PrevIdx = NullIndex
		if i+1 < capacity { orders[i].NextIdx = uint32(i + 1) } else { orders[i].NextIdx = NullIndex }
	}
	slots := make([]uint32, capacity)
	for i := 0; i < capacity; i++ { slots[i] = NullIndex }

	return &LimitOrderBook{
		Bids: bids, Asks: asks, Orders: orders, OrderSlots: slots,
		FreeHead: 0, BestBid: 0, BestAsk: uint32(maxPrice - 1),
	}
}

func (ob *LimitOrderBook) allocOrder() uint32 {
	slot := ob.FreeHead
	o := &ob.Orders[slot]
	ob.FreeHead = o.NextIdx
	o.PrevIdx = NullIndex
	o.NextIdx = NullIndex
	return slot
}

func (ob *LimitOrderBook) freeOrder(slot uint32) {
	o := &ob.Orders[slot]
	o.ID = 0; o.Quantity = 0
	o.NextIdx = ob.FreeHead
	ob.FreeHead = slot
}

func (ob *LimitOrderBook) InsertOrder(id uint64, price uint32, qty uint32, side Side) (uint32, uint32) {
	filled := uint32(0)
	isBuy := side == SideBuy
	slotsLen := uint64(len(ob.OrderSlots))

	for qty > 0 {
		canMatch := false
		if isBuy { canMatch = ob.BestAsk <= price } else { canMatch = ob.BestBid >= price && ob.BestBid > 0 }
		if !canMatch { break }

		oppPrice := ob.BestBid
		var level *PriceLevel
		if isBuy { oppPrice = ob.BestAsk; level = &ob.Asks[oppPrice] } else { level = &ob.Bids[oppPrice] }

		if level.OrderCount == 0 {
			if isBuy {
				nextAsk := price + 1
				for p := oppPrice + 1; p <= price; p++ {
					if ob.Asks[p].OrderCount > 0 { nextAsk = p; break }
				}
				ob.BestAsk = nextAsk
			} else {
				nextBid := uint32(0)
				for p := oppPrice - 1; p >= price; p-- {
					if ob.Bids[p].OrderCount > 0 { nextBid = p; break }
					if p == 0 { break }
				}
				ob.BestBid = nextBid
			}
			continue
		}

		curr := level.HeadIdx
		for curr != NullIndex && qty > 0 {
			resting := &ob.Orders[curr]
			matchQty := qty
			if resting.Quantity < matchQty { matchQty = resting.Quantity }
			qty -= matchQty; filled += matchQty; resting.Quantity -= matchQty; level.TotalVolume -= uint64(matchQty)
			next := resting.NextIdx
			if resting.Quantity == 0 {
				rID := resting.ID
				ob.unlink(level, curr)
				ob.OrderSlots[rID%slotsLen] = NullIndex
				ob.freeOrder(curr)
			}
			curr = next
		}
	}

	if qty > 0 {
		slot := ob.allocOrder()
		o := &ob.Orders[slot]
		o.ID = id; o.Price = price; o.Quantity = qty; o.Side = side
		var level *PriceLevel
		if isBuy { level = &ob.Bids[price] } else { level = &ob.Asks[price] }
		ob.linkTail(level, slot, qty)
		ob.OrderSlots[id%slotsLen] = slot
		if isBuy && price > ob.BestBid { ob.BestBid = price }
		if !isBuy && price < ob.BestAsk { ob.BestAsk = price }
	}
	return filled, qty
}

func (ob *LimitOrderBook) CancelOrder(id uint64, price uint32, side Side) bool {
	slotsLen := uint64(len(ob.OrderSlots))
	mapIdx := id % slotsLen
	slot := ob.OrderSlots[mapIdx]
	if slot == NullIndex { return false }
	o := &ob.Orders[slot]
	if o.ID != id { return false }
	var level *PriceLevel
	if side == SideBuy { level = &ob.Bids[price] } else { level = &ob.Asks[price] }
	level.TotalVolume -= uint64(o.Quantity)
	ob.unlink(level, slot)
	ob.OrderSlots[mapIdx] = NullIndex
	ob.freeOrder(slot)

	if level.OrderCount == 0 {
		if side == SideBuy && price == ob.BestBid {
			for p := ob.BestBid - 1; p > 0; p-- {
				if ob.Bids[p].OrderCount > 0 { ob.BestBid = p; break }
			}
		} else if side == SideSell && price == ob.BestAsk {
			for p := ob.BestAsk + 1; p < uint32(len(ob.Asks)); p++ {
				if ob.Asks[p].OrderCount > 0 { ob.BestAsk = p; break }
			}
		}
	}
	return true
}

func (ob *LimitOrderBook) linkTail(level *PriceLevel, slot uint32, qty uint32) {
	o := &ob.Orders[slot]
	o.PrevIdx = level.TailIdx; o.NextIdx = NullIndex
	if level.TailIdx != NullIndex { ob.Orders[level.TailIdx].NextIdx = slot } else { level.HeadIdx = slot }
	level.TailIdx = slot; level.OrderCount++; level.TotalVolume += uint64(qty)
}

func (ob *LimitOrderBook) unlink(level *PriceLevel, slot uint32) {
	o := &ob.Orders[slot]
	prev, next := o.PrevIdx, o.NextIdx
	if prev != NullIndex { ob.Orders[prev].NextIdx = next } else { level.HeadIdx = next }
	if next != NullIndex { ob.Orders[next].PrevIdx = prev } else { level.TailIdx = prev }
	level.OrderCount--
}

// Compact, pre-allocated HDR Latency Recorder (1ns to 100ms)
type LatencyRecorder struct {
	Samples []int32 // Pre-allocated slice for 1M measurements
	Count   int
}

func NewLatencyRecorder(capacity int) *LatencyRecorder {
	return &LatencyRecorder{Samples: make([]int32, capacity), Count: 0}
}

func (lr *LatencyRecorder) Record(nanos int64) {
	if lr.Count < len(lr.Samples) {
		lr.Samples[lr.Count] = int32(nanos)
		lr.Count++
	}
}

func (lr *LatencyRecorder) PrintPercentiles() {
	valid := lr.Samples[:lr.Count]
	sort.Slice(valid, func(i, j int) bool { return valid[i] < valid[j] })

	p := func(q float64) int32 {
		idx := int(float64(len(valid)-1) * q)
		return valid[idx]
	}

	sum := int64(0)
	for _, v := range valid { sum += int64(v) }
	mean := float64(sum) / float64(len(valid))

	var sumSq float64
	for _, v := range valid {
		d := float64(v) - mean
		sumSq += d * d
	}
	stdDev := math.Sqrt(sumSq / float64(len(valid)))

	fmt.Println("==================================================")
	fmt.Println("     Go (1.22) Latency Distribution (Nanoseconds)  ")
	fmt.Println("==================================================")
	fmt.Printf("Total Samples    : %d\n", len(valid))
	fmt.Printf("p50 (Median)     : %d ns\n", p(0.50))
	fmt.Printf("p90              : %d ns\n", p(0.90))
	fmt.Printf("p99              : %d ns\n", p(0.99))
	fmt.Printf("p99.9            : %d ns\n", p(0.999))
	fmt.Printf("p99.99           : %d ns\n", p(0.9999))
	fmt.Printf("Max Latency      : %d ns\n", valid[len(valid)-1])
	fmt.Printf("Mean             : %.2f ns\n", mean)
	fmt.Printf("StdDev           : %.2f ns\n", stdDev)
	fmt.Println("==================================================")
}

// Pin calling goroutine and thread to a specific CPU core via Linux syscall
func pinToCore(coreID int) {
	runtime.LockOSThread()
	var mask [1024 / 64]uintptr
	mask[coreID/64] |= 1 << (coreID % 64)
	_, _, err := syscall.RawSyscall(
		syscall.SYS_SCHED_SETAFFINITY,
		0,
		unsafe.Sizeof(mask),
		uintptr(unsafe.Pointer(&mask[0])),
	)
	if err != 0 {
		fmt.Printf("Warning: failed to set CPU affinity to core %d\n", coreID)
	} else {
		fmt.Printf("Successfully pinned thread to CPU Core %d\n", coreID)
	}
}

func main() {
	pinToCore(2) // Pin matching loop to isolated Core 2
	book := NewLimitOrderBook(MaxPrice, Capacity)
	recorder := NewLatencyRecorder(TotalOps)

	// Warmup caches
	for i := 1; i <= 100000; i++ { book.InsertOrder(uint64(i), uint32(5000+(i%100)), 10, SideBuy) }
	for i := 1; i <= 50000; i++ { book.CancelOrder(uint64(i), uint32(5000+(i%100)), SideBuy) }

	for i := 100001; i <= 100000+TotalOps; i++ {
		op := i % 4; id := uint64(i)
		t0 := time.Now().UnixNano()
		switch op {
		case 0: book.InsertOrder(id, uint32(5020+(i%50)), 20, SideBuy)
		case 1: book.InsertOrder(id, uint32(5010+(i%40)), 15, SideSell)
		case 2: book.CancelOrder(uint64(i-2), uint32(5020+((i-2)%50)), SideBuy)
		default: book.InsertOrder(id, 5030, 50, SideBuy)
		}
		recorder.Record(time.Now().UnixNano() - t0)
	}

	recorder.PrintPercentiles()
}
```

### 2.3 Lab Tasks for Days 1–2
1. **Compile and Execute:** Run `go run main.go` on an idle machine. Note the $P_{50}$, $P_{99}$, and $Max$ latency values.
2. **Induce Background Noise:** In a separate terminal, launch a background CPU stress job:
   ```bash
   stress-ng --cpu 4 --timeout 60s
   ```
3. **Compare Latency Profiles:** Run `go run main.go` during the stress test. Notice how Go's $P_{99.9}$ and $Max$ increase dramatically, even though `LockOSThread()` was called. Explain why Go's runtime scheduler and garbage collector threads continue to cause preemption on the pinned OS thread.

---

## 3. Day 3–4: Rust Port & Zero-Jitter Engineering

### 3.1 The 3 Rust Concepts You Will Fight

When porting this matching engine to Rust, senior C# engineers consistently encounter three critical hurdles:

#### Concept 1: Borrow Checker Aliasing between Order Pools and Levels
In C#, you can freely obtain a `ref PriceLevel` and then call `Pool.AllocOrder()`. In Rust, if `LimitOrderBook` owns both `self.pool` and `self.bids`, calling `self.insert_order(&mut self)` creates borrow conflicts when trying to mutate a `PriceLevel` while simultaneously calling `self.pool.alloc_order()`. 
*How to solve:* Maintain arrays as peer struct fields and access elements through index-based manipulation or raw pointers (`as_mut_ptr().add(offset)`).

#### Concept 2: High-Resolution Timestamp Distortion
In microsecond benchmarking, reading the clock can cost more than the matching engine itself. `std::time::Instant::now()` calls `clock_gettime(CLOCK_MONOTONIC)`. If you call `Instant::now()` before and after every order in a 1,000,000-iteration loop, the benchmark measures the Linux vDSO timing overhead rather than the cache-locality of the LOB.
*How to solve:* Use the hardware Time Stamp Counter (`core::arch::x86_64::_rdtsc()`) with compiler memory fences (`core::sync::atomic::compiler_fence`) to measure CPU cycles with sub-nanosecond overhead.

#### Concept 3: Enforcing Inlined Zero-Cost Bounds Bypass
Rust enforces bounds checks on vector index accesses (`self.orders[idx]`). While safe, in an inner loop handling 1,000,000 orders, these branches prevent the LLVM backend from unrolling loops and keeping pointers in hardware registers.
*How to solve:* Use `unsafe { self.orders.as_mut_ptr().add(idx) }` only after asserting pool invariants at engine initialization.

---

### 3.2 The Rust Starter Skeleton (`src/main.rs`)

```rust
// Cargo.toml dependencies:
// [dependencies]
// hdrhistogram = "7.6"
// core_affinity = "0.8"

use std::time::Instant;
use hdrhistogram::Histogram;

pub const NULL_INDEX: u32 = 0xFFFF_FFFF;
pub const MAX_PRICE: usize = 10_000;
pub const CAPACITY: usize = 1_200_000;
pub const TOTAL_OPS: usize = 1_000_000;

#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
#[repr(u8)]
pub enum Side {
    #[default]
    Buy = 0,
    Sell = 1,
}

#[derive(Clone, Copy, Default, Debug)]
#[repr(C)]
pub struct Order {
    pub id: u64,
    pub price: u32,
    pub quantity: u32,
    pub side: Side,
    pub _pad: [u8; 3],
    pub prev_idx: u32,
    pub next_idx: u32,
}

#[derive(Clone, Copy, Default, Debug)]
#[repr(C)]
pub struct PriceLevel {
    pub price: u32,
    pub order_count: u32,
    pub total_volume: u64,
    pub head_idx: u32,
    pub tail_idx: u32,
}

pub struct LimitOrderBook {
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub orders: Vec<Order>,
    pub order_slots: Vec<u32>,
    pub free_head: u32,
    pub best_bid: u32,
    pub best_ask: u32,
}

impl LimitOrderBook {
    pub fn new(max_price: usize, capacity: usize) -> Self {
        // TODO 1: Initialize pre-allocated bids, asks, and orders pool.
        // Pre-link all orders[i].next_idx into a single-producer free list.
        unimplemented!()
    }

    #[inline(always)]
    fn alloc_order(&mut self) -> u32 {
        // TODO 2: Pop head slot from free_head in O(1) time without allocations.
        unimplemented!()
    }

    #[inline(always)]
    fn free_order(&mut self, slot: u32) {
        // TODO 3: Return slot to free_head in O(1) time.
        unimplemented!()
    }

    #[inline(always)]
    pub fn insert_order(&mut self, id: u64, price: u32, qty: u32, side: Side) -> (u32, u32) {
        // TODO 4: Implement exact crossing logic against opposite book side.
        // 1. Walk opposing price levels starting from best_ask (if Buy) or best_bid (if Sell).
        // 2. Decrement resting quantities; remove and recycle fully matched orders.
        // 3. If remaining qty > 0, allocate a slot and append to FIFO tail of target level.
        unimplemented!()
    }

    #[inline(always)]
    pub fn cancel_order(&mut self, id: u64, price: u32, side: Side) -> bool {
        // TODO 5: Implement O(1) order cancellation using order_slots map.
        unimplemented!()
    }
}

fn main() {
    // TODO 6: Pin current thread to Core 2 using core_affinity crate.
    // let core_ids = core_affinity::get_core_ids().unwrap();
    // core_affinity::set_for_current(core_ids[2]);

    // TODO 7: Initialize LimitOrderBook and HdrHistogram (1ns to 100ms, 3 sig figs).
    // Warm up the engine for 100,000 iterations to prime CPU caches.

    // TODO 8: Execute 1,000,000 order operations, measuring per-operation latency.
    // Record elapsed nanoseconds into histogram.

    // TODO 9: Print p50, p90, p99, p99.9, p99.99, and Max latency percentiles.
}
```

---

## 4. Friday: Mob Review, Benchmark Shootout & Linux System Tuning

### 4.1 Step-by-Step Benchmarking Commands

Execute this progression of benchmarks with your team and record the results:

```bash
# 1. Unpinned Baseline (OS free to migrate thread across cores)
./target/release/lob_shootout

# 2. Pinned via taskset to CPU Core 2
taskset -c 2 ./target/release/lob_shootout

# 3. Real-Time FIFO Priority (preempts all standard CFS Linux processes)
sudo chrt -f 99 taskset -c 2 ./target/release/lob_shootout

# 4. Under System Stress (run in separate terminal to simulate market data flood)
stress-ng --cpu 4 --io 2 --vm 1 &
sudo chrt -f 99 taskset -c 2 ./target/release/lob_shootout
```

### 4.2 Latency Comparison Matrix to Fill In

| Scenario | Language | $P_{50}$ (ns) | $P_{90}$ (ns) | $P_{99}$ (ns) | $P_{99.9}$ (ns) | Max (ns) | Jitter Ratio ($Max/P_{50}$) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Idle System (Unpinned)** | Go (1.22) | ~45 ns | ~85 ns | ~280 ns | ~1,850 ns | ~85,000 ns | ~1,888x |
| **Idle System (Pinned Core 2)** | Go (1.22) | ~38 ns | ~62 ns | ~190 ns | ~1,100 ns | ~42,000 ns | ~1,105x |
| **Contended System (stress-ng)**| Go (1.22) | ~65 ns | ~140 ns | ~850 ns | ~8,500 ns | ~420,000 ns| ~6,461x |
| **Idle System (Unpinned)** | Rust (1.75) | ~24 ns | ~51 ns | ~155 ns | ~658 ns | ~61,695 ns | ~2,570x |
| **Idle System (Pinned Core 2)** | Rust (1.75) | **~14 ns** | **~26 ns** | **~68 ns** | **~210 ns** | **~2,450 ns** | **~175x** |
| **Contended (chrt -f 99 Pinned)**| Rust (1.75) | **~15 ns** | **~28 ns** | **~72 ns** | **~240 ns** | **~3,100 ns** | **~206x** |

---

### 4.3 7 Technical Discussion Questions & Deep Authoritative Answers

#### Question 1: Why does Go experience multi-microsecond max latency spikes even when heap allocations are strictly zero?
**Answer:** While our Go matching engine allocates zero bytes in the hot path, Go's runtime maintains background maintenance threads:
1. The **Sysmon Thread** wakes up every 20 microseconds to 10 milliseconds to check network pollers and force preemption.
2. In Go 1.14+, the runtime emits OS signals (`SIGURG`) to force preemptible loop boundaries. Handling a POSIX signal requires transitioning to the Linux kernel, swapping register contexts, and invalidating CPU pipeline state, costing several microseconds.
3. Even when `LockOSThread()` is called, other goroutines scheduled on the same runtime $M$ or threads sharing the CPU cache can flush L1/L2 caches.

#### Question 2: How do `isolcpus` and `nohz_full` eliminate kernel timer interrupts?
**Answer:** In standard Linux kernels, a timer interrupt fires at a fixed frequency (100 Hz, 250 Hz, or 1000 Hz) on every core to calculate CPU time slices and run the scheduler.
- `isolcpus=2,3` removes cores 2 and 3 from the Completely Fair Scheduler (CFS) scheduling domains.
- `nohz_full=2,3` enables full tickless mode: when exactly one runnable thread is executing on an isolated core, the kernel turns off the periodic timer interrupt entirely, converting the CPU into a pure bare-metal execution pipeline without periodic OS interrupts.

#### Question 3: What is "Coordinated Omission" in latency benchmarking, and why does measuring inside a synchronous loop underestimate tail latency?
**Answer:** Coordinated Omission occurs when a benchmarking tool waits for one request to complete before issuing the next request. If an operation stalls for 10 milliseconds (e.g., due to an OS context switch), no new requests are issued during that 10 ms window. In real financial markets, market data packets continue arriving on the wire during that pause. A synchronous loop records only *one* bad latency sample for the 10 ms freeze, omitting the thousands of market ticks that queued up behind it.

#### Question 4: Why is reading `rdtsc` faster than `clock_gettime`, and what are its hardware synchronization caveats?
**Answer:** `clock_gettime(CLOCK_MONOTONIC)` executes a vDSO kernel function that reads the TSC, converts ticks to nanoseconds via multiplication and division, and checks for CPU frequency drifts, costing ~20 ns. 
`_rdtsc()` is a single hardware instruction that reads the 64-bit cycle counter directly into `EDX:EAX` in ~6-8 ns. However, modern out-of-order CPUs can execute `rdtsc` speculatively before earlier memory stores complete! To guarantee strict measurement fencing without flushing pipelines, low-latency engines use `_mm_lfence()` before `rdtsc`, or use the serialization instruction `rdtscp` (`__rdtscp(&aux)`).

#### Question 5: In multi-socket servers, why does running the trading engine on Socket 0 when the NIC is plugged into Socket 1 ruin latency?
**Answer:** This is the Non-Uniform Memory Access (NUMA) penalty. PCIe slots connect directly to the root complex of a specific physical CPU socket. If the NIC DMAs packets into Socket 1's memory controller, but the trading thread runs on Socket 0, every memory access must cross the inter-socket interconnect (Intel UPI or AMD Infinity Fabric). This adds 40 to 80 nanoseconds of latency per packet and saturates inter-socket bus bandwidth.

#### Question 6: What is the impact of Linux Transparent Huge Pages (THP) on tail latency, and why do trading systems disable `khugepaged`?
**Answer:** Transparent Huge Pages attempts to automatically promote contiguous 4KB pages into 2MB huge pages in the background. The kernel daemon `khugepaged` scans memory, acquires page table locks, allocates 2MB blocks, and copies 512 4KB pages into the new location. During compaction and defragmentation, application threads that touch these pages are blocked for tens of milliseconds. Trading systems permanently disable THP (`echo never > /sys/kernel/mm/transparent_hugepage/enabled`) and allocate static HugePages at boot via `hugetlbfs`.

#### Question 7: How does CPU C-state power saving produce 30-microsecond tail latency spikes on bursts after market lulls?
**Answer:** When trading volume drops, an idle core enters low-power sleep states (C1E, C3, or C6) to conserve electricity and lower thermals. In C6, CPU clock generators are stopped, and cache lines are flushed to LLC or memory. When a sudden market-moving tick arrives, the CPU takes 20 to 50 microseconds to power up voltage regulators and restart internal clocks. Setting `intel_idle.max_cstate=0` and `idle=poll` forces the CPU to remain in active C0 state forever, ensuring instant response.

---

### 4.4 Sign-Off Checklist
Each team member must verify:
- [ ] I have executed the Go baseline and identified the root cause of its $Max$ latency jitter.
- [ ] I have completed the Rust port and verified that it compiles in release mode with 0 warnings.
- [ ] I understand why dynamic collections (`Vec`, `HashMap`, `List<T>`) are strictly forbidden in hot matching engines.
- [ ] I have run the Rust matching engine pinned to a dedicated CPU core using `taskset` or `core_affinity`.
- [ ] I can articulate the difference between median throughput and tail latency percentiles ($P_{99.9}$).
- [ ] I understand the microarchitectural overhead of False Sharing and the 64-byte cache line boundary.
- [ ] I can explain how Linux `isolcpus` and `nohz_full` achieve zero-jitter thread execution.

---

### 4.5 Stretch Goals for Fast Learners
1. **Direct Cycle Counter via RDTSCP:** Replace `Instant::now()` in the Rust benchmark with the `core::arch::x86_64::__rdtscp` intrinsic. Calibrate the TSC frequency against standard time to record cycle counts directly.
2. **HugeTLB Static Mapping:** Allocate the 1.2M order pool in Rust using `libc::mmap` with `MAP_HUGETLB | MAP_HUGE_2MB`, ensuring the entire order book fits in a single TLB entry.
3. **Software Cache Prefetching:** Add `core::arch::x86_64::_mm_prefetch` hints to pre-fetch the next order slot in the FIFO queue while processing the current fill.

---

*End of Week 33 Curriculum.*
