# Week 26: Hands-On Lab — The Starvation & Reactor Stall Hunt

## Lab Overview & Systems Scenario

In modern microservices and high-throughput network engines, asynchronous runtimes are tasked with balancing two fundamentally opposing categories of work:
1. **Latency-Critical Network I/O**: Operations such as handling HTTP health-check probes, proxying TCP streams, maintaining real-time WebSocket heartbeats, and processing TLS handshakes. These tasks require microsecond-level dispatch latency to avoid cascading gateway timeouts.
2. **Compute-Intensive Payloads**: Operations such as validating cryptographic JSON Web Tokens (JWTs), decrypting payloads, compressing HTTP responses with Brotli/Gzip, and parsing complex multi-megabyte JSON or Protobuf bodies.

When an engineer inadvertently executes an uninterrupted, CPU-bound calculation or an uncooperative synchronous function inside an asynchronous execution context, the underlying worker thread is monopolized. Other asynchronous tasks co-located on that thread are completely starved of CPU cycles. 

In production, this architectural defect is known as a **Reactor Stall** (or **Worker Starvation**). Unlike memory leaks or crashes, reactor stalls do not produce stack traces or obvious crash dumps; instead, they manifest as:
- Random P99 and P99.9 latency spikes under seemingly moderate load.
- Kubernetes liveness/readiness probes timing out, causing container restarts.
- Upstream load balancers dropping connections and reporting `504 Gateway Timeout`.
- Epoll event queues filling up in the kernel, resulting in packet drops.

In this intensive team lab, you will reproduce, measure, diagnose, and resolve worker starvation in both **Rust (Tokio)** and **Go (G-M-P runtime)**. You will discover why Tokio's cooperative model requires explicit concurrency boundaries, how custom instrumentation and `tokio-console` expose reactor bottlenecks, and how Go's runtime uses operating system signals (`SIGURG`) to forcibly preempt stubborn loops.

---

## Lab Timeline Structure

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              WEEKLY LAB TIMELINE                            │
├──────────────────────────┬──────────────────────────┬───────────────────────┤
│ Days 1–2: Rust (Tokio)   │ Days 3–4: Go (G-M-P)     │ Friday: Mob Review    │
├──────────────────────────┼──────────────────────────┼───────────────────────┤
│ • Reproduce Tokio stall  │ • Run Go CPU loop        │ • Benchmark metrics   │
│ • Inspect tokio-console  │ • Trace GODEBUG sched    │ • 7 Architectural Q&A │
│ • Implement yield_now()  │ • Disable async preemption│ • 7-Point sign-off   │
│ • Implement spawn_blocking│ • Analyze SIGURG signals│ • Stretch challenges  │
└──────────────────────────┴──────────────────────────┴───────────────────────┘
```

---

## Days 1–2: Tokio Reactor Stall & Worker Starvation (Rust)

### The Mechanics of Starvation in Tokio

Tokio's multi-threaded runtime (`rt-multi-thread`) maintains a fixed pool of OS worker threads, typically equal to the number of physical CPU cores. Each worker thread runs an event loop that pulls tasks from a local 256-element lock-free work-stealing queue. If a worker's local queue is empty, it attempts to steal tasks from other workers or check the global injection queue.

Crucially, **Tokio is a purely cooperative scheduler**. A worker thread cannot interrupt running machine instructions. It switches tasks only when the executing future voluntarily relinquishes the thread by returning `Poll::Pending` at an `.await` boundary. If an asynchronous task runs a computational loop without hitting an `.await` point, the worker thread remains locked inside that future's `poll()` method. Consequently:
- The worker thread cannot poll other tasks on its local deque.
- The worker thread cannot steal tasks from saturated peers.
- The worker thread cannot process pending timer expirations or epoll I/O readiness events registered on its local driver.

### Rust Starter Project Setup (`tokio_starvation_lab`)

#### Project File: `Cargo.toml`

```toml
[package]
name = "tokio_starvation_lab"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1.38", features = ["full", "tracing"] }
console-subscriber = "0.3"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
```

#### Production-Grade Starter Code: `src/main.rs`

```rust
use std::env;
use std::time::{Duration, Instant};
use tokio::time::sleep;

// Latency-sensitive task: Simulates an edge gateway health check / heartbeat.
// Must complete every 50ms. If jitter exceeds 20ms (total > 70ms), it reports a reactor stall!
async fn heartbeat_monitor() {
    let mut interval_ticker = tokio::time::interval(Duration::from_millis(50));
    let mut last_tick = Instant::now();

    for tick in 1..=20 {
        interval_ticker.tick().await;
        let elapsed = last_tick.elapsed();
        last_tick = Instant::now();

        if elapsed > Duration::from_millis(70) {
            eprintln!(
                "🚨 [HEARTBEAT STALL] Tick {}: Expected 50ms, took {:?} (JITTER: +{:?})",
                tick, elapsed, elapsed - Duration::from_millis(50)
            );
        } else {
            println!("✅ [Heartbeat OK] Tick {}: {:?}", tick, elapsed);
        }
    }
}

// CPU-intensive task: Simulates cryptographic hash calculation or intensive JSON parsing.
fn heavy_computation(iterations: u64) -> u64 {
    let mut sum: u64 = 0;
    for i in 0..iterations {
        sum = sum.wrapping_add(i.rotate_left(3) ^ 0x5555_5555_AAAA_AAAA);
    }
    sum
}

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    let args: Vec<String> = env::args().collect();
    let mode = if args.len() > 1 { args[1].as_str() } else { "unmitigated" };

    println!("=== Starting Tokio Starvation Lab ===");
    println!("Runtime: 2 Worker Threads | Mode: {}\n", mode);

    // Spawn the latency-sensitive heartbeat monitor
    let heartbeat_handle = tokio::spawn(heartbeat_monitor());

    // Allow heartbeat to establish baseline ticks
    sleep(Duration::from_millis(150)).await;

    let compute_handle = match mode {
        "unmitigated" => {
            // UNMITIGATED MODE: Demonstrates catastrophic worker monopolization.
            // Runs a synchronous CPU loop directly inside the async task!
            tokio::spawn(async {
                println!("🔥 [Unmitigated] Monopolizing Tokio worker thread...");
                let start = Instant::now();
                let result = heavy_computation(600_000_000);
                println!("🔥 [Unmitigated] Completed in {:?}, result: {}", start.elapsed(), result);
                result
            })
        }
        "yield-now" => {
            // FIX OPTION B: Chunked cooperative yielding using tokio::task::yield_now()
            tokio::spawn(async {
                println!("🛡️ [yield_now] Running chunked cooperative compute...");
                let start = Instant::now();
                let total_iterations: u64 = 600_000_000;
                let chunk_size: u64 = 25_000_000; // Yield every 25 million iterations
                let mut sum: u64 = 0;

                for chunk_start in (0..total_iterations).step_by(chunk_size as usize) {
                    let chunk_end = (chunk_start + chunk_size).min(total_iterations);
                    for i in chunk_start..chunk_end {
                        sum = sum.wrapping_add(i.rotate_left(3) ^ 0x5555_5555_AAAA_AAAA);
                    }
                    // Yield execution back to Tokio's scheduler so heartbeats can run!
                    tokio::task::yield_now().await;
                }
                println!("🛡️ [yield_now] Completed in {:?}, result: {}", start.elapsed(), sum);
                sum
            })
        }
        "spawn-blocking" => {
            // FIX OPTION A: Offloading to Tokio's dedicated OS blocking threadpool
            tokio::task::spawn_blocking(|| {
                println!("🛡️ [spawn_blocking] Offloaded to dedicated blocking OS thread...");
                let start = Instant::now();
                let result = heavy_computation(600_000_000);
                println!("🛡️ [spawn_blocking] Completed in {:?}, result: {}", start.elapsed(), result);
                result
            })
        }
        _ => panic!("Unknown mode: {}. Use 'unmitigated', 'yield-now', or 'spawn-blocking'", mode),
    };

    let _ = tokio::join!(heartbeat_handle, compute_handle);
    println!("\n=== Lab Run Completed ===");
}
```

### The 3 Rust Systems Concepts You Will Fight

1. **The Cooperative Scheduling Contract**: Coming from C# or Go, engineers often assume the runtime automatically slices CPU time across threads. In Rust, Tokio cannot insert hardware interrupt routines into compiled assembly. Unless your task encounters an `.await` where the underlying future returns `Poll::Pending`, the thread will run user code without interruption.
2. **Lifetime Boundaries in `spawn_blocking`**: When migrating code to `tokio::task::spawn_blocking(move || { ... })`, the closure must satisfy the `'static + Send` trait bounds. Because the task executes on an independent OS thread pool that may outlive the caller, you cannot borrow references to local stack variables from the outer `async fn`. All inputs must be transferred by value (`move`) or shared via thread-safe reference counters (`Arc<T>`).
3. **Yield Granularity Overhead with `yield_now`**: If you insert `tokio::task::yield_now().await` inside the computation loop, yield granularity is critical. If you yield on every iteration of a 600,000,000 loop, the overhead of re-queuing the task and switching register states will increase execution time from 2 seconds to over 90 seconds. You must batch iterations into chunks (e.g., 25,000,000 operations per yield) to preserve CPU cache lines while capping starvation latency under 15ms.

### In-Depth Diagnostics via Custom Tracing

Beyond external tools like `tokio-console`, production systems often detect stalls by embedding a custom `tracing::Subscriber`. Below is an automated stall-detector layer you can compile directly into the lab:

```rust
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tracing::span::Id;
use tracing::Subscriber;
use tracing_subscriber::layer::Context;
use tracing_subscriber::Layer;

pub struct StallDetectorLayer {
    threshold: Duration,
    last_poll_start: Mutex<Option<Instant>>,
}

impl StallDetectorLayer {
    pub fn new(threshold: Duration) -> Self {
        Self {
            threshold,
            last_poll_start: Mutex::new(None),
        }
    }
}

impl<S: Subscriber> Layer<S> for StallDetectorLayer {
    fn on_enter(&self, _id: &Id, _ctx: Context<'_, S>) {
        let mut lock = self.last_poll_start.lock().unwrap();
        *lock = Some(Instant::now());
    }

    fn on_exit(&self, _id: &Id, _ctx: Context<'_, S>) {
        let mut lock = self.last_poll_start.lock().unwrap();
        if let Some(start) = lock.take() {
            let duration = start.elapsed();
            if duration > self.threshold {
                eprintln!(
                    "⚠️  [RUNTIME WARNING] Task poll duration exceeded threshold! Took: {:?}",
                    duration
                );
            }
        }
    }
}
```

When integrated, this layer intercepts every invocation of `Future::poll()`. If a single poll occupies the worker thread longer than 10ms, it immediately emits a warning trace pinpointing the offender before cascading timeouts occur.

---

## Days 3–4: Go Runtime CPU Loop & Preemption Mechanics (Reference Implementation)

Unlike Tokio's cooperative library model, Go provides **built-in runtime-level asynchronous preemption**. Prior to Go 1.14, a tight CPU loop with no function calls or memory allocations would starve an OS thread (`M`) indefinitely. Since Go 1.14, the runtime monitor thread (`sysmon`) forcibly preempts loops using operating system signals (`SIGURG`).

### Complete Go Reference Implementation (`main.go`)

```go
package main

import (
	"flag"
	"fmt"
	"os"
	"runtime"
	"sync"
	"time"
)

// Latency-critical heartbeat monitor simulating an edge health check
func runHeartbeat(duration time.Duration, wg *sync.WaitGroup) {
	defer wg.Done()
	ticker := time.NewTicker(50 * time.Millisecond)
	defer ticker.Stop()

	lastTick := time.Now()
	for tick := 1; tick <= 20; tick++ {
		<-ticker.C
		elapsed := time.Since(lastTick)
		lastTick = time.Now()

		if elapsed > 75*time.Millisecond {
			fmt.Fprintf(os.Stderr, "🚨 [GO HEARTBEAT STALL] Tick %d: Took %v (Jitter: +%v)\n",
				tick, elapsed, elapsed-50*time.Millisecond)
		} else {
			fmt.Printf("✅ [Go Heartbeat OK] Tick %d: %v\n", tick, elapsed)
		}
	}
}

// Tight, non-allocating computational loop with NO function calls inside the body.
// This is designed specifically to test signal-based preemption!
func tightComputeLoop(duration time.Duration, wg *sync.WaitGroup) {
	defer wg.Done()
	fmt.Printf("🔥 [Go Compute] Starting tight non-allocating loop for %v...\n", duration)
	start := time.Now()

	var counter uint64
	// Within the inner loop we burn pure arithmetic operations without function preambles.
	for time.Since(start) < duration {
		for i := 0; i < 100_000_000; i++ {
			counter = counter ^ 0xDEADBEEFCAFEBABE
			counter = (counter << 1) | (counter >> 63)
		}
	}
	fmt.Printf("🔥 [Go Compute] Finished in %v, counter: %d\n", time.Since(start), counter)
}

func main() {
	procs := flag.Int("procs", 1, "Number of logical processors (GOMAXPROCS)")
	flag.Parse()

	runtime.GOMAXPROCS(*procs)
	fmt.Printf("=== Go Runtime Preemption Lab (GOMAXPROCS=%d) ===\n", runtime.GOMAXPROCS(0))

	var wg sync.WaitGroup
	wg.Add(2)

	// Launch heartbeat monitor
	go runHeartbeat(time.Second, &wg)

	// Sleep briefly to establish regular tick rhythm
	time.Sleep(120 * time.Millisecond)

	// Launch tight CPU loop on the SAME logical processor (P0)
	go tightComputeLoop(1000*time.Millisecond, &wg)

	wg.Wait()
	fmt.Println("=== Lab Finished Successfully ===")
}
```

### Automated Benchmark Suite (`main_test.go`)

To verify preemption under load in CI pipelines, execute this benchmark suite:

```go
package main

import (
	"runtime"
	"sync"
	"testing"
	"time"
)

func BenchmarkPreemptionJitter(b *testing.B) {
	runtime.GOMAXPROCS(1)
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		var wg sync.WaitGroup
		wg.Add(2)

		go func() {
			defer wg.Done()
			ticker := time.NewTicker(20 * time.Millisecond)
			defer ticker.Stop()
			for j := 0; j < 5; j++ {
				<-ticker.C
			}
		}()

		go func() {
			defer wg.Done()
			start := time.Now()
			var dummy uint64
			for time.Since(start) < 100*time.Millisecond {
				dummy ^= 0x123456789ABCDEF0
			}
		}()

		wg.Wait()
	}
}
```

### Step-by-Step Diagnostic Verification

#### Run 1: Default Go 1.14+ Asynchronous Preemption (`SIGURG` Active)
Execute with a single logical processor (`GOMAXPROCS=1`):
```bash
go run main.go -procs 1
```
**Observed Behavior**: Despite both the heartbeat and the compute loop sharing a single OS thread (`P0`), the heartbeat continues ticking smoothly every 50ms–65ms. Every 10ms, the background `sysmon` thread detects that `tightComputeLoop` has exceeded its time slice, delivers `SIGURG` to the OS thread `M0`, rewires its instruction pointer to `runtime.asyncPreempt`, and yields control to the heartbeat goroutine.

#### Run 2: Disabling Asynchronous Preemption (`asyncpreemptoff=1`)
Now, force Go to behave like legacy Go 1.13 by disabling signal preemption:
```bash
GODEBUG=asyncpreemptoff=1 go run main.go -procs 1
```
**Observed Behavior**: Total reactor freeze! The heartbeat monitor emits zero output for the entire 1,000ms duration of the compute loop. Because the loop contains no function calls (which contain compiler-inserted `morestack` preambles) and signal preemption is disabled, the goroutine cannot be descheduled.

#### Run 3: Dissecting the Go Scheduler Trace
Run with scheduler tracing enabled to observe runtime thread management:
```bash
GODEBUG=schedtrace=1000,scheddetail=1 go run main.go -procs 1
```

**Trace Output Dissection**:
```text
SCHED 1002ms: gomaxprocs=1 idleprocs=0 threads=4 spinningthreads=0 needspinning=0 runqueue=1
  P0: status=1 schedtick=32 syscalltick=0 m=2 runqsize=1 gfreecnt=0 timerslen=1
    runq: [2]
  M2: p=0 curg=3 mallocing=0 throwing=0 preemptoff= locks=0 dying=0
    G3: status=2(running) m=2 ...
  M1: p=-1 curg=0 ... (sysmon background thread)
```
- `gomaxprocs=1`: Single logical processor active.
- `P0: status=1 runqsize=1 runq: [2]`: Processor 0 is actively executing Goroutine 3 (`tightComputeLoop`), while Goroutine 2 (`runHeartbeat`) waits in `P0`'s local run queue.
- `M1: p=-1`: The `sysmon` thread running continuously without a `P`, monitoring `P0`'s execution duration to trigger `SIGURG`.

#### Inspecting Compiler-Inserted Cooperative Check Preamble
To see why standard loops yield cooperatively while tight loops do not, inspect the assembly generated by the Go compiler:
```bash
go tool compile -S main.go | grep -A 5 "TEXT.*runHeartbeat"
```
You will observe instructions resembling:
```assembly
0x0000 TEXT main.runHeartbeat(SB), ABIInternal, $48-16
0x0000 MOVQ (TLS), R14
0x0009 CMPQ SP, 16(R14)
0x000d JLS  0x004a
...
0x004a CALL runtime.morestack_noctxt(SB)
```
Every function call checks whether `SP <= stackguard0`. If the runtime needs to preempt the goroutine cooperatively (or grow the stack), it sets `stackguard0 = stackPreempt`. When the loop executes no function calls, this check never runs, necessitating `sysmon` and `SIGURG`.

---

## Friday Mob Review: Metrics, Deep Architectural Q&A, and Sign-Off

### Benchmark Verification Table

During the Friday mob review, run all test permutations and fill in the recorded latency metrics:

| Scenario | P50 Jitter | P99 Jitter | Heartbeat Misses (>75ms) | CPU Core Utilization |
| :--- | :--- | :--- | :--- | :--- |
| **Tokio**: Unmitigated CPU Loop | > 2,000 ms | > 2,400 ms | 100% during compute | 100% on 1 worker core |
| **Tokio**: Chunked `yield_now().await` | < 6 ms | < 18 ms | 0% | Distributed across cores |
| **Tokio**: Offloaded `spawn_blocking` | < 2 ms | < 5 ms | 0% | Threadpool offloaded |
| **Go**: Default (`SIGURG` Preemption) | < 8 ms | < 15 ms | 0% | Preempted every 10ms |
| **Go**: `asyncpreemptoff=1` | > 950 ms | > 1,000 ms | 100% during compute | 100% frozen on P0 |

---

### 7 Deep Technical Architectural Questions

#### 1. Why does Tokio's multi-threaded scheduler fail to preempt a `while(true)` loop, whereas Go 1.14+ handles it automatically?
Tokio is an application-level runtime library compiled into standard user-space machine code without specialized compiler support or kernel signal injection. Tokio relies entirely on cooperative multitasking: a task must voluntarily relinquish execution by returning `Poll::Pending` at an `.await` suspension point. Go, by contrast, is an integrated language and runtime ecosystem. Go's runtime controls compiler assembly generation (inserting `morestack` stack check preambles) and includes an ambient monitoring thread (`sysmon`) that issues kernel signals (`SIGURG`) to interrupt executing OS threads at valid register safe points.

#### 2. What is the mechanical cost of `tokio::task::yield_now().await` compared to `tokio::task::spawn_blocking`?
`tokio::task::yield_now().await` involves minimal memory overhead: it requires zero heap allocations, merely moving the task's intrusive linked-list node to the tail of the worker's local run queue. However, if invoked too frequently, it destroys CPU pipeline execution and invalidates L1/L2 data caches. `spawn_blocking` dispatches the work to a separate, dedicated OS thread pool. This entails mutex contention on the blocking work queue, potential OS thread context switching latency, and a heap allocation for the boxed closure. Use `yield_now` for chunked CPU workloads; use `spawn_blocking` for long uninterrupted calculations or blocking synchronous file/C-library I/O.

#### 3. How does the Go runtime handle a goroutine executing a tight loop in pure C code via CGO or an uninterruptible OS syscall? Does `SIGURG` work there?
No. `SIGURG` cannot preempt a goroutine executing inside foreign C code (CGO) or an uninterruptible OS syscall. The Go runtime cannot inspect or trace foreign C stack frames, nor does it know the register allocation map of C functions. Rewiring the program counter inside arbitrary C code would corrupt memory and crash the process. Instead, Go handles CGO and blocking syscalls cooperatively via `runtime.entersyscall()`: the runtime detaches the logical processor `P` from the blocking OS thread `M`, allowing an idle OS thread to bind to `P` and continue servicing runnable goroutines.

#### 4. In C#, what happens if a ThreadPool worker thread executes a tight non-yielding loop inside an `async Task` method? How does the CLR ThreadPool Hill-Climbing algorithm react?
The ThreadPool worker thread is monopolized and cannot service queued work items or continuation callbacks. Consider this C# starvation snippet:
```csharp
ThreadPool.GetAvailableThreads(out int workerThreads, out _);
Task.Run(() => { while (true) { /* Burns ThreadPool worker */ } });
```
If multiple worker threads encounter synchronous compute loops, pending tasks accumulate in the global ThreadPool queue, causing catastrophic latency. The CLR's **Hill-Climbing algorithm** monitors completed work throughput and CPU saturation; detecting stalled throughput, it injects additional OS threads into the ThreadPool at a throttled rate (typically 1 to 2 threads per second). This slow ramp-up causes multi-second latency degradation before sufficient threads are spawned to service waiting I/O continuations.

#### 5. Why did the Go team choose `SIGURG` over other POSIX signals like `SIGALRM` or `SIGVTALRM`? What happens on Windows where POSIX signals do not exist?
`SIGALRM` and `SIGVTALRM` are commonly utilized by third-party C libraries and debugging tools for profiling, interval timers, or timeouts; intercepting them would introduce severe compatibility bugs. `SIGURG` (urgent out-of-band socket data) is virtually unused in modern network software, and its default POSIX action is to be silently ignored. On Windows, which lacks POSIX signals, the Go runtime achieves preemption using the Win32 API functions `SuspendThread`, `GetThreadContext`, and `SetThreadContext` to pause target threads and rewrite their instruction pointers directly.

#### 6. How does pinning (`Pin<T>`) impact Tokio's ability to migrate tasks across worker threads in a work-stealing scheduler?
Pinning guarantees **pointer address stability in memory**, not thread affinity. A `Pin<Box<Task>>` or heap-allocated future resides at a fixed virtual memory address on the heap. When a worker thread steals a task from another worker's queue, it moves the *pointer* (`Pin<Box<T>>`) across thread deques, but the pointee data in heap memory does not move. Because Tokio tasks implement the `Send` marker trait, transferring ownership of the pinned pointer across OS threads is entirely safe. Pinning prevents memory relocations within address space; it does not bind execution to a physical core.

#### 7. Under what circumstances can cooperative `yield_now()` in Tokio cause an infinite busy-loop or live-lock?
If a worker thread's local run queue contains only the task calling `yield_now()` and no other tasks are ready, `yield_now()` will immediately reschedule and poll that same task on the very next cycle. If that task repeatedly calls `yield_now()` while waiting for an external condition that can only be satisfied by a task stuck on another saturated thread, the worker core spins at 100% CPU utilization without making progress:
```rust
// ANTI-PATTERN: Burns 100% CPU in a live-lock busy-loop
while !shared_flag.load(Ordering::Relaxed) {
    tokio::task::yield_now().await;
}
```
Idle waiting must always be mediated by event-driven notification (`Waker.wake()`, notification channels, or timer futures), never busy-yielding.

---

## 7-Point Sign-Off Checklist

Before submitting your lab results for team sign-off, verify each item:

- [ ] **1. Baseline Jitter Verified**: Confirmed that the latency-sensitive heartbeat runs with < 20ms jitter when no compute task is active.
- [ ] **2. Tokio Starvation Reproduced**: Confirmed that running unmitigated compute in Tokio causes heartbeat jitter to exceed 1,000ms.
- [ ] **3. Tokio Diagnostics Captured**: Observed task poll duration in `tokio-console` or trace logs exceeding 100ms.
- [ ] **4. Tokio Fixes Benchmarked**: Verified that both `spawn_blocking` and chunked `yield_now` restore heartbeat jitter to < 20ms.
- [ ] **5. Go Preemption Confirmed**: Observed Go's `SIGURG` preemption maintaining heartbeat ticks under `GOMAXPROCS=1`.
- [ ] **6. Go Async Preemption Failure Reproduced**: Confirmed that executing with `GODEBUG=asyncpreemptoff=1` completely freezes the heartbeat.
- [ ] **7. Schedtrace Analyzed**: Verified goroutine queue transitions (`runqsize`, `curg`, `sysmon`) using `GODEBUG=schedtrace=1000`.

---

## Stretch Goals for Fast Learners

1. **Custom Tokio Tracing Layer**: Integrate the provided `StallDetectorLayer` into your Tokio lab. Set the detection threshold to 15ms and verify that it flags the compute task while ignoring the fast heartbeat ticks.
2. **Measuring SIGURG Latency via `perf`**: On Linux, execute `perf record -e signal:signal_deliver go run main.go -procs 1` to capture operating system signal deliveries. Analyze the time delta between `sysmon` signal emission and user-space resumption.
3. **Lock-Free Local Run Queue**: Implement a 256-element lock-free work-stealing circular ring buffer in Rust matching the architecture of Go's `runq` and Tokio's local queue, complete with atomic head/tail CAS management and `steal_half` mechanics.
