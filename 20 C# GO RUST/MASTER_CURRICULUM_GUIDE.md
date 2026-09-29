# Polyglot Engineering Mastery: C#, Go & Rust (35-Week Deep Dive)
### From Senior .NET Engineers to Elite Polyglot Systems Architects

Welcome to the comprehensive 35-Week Systems & Backend Engineering Curriculum designed specifically for senior **C# (.NET)** engineers transitioning into **Go** and **Rust**.

---

## 🎯 Curriculum Vision & Philosophy

Most language tutorials teach syntax. This curriculum teaches **systems fundamentals, runtime architecture, memory mechanics, hardware sympathy, and idiom translation**.

Instead of learning Go or Rust in isolation, every single week investigates a fundamental computer science and engineering challenge across all three languages:

1. **C# Baseline:** How you solve it today on the .NET CLR (the mental anchor).
2. **Go Implementation:** How Go solves it with simplicity, CSP concurrency, and minimal runtime.
3. **Rust Implementation:** How Rust solves it with zero-cost abstractions, affine type ownership, and compile-time safety.

---

## 📂 Folder & Document Structure

Each week has a dedicated directory containing three comprehensive documents:

```
Week-XX-<Topic-Name>/
├── 01_CONCEPTUAL_DEEP_DIVE.md    # Theoretical foundations, runtime mechanics, memory models
├── 02_CODE_COMPARISON_ROSETTA.md # Side-by-side production-grade code implementations
└── 03_HANDS_ON_LAB_EXERCISE.md   # Practical team lab, edge cases, benchmarking & review questions
```

---

## 🗺️ 35-Week Master Architecture

### Pillar 1: Memory, Compilation & Foundational Semantics (Weeks 1–4)
* **[Week 01: Toolchain, Compilers & Runtime Execution Models](./Week-01-Toolchain-Compilers-Runtime/)**
  * Bytecode & JIT (CLR) vs. Native Go Runtime vs. LLVM Static Compilation.
* **[Week 02: Memory Anatomy: Stack, Heap & Value vs. Reference Semantics](./Week-02-Memory-Stack-Heap-Value-Ref/)**
  * Spatial locality, cache lines, pointer indirection, and heap allocation costs.
* **[Week 03: Memory Reclamation: Escape Analysis vs. The Borrow Checker](./Week-03-Memory-Escape-Analysis-Borrow-Checker/)**
  * Generational GC vs. Go Tri-Color GC & Escape Analysis vs. Rust Affine Ownership & Lifetimes.
* **[Week 04: Advanced Pointer Mechanics & Smart Pointers](./Week-04-Pointers-And-Smart-Pointers/)**
  * `Span<T>` & `unsafe` vs. Go pointer mechanics vs. `Box<T>`, `Rc<T>`, `RefCell<T>`, RAII `Drop`.

### Pillar 2: Type Systems, Data Modeling & Error Philosophy (Weeks 5–8)
* **[Week 05: Composition Over Inheritance](./Week-05-Composition-Over-Inheritance/)**
  * OOP inheritance trees vs. Go struct embedding vs. Rust structs & tuple structs.
* **[Week 06: Polymorphism: Nominal vs. Structural vs. Trait Bounds](./Week-06-Polymorphism-Interfaces-Traits/)**
  * C# nominal interfaces vs. Go duck-typing structural interfaces vs. Rust traits & generics.
* **[Week 07: Algebraic Data Types & Exhaustive Pattern Matching](./Week-07-Algebraic-Types-Pattern-Matching/)**
  * C# records/switch vs. Go type assertions/switches vs. Rust enums with data (sum types).
* **[Week 08: Error Architecture: Exceptions vs. Values vs. Monads](./Week-08-Error-Architecture/)**
  * C# exceptions vs. Go `(T, error)` tuples & wrapping vs. Rust `Result<T, E>`, `Option<T>`, and `?`.

### Pillar 3: Data Structures, Collections & Zero-Cost Abstractions (Weeks 9–10)
* **[Week 09: Slices, Vectors & Memory Allocation Dynamics](./Week-09-Slices-Vectors-Allocation/)**
  * `List<T>` vs. Go slice headers & backing arrays vs. Rust `Vec<T>` capacity & reallocation.
* **[Week 10: Functional Pipelines & Zero-Cost Iteration](./Week-10-Iterators-Closures-Pipelines/)**
  * LINQ lazy evaluation vs. Go procedural range loops vs. Rust zero-cost iterators & closures.

### Pillar 4: Concurrency, Threading & Asynchronous Runtimes (Weeks 11–14)
* **[Week 11: Multi-Threading & Shared-Memory Synchronization](./Week-11-Threading-Synchronization/)**
  * ThreadPool & locks vs. Go `sync.Mutex` & race detector vs. Rust `Send`/`Sync` & `Arc<Mutex<T>>`.
* **[Week 12: Communicating Sequential Processes (CSP) & Message Passing](./Week-12-CSP-Channels-Message-Passing/)**
  * `System.Threading.Channels` vs. Go Goroutines & Channels (`select`) vs. Rust crossbeam channels.
* **[Week 13: Asynchronous Execution: The G-M-P Scheduler vs. Tokio Futures](./Week-13-Async-Runtimes-Futures/)**
  * C# TAP `Task` state machines vs. Go M:N runtime scheduler vs. Rust cooperative futures & Tokio.
* **[Week 14: Production Concurrency Patterns: Worker Pools & Cancellation](./Week-14-Worker-Pools-Cancellation/)**
  * `CancellationToken` vs. Go `context.Context` vs. Rust Tokio worker pools & cancellation tokens.

### Pillar 5: Enterprise Architecture, Persistence & Networking (Weeks 15–18)
* **[Week 15: HTTP Protocol, Routing & Middleware Chains](./Week-15-HTTP-Routing-Middleware/)**
  * ASP.NET Core middleware vs. Go `net/http` & Chi vs. Rust `Axum` & Tower services.
* **[Week 16: Clean Architecture & Dependency Inversion Without Magic](./Week-16-Clean-Architecture-DI/)**
  * C# reflection IoC containers vs. Go explicit struct wiring vs. Rust trait-based dependency injection.
* **[Week 17: Database Persistence, Transactions & Connection Pools](./Week-17-Database-Persistence-Transactions/)**
  * EF Core & Dapper vs. Go `database/sql` & `pgx` vs. Rust `SQLx` compile-time verified queries.
* **[Week 18: Asynchronous Messaging & Event-Driven Integration](./Week-18-Async-Messaging-Event-Driven/)**
  * MassTransit vs. Go message consumer loops vs. Rust async stream consumers (RabbitMQ/Kafka).

### Pillar 6: Systems Performance, Quality Engineering & Capstone (Weeks 19–24)
* **[Week 19: Testing Paradigms, Quality Gates & Fuzzing](./Week-19-Testing-Quality-Fuzzing/)**
  * xUnit/Moq vs. Go table-driven tests & `httptest` vs. Rust `cargo test`, `mockall` & `proptest`.
* **[Week 20: Performance Profiling, Memory Optimization & Benchmarking](./Week-20-Profiling-Memory-Benchmarking/)**
  * BenchmarkDotNet & dotnet-trace vs. Go `pprof` & allocation tuning vs. Rust `criterion` & flamegraphs.
* **[Week 21: High-Performance RPC & Serialization (gRPC & Protocol Buffers)](./Week-21-gRPC-Protobuf-RPC/)**
  * `Grpc.AspNetCore` vs. Go `grpc-go` vs. Rust `tonic`.
* **[Week 22: Production Observability & Security Engineering](./Week-22-Observability-Security/)**
  * Serilog & OpenTelemetry vs. Go `log/slog` & OTel vs. Rust `tracing` & crypto safety.
* **[Week 23: Enterprise Capstone Implementation (The Polyglot Platform)](./Week-23-Capstone-Implementation/)**
  * Building the Polyglot Order & Financial Processing Platform.
* **[Week 24: Capstone Hardening, Comparative Benchmark & Group Defense](./Week-24-Capstone-Defense/)**
  * End-to-end load testing, profiling, latency comparison, and architectural review.

---

### 🚀 Pillar 7: Advanced Systems Internals & Hardware Sympathy (Weeks 25–28)
* **[Week 25: Low-Level Memory Allocators, TCMalloc & Arena Architecture](./Week-25-Memory-Allocators-Arenas/)**
  * Heap allocator internals, TCMalloc 67 size classes, `mcache`/`mcentral`/`mheap` in Go, .NET SOH/LOH/POH, Rust pluggable allocators (`jemalloc`, `mimalloc`), Arena/Bump allocation (`bumpalo`), zero-fragmentation parsing.
* **[Week 26: The Async Engine Deep Dive: Self-Referential Structs, `Pin<P>`, & Wakers](./Week-26-Async-Internals-Pin-Wakers/)**
  * Why async futures are self-referential state machines, physical memory moves causing dangling pointers, `Pin<P>` and `Unpin` contract, the Waker contract in Tokio, Go G-M-P asynchronous signal preemption (`SIGURG`), cooperative starvation, task stealing.
* **[Week 27: Hardware SIMD Vectorization & Zero-Cost FFI Systems Interop](./Week-27-SIMD-Vectorization-FFI/)**
  * SIMD vector registers (YMM/ZMM, 256/512-bit), AVX2/AVX-512, auto-vectorization, C# `System.Runtime.Intrinsics`, Go Plan 9 assembly (`.s` files) vs Rust `std::arch` & `std::simd`. FFI internals: P/Invoke vs `cgo` stack switching cost (50-100ns context switch) vs Rust zero-cost `extern "C"`.
* **[Week 28: Lock-Free Systems, Memory Barriers & Hardware Atomic Orderings](./Week-28-Lock-Free-Memory-Models/)**
  * Cache coherence, instruction reordering, store buffers, MESI, Acquire/Release hardware semantics on x86-64 vs ARM64, Sequential Consistency vs Relaxed. Single Producer Multi Consumer (SPMC) ring buffer implementation.

### 🌐 Pillar 8: OS Primitives, Kernel Bypass & High-Performance Networking (Weeks 29–32)
* **[Week 29: Linux Systems Programming, POSIX Signals & Process Lifecycle](./Week-29-Linux-Systems-Signals-Processes/)**
  * Linux virtual memory mappings (`mmap`, `madvise`, `MADV_DONTNEED`), file descriptors, epoll event loops, POSIX signal handling across runtimes (Go's signal trampoline, Rust `signal-hook`, .NET PosixSignalRegistration), daemonization, graceful termination.
* **[Week 30: Zero-Copy I/O, Kernel Bypass & High-Performance Networking](./Week-30-Zero-Copy-Networking-IO-Uring/)**
  * Standard VFS read/write syscall tax, page cache copying, `sendfile`, `splice`, Linux `io_uring` ring buffer architecture (Submission Queue / Completion Queue) vs Go netpoller vs Windows IOCP. Benchmarking zero-copy packet ingestion.
* **[Week 31: Storage Engines: LSM-Trees, Write-Ahead Logs (WAL) & B-Trees](./Week-31-Storage-Engines-LSM-WAL/)**
  * Building a high-performance key-value storage engine from scratch. Write-Ahead Logging (WAL) with `fsync` vs `fdatasync`, MemTable (in-memory skiplist/red-black tree), SSTables (Sorted String Tables) on disk with Bloom filters and sparse index. Comparing implementations in C#, Go, and Rust.
* **[Week 32: Distributed Consensus & State Machine Replication (Raft Protocol)](./Week-32-Distributed-Consensus-Raft/)**
  * Distributed systems engineering, network partitions, leader election, log replication, commit index, RPC heartbeats, deterministic state machine replication. Building a lightweight 3-node Raft cluster in Go and Rust.

### 🏆 Pillar 9: Production Mastery, High-Frequency Systems & Final Enterprise Defense (Weeks 33–35)
* **[Week 33: High-Frequency Systems & Low-Latency FinTech Architecture](./Week-33-Low-Latency-FinTech-Systems/)**
  * Sub-microsecond engineering, core pinning / thread affinity (`sched_setaffinity`), busy-spin polling vs OS sleep, eliminating context switches, mechanical sympathy, zero-allocation order book matching engine.
* **[Week 34: Security Hardening, Cryptographic Memory & Memory Forensics](./Week-34-Security-Memory-Forensics/)**
  * Timing attacks in cryptography (constant-time comparisons), clearing secrets from RAM (`zeroize`, `CryptographicOperations.ZeroMemory`), buffer overflow defenses, AddressSanitizer (ASan), MemorySanitizer (MSan), Miri undefined behavior interpreter, dynamic fuzzing.
* **[Week 35: The Grand Polyglot Architecture & 35-Week Master Engineering Defense](./Week-35-Master-Engineering-Defense/)**
  * Production readiness review for modern systems engineers. Comprehensive architectural comparison of C# vs Go vs Rust across 35 criteria. Complete 100-question technical defense quiz. Career transition blueprint from Senior .NET Engineer to Principal Polyglot Systems Architect.

---

## 👥 How Your 5-Person Team Should Work Together

1. **Monday (Concept & Theory):** Everyone reads `01_CONCEPTUAL_DEEP_DIVE.md`.
2. **Tuesday–Thursday (Code & Lab):** Study `02_CODE_COMPARISON_ROSETTA.md` and complete `03_HANDS_ON_LAB_EXERCISE.md`.
3. **Friday (Mob Review & Defense):** Host a 1-hour team meeting:
   * Walk through each member's solutions.
   * Review compiler errors (especially borrow checker struggles).
   * Benchmark performance and compare memory profiles across C#, Go, and Rust.
