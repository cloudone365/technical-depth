# Week 03 · Hands-On Lab Exercise: Zero-Escape Engineering & The Borrow Checker Obstacle Course
### Practical Memory Reclamation: From Escape Analysis Forensics to Static Lifetime Proofs

> **Lab Objective:** Transition from theoretical knowledge to muscle memory:
> 1. **Go Zero-Escape Challenge:** Take a production-style HTTP telemetry serializer that triggers 6 separate heap allocations per request and refactor it into a **zero-allocation (0 B/op, 0 allocs/op)** hot path using stack buffers, value receivers, and compiler escape analysis diagnostics (`-gcflags="-m -m"`).
> 2. **Rust Borrow Checker Obstacle Course:** Solve 5 real-world compiler errors that every senior C# engineer encounters when adapting to Rust's ownership model, learning to resolve lifetime conflicts mathematically rather than cheating with `.clone()`.
> 3. **Friday Mob Review:** Defend your architectural decisions, compare allocations, and certify team readiness.

---

## Lab Architecture & Timetable

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LAB WORKFLOW & TIMETABLE                        │
├────────────────────────────────────────────────────────────────────────┤
│ • Day 1-2: Go Zero-Escape HTTP Handler Refactoring                     │
│            Profile allocating starter code with `go test -benchmem`,   │
│            dissect AST escape flow graph with `-gcflags="-m -m"`,      │
│            refactor to 0 B/op using stack arrays and in-place buffers. │
│                                                                        │
│ • Day 3-4: The Rust Borrow Checker Obstacle Course                     │
│            Conquer 5 progressive lifetime compilation failures:       │
│            Iterator invalidation, self-references, multi-lifetimes,    │
│            local reference escapes, and struct reference bounds.       │
│                                                                        │
│ • Day 5:   Friday Mob Review & Benchmark Defense                       │
│            Present allocation metrics, explain NLL constraint solving, │
│            and complete the 7-point production sign-off checklist.     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: Days 1–2 — The Go Zero-Escape Challenge

### 1.1 The Allocating Starter Code (`telemetry_starter.go`)
Save this file into `labs/week03/go/telemetry_starter.go`:

```go
package main

import (
	"fmt"
	"strconv"
	"time"
)

type TelemetryRecord struct {
	Timestamp int64
	SensorID  string
	Reading   float64
	Status    string
}

// FormatTelemetryNaive formats a record into a CSV log line.
// DELIBERATE BUGS: Triggers 6 distinct heap allocations per invocation!
func FormatTelemetryNaive(rec *TelemetryRecord) string {
	// ESCAPE 1: fmt.Sprintf takes ...any, causing boxing of float64 and int64 into interface{}
	// ESCAPE 2: String concatenation and Sprintf return a newly allocated heap string
	line := fmt.Sprintf("%d,%s,%.2f,%s\n", rec.Timestamp, rec.SensorID, rec.Reading, rec.Status)
	return line
}

// Ingestion pipeline simulating a high-throughput worker
func ProcessBatchNaive(records []*TelemetryRecord) int {
	totalChars := 0
	for _, rec := range records {
		str := FormatTelemetryNaive(rec)
		totalChars += len(str)
	}
	return totalChars
}
```

### 1.2 Benchmark Harness (`telemetry_test.go`)
```go
package main

import (
	"testing"
)

var sampleRecord = &TelemetryRecord{
	Timestamp: 1774900000,
	SensorID:  "sensor-us-east-4421",
	Reading:   98.614,
	Status:    "OK",
}

func BenchmarkFormatNaive(b *testing.B) {
	b.ResetTimer()
	b.ReportAllocs()

	for i := 0; i < b.N; i++ {
		str := FormatTelemetryNaive(sampleRecord)
		if len(str) == 0 {
			b.Fatal("empty string")
		}
	}
}
```

Run the baseline benchmark:
```bash
go test -bench=BenchmarkFormatNaive -benchmem
```

**Expected Baseline Output:**
```text
BenchmarkFormatNaive-12    5200000    218.4 ns/op    96 B/op    5 allocs/op
```
At 100,000 requests/second, **5 allocs/op** translates to **500,000 heap allocations per second**, triggering constant garbage collection cycles and CPU thrashing!

### 1.3 Forensic Investigation with `-gcflags`
Run the compiler escape diagnostic:
```bash
go build -gcflags="-m -m" telemetry_starter.go
```
Inspect the output lines containing `escapes to heap`:
* `rec.Timestamp escapes to heap`: boxed into `interface{}` parameter of `fmt.Sprintf`.
* `rec.Reading escapes to heap`: boxed into `interface{}` parameter.
* `fmt.Sprintf(...) escapes to heap`: string return allocates a new byte slice buffer.

### 1.4 Your Task: Zero-Allocation Refactoring (`telemetry_optimized.go`)
Refactor the formatter to achieve **0 B/op and 0 allocs/op**:
1. Change signature to append into an existing pre-allocated caller buffer `[]byte`:  
   `func FormatTelemetryOptimized(dst []byte, rec *TelemetryRecord) []byte`
2. Eliminate `fmt.Sprintf` entirely.
3. Use `strconv.AppendInt` and `strconv.AppendFloat` to serialize numbers directly into the byte slice without string allocation.
4. Use `copy()` or direct slice appends for string literals.

#### The Verified Zero-Allocation Solution:
```go
// File: labs/week03/go/telemetry_optimized.go
package main

import (
	"strconv"
)

//go:noinline
func FormatTelemetryOptimized(dst []byte, rec *TelemetryRecord) []byte {
	// 1. Append Timestamp as base-10 integer
	dst = strconv.AppendInt(dst, rec.Timestamp, 10)
	dst = append(dst, ',')

	// 2. Append SensorID bytes directly without copy
	dst = append(dst, rec.SensorID...)
	dst = append(dst, ',')

	// 3. Append Reading float with 2 decimal precision
	dst = strconv.AppendFloat(dst, rec.Reading, 'f', 2, 64)
	dst = append(dst, ',')

	// 4. Append Status and newline
	dst = append(dst, rec.Status...)
	dst = append(dst, '\n')

	return dst
}
```

Add the optimized benchmark to `telemetry_test.go`:
```go
func BenchmarkFormatOptimized(b *testing.B) {
	// Pre-allocate a 128-byte stack buffer (reused across loop iterations)
	buf := make([]byte, 0, 128)
	b.ResetTimer()
	b.ReportAllocs()

	for i := 0; i < b.N; i++ {
		buf = buf[:0] // Reset length to 0 without reallocating capacity
		buf = FormatTelemetryOptimized(buf, sampleRecord)
		if len(buf) == 0 {
			b.Fatal("empty buffer")
		}
	}
}
```

Run the benchmark again:
```bash
go test -bench=. -benchmem
```

**Verified Optimization Results:**
```text
BenchmarkFormatNaive-12        5200000    218.4 ns/op    96 B/op    5 allocs/op
BenchmarkFormatOptimized-12   28400000     39.2 ns/op     0 B/op    0 allocs/op  <-- 5.5x Faster, ZERO Garbage!
```

---

## Part 2: Days 3–4 — The Rust Borrow Checker Obstacle Course

In Rust, you do not tune a garbage collector. You write code that mathematically satisfies the compiler’s ownership and lifetime proofs. Solve these 5 real-world obstacles.

---

### Obstacle 1: The Vector Iterator Invalidation Trap

#### The Broken Code (`src/obstacle1.rs`):
```rust
fn duplicate_evens(nums: &mut Vec<i32>) {
    for n in nums.iter() {
        if *n % 2 == 0 {
            nums.push(*n); // COMPILE ERROR!
        }
    }
}
```

#### The Exact Compiler Error:
```text
error[E0502]: cannot borrow `*nums` as mutable because it is also borrowed as immutable
 --> src/obstacle1.rs:3:14
  |
2 |     for n in nums.iter() {
  |              -----------
  |              |
  |              immutable borrow occurs here
  |              immutable borrow later used here
3 |         if *n % 2 == 0 {
4 |             nums.push(*n);
  |             ^^^^^^^^^^^^^ mutable borrow occurs here
```

#### The Logical Proof (Why the compiler rejected it):
In C++ or C#, calling `push()` while iterating over a collection can trigger a reallocation of the vector's backing array on the heap. If the array reallocates, the pointer held by `nums.iter()` becomes a **dangling pointer**, leading to undefined behavior or silent memory corruption. The Rust borrow checker rejects this at compile time.

#### The Systems Fix (Without `.clone()` of the vector):
Collect the values or iterate indices:
```rust
fn duplicate_evens_fixed(nums: &mut Vec<i32>) {
    // Collect the even values into a separate small stack/temporary buffer first
    let evens: Vec<i32> = nums.iter().copied().filter(|&x| x % 2 == 0).collect();
    nums.extend(evens);
}
```

---

### Obstacle 2: Returning a Reference to a Stack Local

#### The Broken Code (`src/obstacle2.rs`):
```rust
fn create_greeting(name: &str) -> &str {
    let greeting = format!("Hello, {}!", name);
    &greeting // COMPILE ERROR!
}
```

#### The Exact Compiler Error:
```text
error[E0515]: cannot return reference to local variable `greeting`
 --> src/obstacle2.rs:3:5
  |
3 |     &greeting
  |     ^^^^^^^^^ returns a reference to data owned by the current function
```

#### The Logical Proof:
`greeting` is owned by `create_greeting`. When the function reaches its closing curly brace `}`, the `Drop` trait deallocates `greeting`'s heap buffer. Returning a reference `&greeting` would hand the caller a pointer to deallocated memory (dangling pointer).

#### The Systems Fix:
Return ownership of the data (`String`), not a borrowed reference:
```rust
fn create_greeting_fixed(name: &str) -> String {
    format!("Hello, {}!", name) // Transfers ownership of String to caller
}
```

---

### Obstacle 3: Multiple References with Ambiguous Lifetimes

#### The Broken Code (`src/obstacle3.rs`):
```rust
fn select_prefix(prefix_a: &str, prefix_b: &str, use_first: bool) -> &str {
    if use_first {
        prefix_a
    } else {
        prefix_b
    }
}
```

#### The Exact Compiler Error:
```text
error[E0106]: missing lifetime specifier
 --> src/obstacle3.rs:1:68
  |
1 | fn select_prefix(prefix_a: &str, prefix_b: &str, use_first: bool) -> &str {
  |                            ----          ----                        ^ expected named lifetime parameter
  |
  = help: this function's return type contains a borrowed value, but the signature
          does not say whether it is borrowed from `prefix_a` or `prefix_b`
```

#### The Logical Proof:
Rust applies **Lifetime Elision Rules**:
* Rule 1: Each elided lifetime in parameters is assigned a distinct lifetime parameter (`'a`, `'b`).
* Rule 2: If there are multiple input lifetime parameters, the compiler cannot guess which input the return reference is bound to!
If the caller drops `prefix_a` while still holding the returned reference, the reference might be invalid if it came from `prefix_a`.

#### The Systems Fix:
Annotate the parameters with a shared lifetime constraint `'a`:
```rust
// Tells the compiler: The returned reference is valid for the INTERSECTION 
// of the lifetimes of prefix_a and prefix_b.
fn select_prefix<'a>(prefix_a: &'a str, prefix_b: &'a str, use_first: bool) -> &'a str {
    if use_first {
        prefix_a
    } else {
        prefix_b
    }
}
```

---

### Obstacle 4: Mutating a Struct Field While Borrowing Another

#### The Broken Code (`src/obstacle4.rs`):
```rust
struct Player {
    name: String,
    score: u32,
    log: Vec<String>,
}

impl Player {
    fn update_score(&mut self, points: u32) {
        let name_ref = &self.name;
        self.score += points;
        // Attempting to mutate self.log while holding self.name
        self.log_event(&format!("Player {} scored {}", name_ref, points)); // COMPILE ERROR?
    }

    fn log_event(&mut self, event: &str) {
        self.log.push(event.to_string());
    }
}
```

#### The Logical Proof & The Split Borrow Solution:
Calling `self.log_event(...)` borrows the **entire `self` mutably** (`&mut self`). But `name_ref` is currently borrowing `self.name` immutably.
Rust's compiler is smart enough to understand **Disjoint Field Borrows** within the same function, but method calls take `&mut self` as a whole!

#### The Systems Fix:
Avoid borrowing `&mut self` when only a subfield is needed:
```rust
impl Player {
    fn update_score_fixed(&mut self, points: u32) {
        self.score += points;
        let event = format!("Player {} scored {}", self.name, points);
        self.log.push(event); // Mutates only self.log directly!
    }
}
```

---

### Obstacle 5: Struct Holding a Borrowed Reference Without Lifetime

#### The Broken Code (`src/obstacle5.rs`):
```rust
struct TokenParser {
    source: &str, // COMPILE ERROR!
    position: usize,
}
```

#### The Exact Compiler Error:
```text
error[E0106]: missing lifetime specifier
 --> src/obstacle5.rs:2:13
  |
2 |     source: &str,
  |             ^ expected named lifetime parameter
  |
help: consider introducing a named lifetime parameter
  |
1 ~ struct TokenParser<'a> {
2 ~     source: &'a str,
  |
```

#### The Systems Fix:
```rust
// Guarantees that TokenParser can never outlive the string buffer it references
pub struct TokenParser<'a> {
    pub source: &'a str,
    pub position: usize,
}

impl<'a> TokenParser<'a> {
    pub fn new(source: &'a str) -> Self {
        Self { source, position: 0 }
    }

    pub fn next_token(&mut self) -> Option<&'a str> {
        if self.position >= self.source.len() {
            return None;
        }
        let remaining = &self.source[self.position..];
        self.position = self.source.len();
        Some(remaining)
    }
}
```

---

## Part 3: Friday Mob Review & Defense Protocol

Gather the 5-engineer team. Display terminal benchmarks and compiler error diagnostics.

### 7 Mandatory Technical Defense Questions

#### 1. Why did switching from `fmt.Sprintf` to `strconv.AppendInt` eliminate allocations in Go?
* **Expected Answer:** `fmt.Sprintf` takes `...any` (variadic interface slice). This forces primitive integers and floats to be boxed into heap-allocated `iface` envelopes. In contrast, `strconv.AppendInt` accepts an existing slice buffer `[]byte` and writes ASCII digit characters directly into contiguous stack memory using simple byte math, with zero heap allocations.

#### 2. Why does returning a pointer to a struct in Go not cause a segmentation fault like it does in C?
* **Expected Answer:** The Go compiler runs Escape Analysis. If it sees a pointer escaping the function's stack frame, it automatically re-routes the allocation from the goroutine's stack to the managed heap at compile-time (`runtime.newobject`). In C, the stack frame is destroyed upon return, leaving a dangling pointer.

#### 3. In Rust Obstacle 1, why was `nums.push(*n)` rejected while iterating over `nums.iter()`?
* **Expected Answer:** `nums.iter()` creates an immutable borrow (`&nums`) that holds a pointer into the vector's backing array. Calling `push()` requires a mutable borrow (`&mut nums`) because pushing may exceed capacity, forcing reallocation to a new heap address. If Rust permitted this, the iterator's pointer would immediately become invalid, causing a use-after-free vulnerability.

#### 4. How does Non-Lexical Lifetimes (NLL) differ from lexical scoping in Rust?
* **Expected Answer:** Lexical scoping keeps a reference alive until the closing curly brace `}` of the block where it was declared. NLL constructs a Control Flow Graph (CFG) in MIR and ends the lifetime at the point of its last actual read or write instruction, allowing mutable borrows to be taken later in the same block.

#### 5. What is the memory footprint of a Go slice header versus a Rust `Vec<T>`?
* **Expected Answer:** Both are exactly **24 bytes** on a 64-bit architecture:
  * Go slice: Pointer (8 bytes) + Length (8 bytes) + Capacity (8 bytes).
  * Rust `Vec<T>`: Pointer (8 bytes) + Capacity (8 bytes) + Length (8 bytes).

#### 6. In C#, how does the CLR prevent Gen 2 full heap scans during Gen 0 collections?
* **Expected Answer:** The CLR JIT injects **Write Barriers** on every reference assignment. When an older Gen 2 object is modified to reference a younger Gen 0 object, the CPU marks a bit in a Card Table. During a Gen 0 collection, the GC only inspects memory pages marked "dirty" in the card table.

#### 7. Why does Rust's `TokenParser<'a>` require a lifetime parameter, while a C# parser class does not?
* **Expected Answer:** In C#, strings are managed heap objects. The Garbage Collector keeps the string alive as long as the parser holds a reference, deferring safety to runtime GC tracking. In Rust, `&'a str` is a borrowed view of memory owned elsewhere. The lifetime parameter `'a` allows the compiler to prove at compile time that the underlying string buffer will outlive the `TokenParser`, preventing use-after-free bugs without runtime GC overhead.

---

## Lab Sign-off Checklist

Each engineer must verify and sign off:
* [ ] **Go Escape Analysis Mastery:** I can run `go build -gcflags="-m -m"` and identify the exact AST flow path that caused an escape.
* [ ] **Zero-Allocation Certification:** I have refactored an allocating Go function to produce `0 B/op` and `0 allocs/op` under `go test -benchmem`.
* [ ] **Rust Borrow Rules:** I can state the borrow rule from memory: any number of `&T` XOR exactly one `&mut T`.
* [ ] **Lifetime Literacy:** I understand that `'a` represents a compile-time constraint between references, not a runtime duration.
* [ ] **RAII vs GC Determinism:** I can explain why Rust's `Drop` executes at compile-time boundaries while C# finalizers run non-deterministically.
