# Week 06 · Hands-On Lab Exercise: The Storage Engine Benchmark — Static vs. Dynamic Dispatch
### Empirical Virtual Call Overheads Across C#, Go, and Rust

> **Lab Objective:** In high-performance backend systems (distributed databases, low-latency caches, message brokers), abstraction layers must be carefully chosen.
> 
> You will build a multi-backend storage engine (`MemoryStorage`, `DiskStorage`) across **C#**, **Go**, and **Rust**:
> 1. Implement both **Static Dispatch (Monomorphization / Generics)** and **Dynamic Dispatch (Vtables / Fat Pointers / Interface Boxing)**.
> 2. Benchmark 10,000,000 sequential `Put` and `Get` operations.
> 3. Dissect the generated machine code using disassemblers (`cargo asm`, `objdump`, `disasm`) to prove where the compiler inlines method bodies vs. where it emits indirect jump instructions (`call [rax]`).
> 4. Defend your architectural choices in the Friday mob review.

---

## Lab Architecture & Timetable

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LAB WORKFLOW & TIMETABLE                        │
├────────────────────────────────────────────────────────────────────────┤
│ • Day 1-2: Go Reference Storage Implementation                         │
│            Implement Storage interface, MemoryStorage, and DiskStorage.│
│            Benchmark direct struct calls vs `runtime.iface` dispatch.  │
│                                                                        │
│ • Day 3-4: Rust Static vs Dynamic Dispatch Engines                     │
│            Implement StorageEngine trait.                              │
│            Build StaticDb<S: StorageEngine> (monomorphized generics).  │
│            Build DynamicDb (Box<dyn StorageEngine> trait objects).     │
│            Verify Object Safety rules and benchmark with Criterion.    │
│                                                                        │
│ • Day 5:   Assembly Disassembly & Friday Mob Review                    │
│            Inspect assembly with `cargo asm` to prove inlining.        │
│            Complete 7-question technical defense and certification.    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: Days 1–2 — The Go Storage Engine Reference

### 1.1 Complete Implementation (`storage.go`)
Save this into `labs/week06/go/storage.go`:

```go
package main

import (
	"errors"
	"fmt"
	"sync"
)

var ErrKeyNotFound = errors.New("key not found")

// The Polymorphic Storage Interface
type Storage interface {
	Put(key uint64, value uint64) error
	Get(key uint64) (uint64, error)
}

// ------------------------------------------------------------------------
// IN-MEMORY STORAGE ENGINE
// ------------------------------------------------------------------------
type MemoryStorage struct {
	mu   sync.RWMutex
	data map[uint64]uint64
}

func NewMemoryStorage(initialCapacity int) *MemoryStorage {
	return &MemoryStorage{
		data: make(map[uint64]uint64, initialCapacity),
	}
}

// Direct concrete methods
func (m *MemoryStorage) Put(key uint64, value uint64) error {
	m.mu.Lock()
	m.data[key] = value
	m.mu.Unlock()
	return nil
}

func (m *MemoryStorage) Get(key uint64) (uint64, error) {
	m.mu.RLock()
	val, ok := m.data[key]
	m.mu.RUnlock()
	if !ok {
		return 0, ErrKeyNotFound
	}
	return val, nil
}

// ------------------------------------------------------------------------
// DIRECT VS INTERFACE HARNESSES
// ------------------------------------------------------------------------

// Direct concrete call (Compiler can potentially inline)
func DirectBatchInsert(mem *MemoryStorage, count uint64) {
	for i := uint64(0); i < count; i++ {
		_ = mem.Put(i, i*2)
	}
}

// Interface dispatch via runtime.iface fat pointer (2-word structure: itab + data)
func InterfaceBatchInsert(store Storage, count uint64) {
	for i := uint64(0); i < count; i++ {
		_ = store.Put(i, i*2)
	}
}
```

### 1.2 Go Benchmark Suite (`storage_test.go`)
Save this into `labs/week06/go/storage_test.go`:

```go
package main

import "testing"

const BatchSize = 1_000_000

func BenchmarkDirectStructCalls(b *testing.B) {
	mem := NewMemoryStorage(BatchSize)
	b.ResetTimer()
	b.ReportAllocs()

	for i := 0; i < b.N; i++ {
		DirectBatchInsert(mem, BatchSize)
	}
}

func BenchmarkInterfaceDispatch(b *testing.B) {
	mem := NewMemoryStorage(BatchSize)
	b.ResetTimer()
	b.ReportAllocs()

	for i := 0; i < b.N; i++ {
		// Passing 'mem' converts concrete pointer to Storage interface (runtime.iface)
		InterfaceBatchInsert(mem, BatchSize)
	}
}
```

Run the benchmark:
```bash
go test -bench=. -benchmem -benchtime=5s
```

---

## Part 2: Days 3–4 — Rust Static vs. Dynamic Dispatch Deep Dive

In Rust, the choice between static dispatch (`<S: StorageEngine>`) and dynamic dispatch (`Box<dyn StorageEngine>`) is explicit.

### 2.1 The Complete Rust Engine (`src/lib.rs`)

```rust
use std::collections::HashMap;

// The Trait Contract
pub trait StorageEngine {
    fn put(&mut self, key: u64, value: u64);
    fn get(&self, key: u64) -> Option<u64>;
}

// ------------------------------------------------------------------------
// IN-MEMORY STORAGE ENGINE
// ------------------------------------------------------------------------
pub struct MemoryEngine {
    data: HashMap<u64, u64>,
}

impl MemoryEngine {
    pub fn new(capacity: usize) -> Self {
        Self {
            data: HashMap::with_capacity(capacity),
        }
    }
}

impl StorageEngine for MemoryEngine {
    #[inline]
    fn put(&mut self, key: u64, value: u64) {
        self.data.insert(key, value);
    }

    #[inline]
    fn get(&self, key: u64) -> Option<u64> {
        self.data.get(&key).copied()
    }
}

// ------------------------------------------------------------------------
// PIPELINE 1: STATIC DISPATCH (Monomorphized Generics)
// ------------------------------------------------------------------------
// The compiler generates a specialized version of StaticDb for MemoryEngine.
// Zero vtables. Zero pointer indirection. Direct function calls or full inlining!
pub struct StaticDb<S: StorageEngine> {
    pub engine: S,
}

impl<S: StorageEngine> StaticDb<S> {
    pub fn new(engine: S) -> Self {
        Self { engine }
    }

    #[inline]
    pub fn run_batch_insert(&mut self, count: u64) {
        for i in 0..count {
            self.engine.put(i, i * 2);
        }
    }

    #[inline]
    pub fn run_batch_lookup(&self, count: u64) -> u64 {
        let mut sum = 0;
        for i in 0..count {
            if let Some(val) = self.engine.get(i) {
                sum += val;
            }
        }
        sum
    }
}

// ------------------------------------------------------------------------
// PIPELINE 2: DYNAMIC DISPATCH (Trait Objects / Fat Pointer Vtable)
// ------------------------------------------------------------------------
// 'dyn StorageEngine' is a 16-byte Fat Pointer:
// [ pointer to data: 8 bytes | pointer to vtable: 8 bytes ]
pub struct DynamicDb {
    pub engine: Box<dyn StorageEngine>,
}

impl DynamicDb {
    pub fn new(engine: Box<dyn StorageEngine>) -> Self {
        Self { engine }
    }

    pub fn run_batch_insert(&mut self, count: u64) {
        for i in 0..count {
            // Emits: call qword ptr [rax+offset] in assembly!
            self.engine.put(i, i * 2);
        }
    }

    pub fn run_batch_lookup(&self, count: u64) -> u64 {
        let mut sum = 0;
        for i in 0..count {
            if let Some(val) = self.engine.get(i) {
                sum += val;
            }
        }
        sum
    }
}
```

### 2.2 Criterion Microbenchmark Suite (`benches/dispatch_bench.rs`)

```rust
// File: benches/dispatch_bench.rs
use criterion::{black_box, criterion_group, criterion_main, Criterion};

#[path = "../src/lib.rs"]
mod storage;
use storage::{DynamicDb, MemoryEngine, StaticDb};

const BATCH_SIZE: u64 = 500_000;

fn bench_static_dispatch(c: &mut Criterion) {
    let mut group = c.benchmark_group("Static_vs_Dynamic_Dispatch");

    group.bench_function("Static_Monomorphized_Batch", |b| {
        b.iter(|| {
            let engine = MemoryEngine::new(BATCH_SIZE as usize);
            let mut db = StaticDb::new(engine);
            db.run_batch_insert(black_box(BATCH_SIZE));
            black_box(db.run_batch_lookup(black_box(BATCH_SIZE)))
        })
    });

    group.bench_function("Dynamic_TraitObject_Vtable_Batch", |b| {
        b.iter(|| {
            let engine = Box::new(MemoryEngine::new(BATCH_SIZE as usize));
            let mut db = DynamicDb::new(engine);
            db.run_batch_insert(black_box(BATCH_SIZE));
            black_box(db.run_batch_lookup(black_box(BATCH_SIZE)))
        })
    });

    group.finish();
}

criterion_group!(benches, bench_static_dispatch);
criterion_main!(benches);
```

Run the benchmark:
```bash
cargo bench
```

---

## Part 3: Assembly Disassembly Inspection

Use `objdump` or `cargo-asm` to physically inspect the compiled machine instructions.

```bash
# Install cargo-asm
cargo install cargo-asm

# Inspect the static pipeline assembly:
cargo asm storage_engine::StaticDb::run_batch_insert
```

### What You Will Observe in the Assembly:
1. **In `StaticDb` (Generics):**
   * There are **NO `call` instructions** inside the loop!
   * The compiler inlined `HashMap.insert` directly into the loop body.
   * Key and value registers are computed and stored directly into CPU registers (`RCX`, `RDX`), unrolled into SIMD or vector instructions.
2. **In `DynamicDb` (Trait Objects):**
   * Look at the loop interior:
     ```assembly
     mov   rax, qword ptr [rdi+8]   ; Load vtable address from fat pointer
     mov   rdi, qword ptr [rdi]     ; Load data address from fat pointer
     call  qword ptr [rax+24]       ; INDIRECT CALL to StorageEngine::put!
     ```
   * Every iteration executes an indirect call. The CPU pipeline must wait for memory address resolution, stalling branch prediction buffers.

---

## Part 4: Friday Mob Review & Defense Protocol

Gather the 5-engineer team. Project the disassembly output and Criterion latency graphs.

### 7 Mandatory Technical Defense Questions

#### 1. Why was `StaticDb` measurably faster than `DynamicDb` in our Rust benchmarks?
* **Expected Answer:** In `StaticDb`, monomorphization allowed the LLVM compiler to inline the method body of `MemoryEngine.put` directly into the loop, completely eliminating function call overhead, stack frame construction, and indirect jumps. In `DynamicDb`, every call requires dereferencing the vtable pointer and performing an indirect jump (`call [rax+24]`), which cannot be inlined and causes CPU branch target buffer misses.

#### 2. What is a "Fat Pointer" in Rust and Go, and what are its exact 2 words?
* **Expected Answer:** A fat pointer is a 16-byte (2-word) structure on 64-bit platforms:
  * In Rust (`&dyn Trait` or `Box<dyn Trait>`): Word 1 = pointer to the concrete data struct; Word 2 = pointer to the vtable.
  * In Go (`runtime.iface`): Word 1 = pointer to the `itab` (interface table with concrete type info and method pointers); Word 2 = pointer to the concrete data.

#### 3. What is the Object Safety rule in Rust, and why does a method returning `Self` prevent a trait from being used as `dyn Trait`?
* **Expected Answer:** Object safety requires that the size of all types and return values be known at compile time, and that the method can be dispatched via a vtable. If a method returns `Self`, the compiler does not know the concrete size or type of `Self` when hiding behind a `dyn Trait` fat pointer. Therefore, traits with methods returning `Self` cannot be made into trait objects.

#### 4. In Go, what happens under the hood when you assign a concrete `int` to an `any` or `interface{}`?
* **Expected Answer:** A primitive `int` is a value type. An interface value requires Word 2 to be a pointer to data. To satisfy this, the Go runtime must allocate memory on the heap (boxing), copy the `int` value into that heap location, and place its address into Word 2 of the interface, causing a heap allocation and GC tracking overhead.

#### 5. What is the "Code Bloat" trade-off of monomorphization in Rust?
* **Expected Answer:** Monomorphization duplicates the machine code of generic functions for every unique type parameter used (`StaticDb<MemoryEngine>`, `StaticDb<DiskEngine>`, etc.). While this yields maximum execution speed and inlining, having dozens of specialized implementations expands binary size, potentially causing instruction cache (I-Cache) thrashing in large codebases.

#### 6. In C#, why does casting a class to an interface not change the pointer size like in Go or Rust?
* **Expected Answer:** In C#, all class references are already pointers to managed heap objects, and every heap object already carries a 16-byte header containing a `MethodTable` pointer. The interface dispatch map is embedded directly inside the class's `MethodTable`. Therefore, casting to an interface keeps the pointer size at 8 bytes, relying on runtime interface dispatch stubs.

#### 7. When is Dynamic Dispatch (`Box<dyn Trait>`) preferred over Generics in production?
* **Expected Answer:**
  1. When handling heterogeneous collections (e.g., a `Vec<Box<dyn StorageEngine>>` containing different backends simultaneously).
  2. When the concrete type is determined at runtime (e.g., chosen dynamically based on configuration files or command-line flags).
  3. When reducing compilation times and preventing binary code bloat in large applications.

---

## Lab Sign-off Checklist

Each engineer must verify and sign off:
* [ ] **Fat Pointer Literacy:** I can draw the 16-byte layout of a Go `runtime.iface` and a Rust `dyn Trait`.
* [ ] **Object Safety Comprehension:** I can explain why generic methods and `Self` returns violate Rust Object Safety.
* [ ] **Assembly Proof:** I have inspected the assembly of static vs dynamic dispatch and identified the indirect `call [rax]` instruction.
* [ ] **Benchmark Competency:** I have executed the Criterion/Go benchmark and measured the latency delta of virtual dispatch.
* [ ] **Architectural Judgement:** I can articulate when to choose monomorphized generics vs trait objects in production systems.
