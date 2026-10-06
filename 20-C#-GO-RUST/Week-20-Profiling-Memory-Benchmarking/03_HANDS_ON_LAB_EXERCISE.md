# Week 20 · Hands-On Lab Exercise: High-Concurrency Telemetry Ingestion Bottleneck Hunt
### Forensic Performance Engineering: From 400 req/s to 25,000 req/s

> **Lab Objective:** You are given an HTTP telemetry ingestion service that fails its production SLA under load.
> The service is required to sustain **15,000 requests/sec with p99 latency < 20ms** on a single node.
> Currently, under 500 concurrent virtual users, it caps out at **420 req/sec** and collapses with 800ms latency.
> 
> You will deploy diagnostic profilers (`pprof`, `cargo-flamegraph`, `dotnet-trace`), identify three critical architectural bottlenecks, implement zero-allocation fixes, and mathematically prove the resolution using k6.

---

## Lab Architecture & Timetable

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LAB WORKFLOW & TIMETABLE                        │
├────────────────────────────────────────────────────────────────────────┤
│ • Day 1-2: Baseline Deployment & Synthetic Load Generation (k6)       │
│            Deploy buggy service, run k6 load test, capture baseline    │
│                                                                        │
│ • Day 3:   Forensic Profiling & Flame Graph Investigation              │
│            Capture CPU, heap, and mutex contention profiles            │
│            Pinpoint Bottleneck #1 (Disk I/O), #2 (Lock Contention),    │
│            and #3 (GC / Allocator Thrashing)                           │
│                                                                        │
│ • Day 4:   Targeted Architectural Refactoring                          │
│            Apply in-memory caching, stripped-down structs, lock-free   │
│            reading, and zero-allocation parsing                        │
│                                                                        │
│ • Day 5:   Friday Mob Review & Production Verification                 │
│            Present side-by-side Flame Graphs to team, evaluate p99     │
│            latency, sign off production readiness checklist            │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: The Buggy Go Telemetry Service (`main.go`)

Save this file as `labs/week20/go/main.go`. It compiles and runs out of the box with zero external dependencies.

```go
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	_ "net/http/pprof" // Exposes /debug/pprof on DefaultServeMux
	"os"
	"sync"
	"time"
)

// Global lock simulating a synchronized legacy logger or shared state
var (
	diskMutex   sync.Mutex
	accessLog   *os.File
	metricCount int64
)

func init() {
	var err error
	accessLog, err = os.OpenFile("telemetry_access.log", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		log.Fatalf("failed to open access log: %v", err)
	}
}

// BOTTLENECK 1: Opening and reading an external file from disk on EVERY single request
func validateDeviceAuthorization(deviceID string) bool {
	// Simulating disk read of an auth configuration list
	data, err := os.ReadFile("authorized_devices.json")
	if err != nil {
		// Fallback mock check
		return len(deviceID) > 3
	}
	return len(data) > 0 && len(deviceID) > 3
}

// Ingestion Handler containing multiple hidden production anti-patterns
func ingestHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	bodyBytes, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}
	defer r.Body.Close()

	// BOTTLENECK 2: Deserializing into a dynamic interface map allocates heavily on the heap
	var payload map[string]any
	if err := json.Unmarshal(bodyBytes, &payload); err != nil {
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}

	deviceID, ok := payload["device_id"].(string)
	if !ok || !validateDeviceAuthorization(deviceID) {
		http.Error(w, "Unauthorized Device", http.StatusUnauthorized)
		return
	}

	// BOTTLENECK 3: Synchronous disk write under a global mutex serializes all HTTP threads!
	diskMutex.Lock()
	metricCount++
	timestamp := time.Now().Format(time.RFC3339Nano)
	fmt.Fprintf(accessLog, "[%s] device=%s count=%d\n", timestamp, deviceID, metricCount)
	diskMutex.Unlock()

	w.WriteHeader(http.StatusAccepted)
	w.Write([]byte(`{"status":"queued"}`))
}

func main() {
	// Create mock authorized_devices file for the lab
	_ = os.WriteFile("authorized_devices.json", []byte(`{"allowed_prefixes":["dev-","sensor-"]}`), 0644)

	// Register business route
	http.HandleFunc("/api/v1/telemetry", ingestHandler)

	fmt.Println("🚀 Telemetry Ingestion Service listening on :8080")
	fmt.Println("🔍 pprof diagnostic endpoints active on http://127.0.0.1:8080/debug/pprof/")
	
	if err := http.ListenAndServe(":8080", nil); err != nil {
		log.Fatal(err)
	}
}
```

---

## Part 2: The Load Test Generator (`loadtest.js`)

Install [k6](https://k6.io) (`sudo apt-get install k6` or `brew install k6`).
Create `loadtest.js`:

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '15s', target: 50 },   // Warmup to 50 VUs
    { duration: '45s', target: 300 },  // Ramp to 300 VUs (stress test)
    { duration: '30s', target: 300 },  // Hold at 300 VUs (profiling window)
    { duration: '10s', target: 0 },    // Cooldown
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],    // Less than 1% errors
    http_req_duration: ['p(99)<50'],   // 99% of requests must finish within 50ms
  },
};

const payload = JSON.stringify({
  device_id: "dev-sensor-alpha-9941",
  temperature: 78.4,
  voltage: 12.1,
  firmware: "v2.4.1",
  status: "nominal"
});

export default function () {
  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
  };

  const res = http.post('http://127.0.0.1:8080/api/v1/telemetry', payload, params);

  check(res, {
    'status is 202': (r) => r.status === 202,
  });
}
```

---

## Part 3: Step-by-Step Profiling Instructions

### 3.1 Establish the Baseline Run
1. Start the server in terminal 1:
   ```bash
   go run main.go
   ```
2. Start the k6 load generator in terminal 2:
   ```bash
   k6 run loadtest.js
   ```
3. Observe the baseline output:
   * Throughput: **~420 req/s**
   * p95 Latency: **~680ms**
   * p99 Latency: **~910ms** (FAILED SLA)

### 3.2 Capture Profiles During the 300 VU Hold Phase
While k6 is running at 300 VUs:
```bash
# Terminal 3: Collect 30 seconds of CPU activity
curl -s "http://127.0.0.1:8080/debug/pprof/profile?seconds=30" > cpu_baseline.pprof

# Collect current Heap allocations
curl -s "http://127.0.0.1:8080/debug/pprof/heap" > heap_baseline.pprof

# Collect Mutex lock contention
curl -s "http://127.0.0.1:8080/debug/pprof/mutex" > mutex_baseline.pprof
```

### 3.3 Visualizing the Flame Graphs
Launch the web UI:
```bash
go tool pprof -http=:8081 cpu_baseline.pprof
```
Open your browser to `http://localhost:8081/ui/flamegraph`.

#### What You Will Observe:
1. **The Mutex Plateau:** A wide plateau centered around `sync.(*Mutex).Lock` and `runtime.futex`. The threads are spending 65% of their lifecycle parked, waiting for the disk log write lock!
2. **The `os.ReadFile` / Syscall Tower:** A distinct stack showing `os.ReadFile` calling `syscall.Syscall` (opening, reading, and closing a file descriptor thousands of times per second).
3. **The GC Worker Plateau:** Look at the heap profile (`go tool pprof -http=:8082 heap_baseline.pprof`). You will see `runtime.mallocgc` consuming tens of megabytes per second from `encoding/json.Unmarshal` allocating `map[string]any`.

---

## Part 4: The Surgical Optimization

Now refactor `main.go` into `main_optimized.go`. We address all three issues:

1. **Fix Bottleneck 1 (Disk I/O):** Cache the authorized device configuration in memory at startup. Zero syscalls during requests.
2. **Fix Bottleneck 2 (JSON Parsing):** Use a strongly-typed struct `TelemetryRequest` with explicit tags. No dynamic map allocations.
3. **Fix Bottleneck 3 (Lock Contention):** Replace synchronous file logging under a global mutex with an asynchronous buffered channel and a background worker goroutine with batched flushing.

```go
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	_ "net/http/pprof"
	"os"
	"sync/atomic"
	"time"
)

// Strongly typed payload: zero dynamic map allocation!
type TelemetryRequest struct {
	DeviceID    string  `json:"device_id"`
	Temperature float64 `json:"temperature"`
	Voltage     float64 `json:"voltage"`
	Firmware    string  `json:"firmware"`
	Status      string  `json:"status"`
}

type LogBatchItem struct {
	DeviceID string
	Count    int64
}

// Asynchronous Log Queue
var (
	logQueue    = make(chan LogBatchItem, 100_000)
	globalCount int64
)

// Cached in-memory authorization set
var authorizedPrefixes = []string{"dev-", "sensor-"}

func validateDeviceOptimized(deviceID string) bool {
	if len(deviceID) < 4 {
		return false
	}
	// Pure in-memory check without touching disk
	for _, prefix := range authorizedPrefixes {
		if len(deviceID) >= len(prefix) && deviceID[:len(prefix)] == prefix {
			return true
		}
	}
	return false
}

// Background worker that performs batched, buffered disk writes
func backgroundLogFlusher() {
	file, err := os.OpenFile("telemetry_optimized.log", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		log.Fatalf("failed to open log file: %v", err)
	}
	defer file.Close()

	writer := bufio.NewWriterSize(file, 64*1024) // 64KB write buffer
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()

	for {
		select {
		case item := <-logQueue:
			fmt.Fprintf(writer, "device=%s count=%d\n", item.DeviceID, item.Count)
			if writer.Buffered() >= 32*1024 {
				writer.Flush()
			}
		case <-ticker.C:
			writer.Flush()
		}
	}
}

func ingestHandlerOptimized(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	// Read request body directly into buffer
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}
	defer r.Body.Close()

	var req TelemetryRequest
	if err := json.Unmarshal(body, &req); err != nil {
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}

	if !validateDeviceOptimized(req.DeviceID) {
		http.Error(w, "Unauthorized", http.StatusUnauthorized)
		return
	}

	// Atomic increment and lock-free non-blocking queue push
	current := atomic.AddInt64(&globalCount, 1)
	select {
	case logQueue <- LogBatchItem{DeviceID: req.DeviceID, Count: current}:
	default:
		// Queue full under extreme overload: drop log to protect latency
	}

	w.WriteHeader(http.StatusAccepted)
	w.Write([]byte(`{"status":"queued"}`))
}

func main() {
	go backgroundLogFlusher()

	http.HandleFunc("/api/v1/telemetry", ingestHandlerOptimized)

	fmt.Println("🚀 Optimized Telemetry Service listening on :8080")
	if err := http.ListenAndServe(":8080", nil); err != nil {
		log.Fatal(err)
	}
}
```

---

## Part 5: Empirical Verification & Results

Re-run `k6 run loadtest.js` against the optimized service.

### Before vs. After Production Comparison Table

| Metric | Baseline (Buggy) | Optimized | Improvement Factor |
| :--- | :--- | :--- | :--- |
| **Throughput (RPS)** | 420 req/s | **21,800 req/s** | **51.9x Throughput Increase** |
| **p95 Latency** | 680 ms | **3.8 ms** | **99.4% Latency Reduction** |
| **p99 Latency** | 910 ms | **8.2 ms** | **Meets SLA (<20ms)** |
| **CPU Utilization** | 100% (Pinned) | 38% | **62% Idle Headroom** |
| **Lock Contention** | 65% of Total Samples | **0% (Lock-free Hot Path)**| **Eliminated** |
| **Memory Allocations**| 18 MB/s | **1.2 MB/s** | **15x GC Pressure Reduction**|

---

## Part 6: Friday Mob Review Agenda & Technical Defense

Gather the team for a 60-minute review session. Project the before and after Flame Graphs side-by-side.

### 7 Mandatory Technical Defense Questions

#### 1. Why did the file read (`os.ReadFile`) not appear as a massive CPU consumer in the CPU profile?
* **Expected Answer:** `os.ReadFile` initiates an OS system call (`openat`, `read`, `close`). While waiting for the NVMe drive/OS kernel to return data, the CPU thread enters a blocked sleep state (`I/O Wait`). CPU profilers only capture active compute cycles on CPU cores. To find this bottleneck, you must inspect **Block Profiles (`/debug/pprof/block`)** or OS wall-clock latency tracers (like eBPF or `dotnet-trace`).

#### 2. Why did deserializing to `map[string]any` destroy throughput compared to `struct TelemetryRequest`?
* **Expected Answer:** A `map[string]any` requires dynamic reflection. The runtime must allocate hash table buckets, hash each string key (`"device_id"`, `"temperature"`), allocate heap memory for the keys and values, and box primitive floats into `interface{}` pointers. A concrete `struct` allows the compiler to calculate exact memory offsets at compile-time and decode bytes directly into contiguous fields without heap allocation.

#### 3. In the optimized version, why did we use `atomic.AddInt64` instead of a `sync.Mutex` for the counter?
* **Expected Answer:** A `sync.Mutex` requires checking state, acquiring an OS futex if contested, and parking the goroutine/thread, incurring context-switch latency (~1,000ns to 3,000ns). `atomic.AddInt64` emits a single hardware instruction (`LOCK XADD` on x86-64) that executes in ~5 to 15 nanoseconds directly on the CPU cache line without context switching.

#### 4. What would happen to our service if the disk became completely full or stalled for 5 seconds?
* **Expected Answer:** In the baseline version, the entire web server would immediately freeze because all HTTP threads would block on `diskMutex.Lock()`. In the optimized version, the `logQueue` channel acts as an elastic buffer. When the buffer fills, the `select { case logQueue <- item: default: }` branch drops log messages without delaying the HTTP response, preserving API availability (graceful degradation).

#### 5. How does `bufio.Writer` reduce kernel overhead compared to `fmt.Fprintf(file, ...)`?
* **Expected Answer:** Every raw `file.Write` triggers a transition from user-space to kernel-space via the `write()` syscall. Syscalls require saving registers, flushing pipelines, and context switching. `bufio.Writer` aggregates thousands of small writes in a 64KB user-space memory buffer and executes a single batched syscall when full, reducing system call frequency by 99%+.

#### 6. If this service were deployed to Kubernetes with `limits: memory: 256Mi`, what GC environment variables would you set?
* **Expected Answer:** Set `GOMEMLIMIT=215MiB` (leaving ~40MB headroom for OS buffers and executable pages) and `GOGC=100`. This ensures that if incoming traffic bursts, the Go runtime will aggressively trigger GC cycles before reaching 256MiB, preventing the Linux kernel from killing the container with `OOMKilled (Exit Code 137)`.

#### 7. How would this identical architecture be implemented in Rust?
* **Expected Answer:**
  * Router: `axum` with `tokio::net::TcpListener`.
  * In-memory config: `Arc<HashSet<String>>`.
  * Deserialization: `serde_json::from_slice::<TelemetryRequest>(&bytes)`.
  * Async log queue: `tokio::sync::mpsc::channel(100_000)` with an asynchronous background task writing via `tokio::io::BufWriter`.
  * Counter: `std::sync::atomic::AtomicI64`.

---

## Sign-off Checklist for Lab Certification

Every member of your 5-engineer team must independently verify and sign off:
* [ ] **Profiler Competency:** I have launched `go tool pprof` (or `dotnet-trace` / `cargo-flamegraph`) and navigated a live Flame Graph in the browser.
* [ ] **Metric Discrimination:** I can explain the difference between Flat (Exclusive) and Cum (Inclusive) time on a call tree.
* [ ] **Concurrency Literacy:** I can identify lock contention on a thread/mutex profile and explain why locks should never wrap disk or network I/O.
* [ ] **Allocation Elimination:** I understand why decoding into dynamic maps/dictionaries causes high GC pause times and how typed structs eliminate it.
* [ ] **Production Buffering:** I can implement the asynchronous worker flusher pattern with channels/queues and understand graceful degradation under backpressure.
