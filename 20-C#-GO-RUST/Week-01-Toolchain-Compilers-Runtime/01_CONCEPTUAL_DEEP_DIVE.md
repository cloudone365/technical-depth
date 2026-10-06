# Week 01 · Toolchain, Compilers & Runtime Execution Models
### Pillar 1: Memory, Compilation & Foundational Semantics

> **Reading time:** ~45 minutes  
> **This week's big question:** When you type `dotnet run`, `go run`, or `cargo run` — what actually happens between your source code and electrons moving on silicon?

---

## Why This Week Matters for Your Career Transition

You have spent years on .NET. You understand that writing `new Customer()` puts an object on the heap. You know that `async/await` generates a state machine. You understand NuGet packages and `.csproj` project files.

Here is the uncomfortable truth: almost everything the CLR does for you is **hidden infrastructure**. When you move to Go and Rust, that infrastructure either becomes your responsibility, or it becomes the compiler's responsibility in a completely different way. If you do not understand the execution model, you will spend months debugging performance issues you do not understand, writing Go code that "looks right but causes 10x more heap allocations than necessary", or fighting Rust's borrow checker without knowing why it exists.

This week you will understand the **entire pipeline from source code to CPU instruction** in all three languages. Not as a trivia exercise — but because it changes how you write code.

---

## Part 1: The C# Execution Model (Your Baseline)

Before we can map Go and Rust onto your mental model, we need to precisely articulate what the CLR actually does.

### What Happens When You Run `dotnet run`

When you run a .NET application, at minimum six distinct systems activate:

**Step 1 — Compilation to CIL (Roslyn Compiler)**

Your `.cs` files are compiled by the Roslyn compiler into **Common Intermediate Language (CIL)**, also called MSIL. CIL is not machine code. It is a stack-based virtual machine instruction set — think of it as the "universal language" that the CLR understands regardless of whether you're on x64, ARM64, or any other architecture.

```
// Your C# code:
int x = 5 + 3;

// Compiled CIL (simplified IL disassembly):
ldc.i4.5       // Push integer constant 5 onto the eval stack
ldc.i4.3       // Push integer constant 3 onto the eval stack
add            // Pop both, add, push result
stloc.0        // Store result into local variable slot 0
```

**Step 2 — The CLR Host Process Starts**

When you execute a .NET application, the OS loads `dotnet.exe` (or the self-contained host). This process initialises the **Common Language Runtime** — a sophisticated managed execution environment that provides:
- A thread pool and thread management system
- A garbage collector with write barriers for tracking inter-generational references
- A type system with runtime type metadata (reflection)
- Exception handling infrastructure with structured error unwinding
- JIT compilation infrastructure

This is significant: **even before your `main()` runs, the CLR has already consumed 20–40 MB of RAM** initialising these systems. That is the price of the managed runtime.

**Step 3 — Tiered JIT Compilation (RyuJIT)**

When a .NET method is first called, it is in CIL form — the CLR has not yet produced machine code for it. The **RyuJIT compiler** handles this through **Tiered Compilation**:

- **Tier 0 (QuickJIT):** The first time a method is called, RyuJIT compiles it rapidly with minimal optimisation. This gets code executing immediately, but the machine code quality is low.
- **Tier 1 (Full Optimisation):** The CLR tracks which methods are called frequently ("hot methods"). After a threshold, it recompiles those methods with aggressive optimisations: inlining, loop unrolling, vectorisation, devirtualisation.

```
Timeline of a .NET web request handler:
Request 1:  Method compiled at Tier 0 → executes at 60% optimal speed
Request 50: CLR detects hot method → schedules recompilation
Request 51: Method now running at Tier 1 → full speed
```

> **Why does this matter?** Your application literally runs faster the longer it is alive. This is why .NET applications often show high latency during "warm-up" and then stabilise. This warm-up cost is **completely absent in Go and Rust** because there is no JIT — the binary is already fully optimised before it ever executes.

**Step 4 — The Garbage Collector**

The CLR's GC uses a **generational model** because research shows most objects die young:

- **Gen 0:** Small, short-lived allocations (local variables, temporary buffers). Collected frequently and extremely quickly — typically under 1 millisecond. Most objects die here.
- **Gen 1:** Objects that survived Gen 0 collection. Acts as a buffer between Gen 0 and Gen 2.
- **Gen 2:** Long-lived objects (application-scope state, large caches). Collected infrequently but expensively — can pause for 10–100ms in worst cases.
- **Large Object Heap (LOH):** Objects larger than 85,000 bytes. Expensive to collect, not compacted by default.

The GC uses **write barriers** — tiny pieces of code injected by the JIT around every reference assignment — to track which old objects point to new objects. This overhead is always present and is why high-allocation hot paths in C# are a performance problem even if objects are collected quickly.

---

## Part 2: The Go Execution Model (Your First Transition)

### The Design Philosophy That Explains Everything

Go was designed in 2007 at Google by Rob Pike, Ken Thompson (co-creator of Unix and C), and Robert Griesemer. The problem they were solving: large Google codebases in C++ took 30–45 minutes to compile, making developer iteration painfully slow. They also needed massive concurrency for distributed systems without the complexity of manual memory management.

The constraints they imposed:
1. **Compilation must be fast** — not JIT fast (at runtime), but build-time fast. A million lines of Go should compile in seconds.
2. **Deployment must be simple** — one binary, no runtime installation required.
3. **Concurrency must be built-in** — not bolted on via libraries.
4. **Memory safety without explicit allocation/deallocation** — a GC, but a low-latency one.

These constraints explain *every* design decision you will encounter.

### What Happens When You Run `go run main.go`

**Step 1 — Direct Compilation to Machine Code**

Unlike C#, Go does not compile to an intermediate format. The `gc` compiler (the official Go compiler, not "garbage collector") compiles your source directly to **machine code for your target architecture**. It generates x64, ARM64, MIPS, etc. assembly directly.

The Go compiler achieves its legendary speed through deliberate architectural choices:
- **No type inference cycles:** Go's type system was deliberately limited to enable single-pass compilation
- **No header files:** Dependencies are analysed through their compiled packages
- **Unused import detection:** The compiler immediately rejects unused imports, keeping the dependency tree minimal and compile times predictable

**Step 2 — The Embedded Runtime**

Here is the key difference from C#: Go does not have an external VM or host process. Instead, the Go compiler **links a runtime library directly into your binary**.

When you run a Go binary, what actually starts up is:
```
OS loads binary
  → Go runtime initialises (bootstrap code in assembly)
    → Heap allocator configured
    → Goroutine scheduler initialised (M:N thread scheduler)
    → GC initialised and background goroutines started
    → Your main() goroutine starts
```

The Go runtime is approximately 300-500KB of code embedded in every Go binary. This is why a simple "Hello, World" in Go produces a ~2MB binary — that runtime is always there.

**Step 3 — The Go Runtime Responsibilities**

The Go runtime manages four critical systems:

**(a) The M:N Goroutine Scheduler (G-M-P model)**

Instead of creating one OS thread per concurrent operation (which would exhaust system resources at scale), Go uses a sophisticated **M:N scheduler**:

- **G (Goroutine):** Your concurrent tasks. Start at 2KB of stack. Can grow dynamically to gigabytes.
- **M (Machine):** OS threads. Typically `runtime.NumCPU()` of them.
- **P (Processor context):** Logical processors. Each M runs one G at a time through a P context.

This means 100,000 goroutines can be running "concurrently" on, say, 8 OS threads. The Go scheduler performs **cooperative + preemptive** goroutine scheduling, deciding which goroutine runs on which OS thread at which time.

**(b) The Tri-Color Mark-Sweep GC**

Go's GC is designed differently from .NET's generational GC. It uses a **concurrent tri-color mark-sweep** algorithm that runs *mostly concurrently with your program*:

- **White:** Objects not yet visited (candidates for collection)
- **Grey:** Objects visited but whose children haven't been scanned
- **Black:** Objects fully scanned (definitely alive)

The GC cycles between two stop-the-world pauses that are typically **under 500 microseconds** (0.5ms) even on large heaps. It trades slightly higher memory overhead for dramatically lower pause times compared to generational GC.

**(c) Stack Management**

In C#, each thread gets a fixed 1MB (by default) or 4MB stack. Exceeding it causes a `StackOverflowException`. Go goroutines start with a **2KB stack** and can grow **dynamically** by allocating a larger stack and copying the contents. This is why you can spawn 100,000 goroutines — they start tiny.

**(d) Network Poller**

The Go runtime includes an integrated network poller that uses OS-level asynchronous I/O (`epoll` on Linux, `kqueue` on macOS) to handle network operations without blocking OS threads. When a goroutine reads from a network connection, it parks on the network poller and another goroutine runs on that OS thread. This is the mechanism that enables Go's effortless concurrency for I/O-heavy workloads.

### What Happens When You Run `go build`

```
Source files (.go)
  → Lexer/Parser → AST (Abstract Syntax Tree)
  → Type checker
  → SSA (Static Single Assignment) form
  → Escape Analysis (determines stack vs. heap allocation)
  → Code generation (machine code for target architecture)
  → Linker (links all packages + runtime into single binary)
```

Note: **Escape Analysis** is listed here because it happens at compile time, not runtime. The compiler analyses each variable to determine if it can safely live on the goroutine stack, or if its lifetime might exceed the stack frame (in which case it moves to the heap). We will study this deeply in Week 3.

---

## Part 3: The Rust Execution Model (Your Second Transition)

### The Design Philosophy That Explains Everything

Rust was created by Mozilla engineer Graydon Hoare starting in 2006, with Mozilla sponsorship beginning in 2009. The problem: Firefox needed to be rewritten to handle modern parallel hardware safely. C++ gave performance but was riddled with memory bugs. Managed languages (Java, C#) gave safety but unacceptable latency and memory overhead for a browser engine.

The goal Mozilla set: **the performance of C++, the safety of a managed language, no garbage collector**.

This sounds impossible. The mechanism that makes it possible is the **Borrow Checker** — a static analysis system built into the compiler that proves at compile time that no memory safety violations can occur. The borrow checker is not a runtime check, not a GC, not a reference counter. It is a **mathematical proof system** that operates entirely during compilation.

Understanding this is the key to understanding Rust. Everything that feels difficult or verbose about Rust exists because you are writing code that the compiler can mathematically prove is safe.

### What Happens When You Run `cargo run`

**Step 1 — Cargo (The Build System and Package Manager)**

Cargo is not just a package manager. It is a complete build orchestration system. When you run `cargo run`:

1. Reads `Cargo.toml` to identify dependencies
2. Downloads and compiles all dependencies (first run only; cached thereafter)
3. Invokes `rustc` (the Rust compiler) with the correct flags
4. Links the final binary
5. Executes the binary

Unlike NuGet (which downloads pre-compiled DLLs) and Go modules (which download pre-compiled packages), **Cargo compiles all dependencies from source**. This is why the first `cargo build` of a project with dependencies can take minutes. Subsequent builds are fast because Cargo intelligently caches compiled artifacts.

**Step 2 — The rustc Compilation Pipeline**

The Rust compiler has a sophisticated multi-stage pipeline:

```
Source Code (.rs)
  → Lexer/Parser → AST
  → Macro expansion (procedural and declarative macros resolve here)
  → Name resolution and type inference
  → HIR (High-level Intermediate Representation)
  → Type checking
  → MIR (Mid-level Intermediate Representation) ← BORROW CHECKER LIVES HERE
  → MIR optimisations (inlining, dead code elimination)
  → LLVM IR (Low Level Virtual Machine Intermediate Representation)
  → LLVM optimisation passes (dozens of aggressive passes in release mode)
  → Machine code generation for target platform
```

The borrow checker operating on MIR is why Rust compilation is slower than Go: the compiler is doing significantly more work proving safety properties.

**Step 3 — The LLVM Backend (Why Rust Can Match C Performance)**

LLVM is a production-grade compiler infrastructure used by C, C++, Swift, Julia, and many others. When Rust generates LLVM IR, it hands off to the same optimisation infrastructure that compiles production C/C++ code at companies like Google, Apple, and Meta.

In debug mode (`cargo build`): LLVM applies minimal optimisations. Compile fast, run slow.
In release mode (`cargo build --release`): LLVM applies aggressive optimisations:
- **Inlining:** Function call overhead eliminated by inserting callee code directly at call site
- **Vectorisation:** Loops over arrays converted to SIMD (Single Instruction, Multiple Data) instructions that process 4/8/16 values simultaneously
- **Dead code elimination:** Code that can never execute is removed entirely
- **Constant folding:** Expressions with known values computed at compile time
- **Loop unrolling:** Loop overhead reduced by expanding iterations

> **The practical implication:** A Rust release binary is often **10x to 100x faster** than a Rust debug binary. Always benchmark with `--release`. The difference between Go and Rust in benchmarks typically comes from whether LLVM vectorised a particular loop.

**Step 4 — Zero Runtime (What This Actually Means)**

When a Rust binary starts, the OS:
1. Loads the ELF binary into memory
2. Jumps to `_start` (the C runtime initialisation stub, `crt0`)
3. `crt0` initialises the stack, heap allocator, and any global constructors
4. `crt0` calls `main()`
5. Your Rust `main()` runs

That's it. There is no goroutine scheduler. There is no garbage collector. There is no thread pool daemon. There is no JIT compiler. The **total startup overhead is microseconds**, not milliseconds.

Memory cleanup doesn't happen via a periodic GC cycle. Instead, the Rust compiler inserts `drop()` calls (equivalent to destructor calls) directly into the machine code at the exact points where values exit scope. These are statically determined at compile time. There is no runtime bookkeeping.

This is why Rust is used for operating systems, device drivers, game engines, and WebAssembly — environments where a garbage collector is simply not acceptable.

---

## Part 4: The Mental Model Transition — Concrete Differences

These are the concrete conceptual changes a C# developer must make:

### 1. From "Publish and Install Runtime" to "Copy One Binary"

```bash
# C# deployment (self-contained, single-file):
dotnet publish -r linux-x64 --self-contained -p:PublishSingleFile=true
# Result: ~50-70MB single file (includes .NET runtime)

# Go deployment:
go build -ldflags="-s -w" -o myapp main.go
# Result: 3-8MB single binary (includes Go runtime)
# scp myapp user@server:/usr/local/bin/myapp — Done.

# Rust deployment:
cargo build --release
# Result: 300KB - 5MB depending on dependencies
# scp target/release/myapp user@server:/usr/local/bin/myapp — Done.
```

### 2. From NuGet Central Registry to Distributed and Crates.io

```
C#:  PM> Install-Package Newtonsoft.Json
     → Downloads DLL from nuget.org
     → Your project references the pre-compiled DLL

Go:  import "github.com/tidwall/gjson"
     go get github.com/tidwall/gjson@v1.17.0
     → Downloads source from GitHub
     → Compiled directly into your binary

Rust: [dependencies]
      serde = { version = "1.0", features = ["derive"] }
      → Downloads source from crates.io
      → Compiled from source, monomorphized into your binary
```

### 3. From "Debug/Release is a Minor Flag" to "Release Changes Everything"

```
C# Debug vs Release:
  → JIT still warms up either way
  → Difference: 10-20% performance typically

Go (no separate debug/release for most purposes):
  go build           # Same optimisation level
  go build -gcflags="-N -l"  # Disable optimisations (for debugging with Delve)

Rust Debug vs Release:
  cargo build         → Debug: No LLVM optimisation. Includes overflow checks. SLOW.
  cargo build --release → Release: Full LLVM optimisation passes. Overflow checks OFF. FAST.
  Typical difference: 10x to 100x faster in release mode
```

### 4. From Hidden GC to Deliberate Allocation

In C#, writing:
```csharp
var results = users.Where(u => u.IsActive).Select(u => u.Name).ToList();
```
This allocates: a `Where` enumerator, a `Select` enumerator, intermediate state, and the final `List<string>`. The GC will clean up the intermediaries. You don't think about it.

In Go, you start thinking about whether the lambda escapes to the heap, whether the slice grows and reallocates, and what the GC cost of intermediate allocations is.

In Rust, lazy iterators produce **zero intermediate allocations**. The above translates to:
```rust
let results: Vec<&str> = users
    .iter()
    .filter(|u| u.is_active)
    .map(|u| u.name.as_str())
    .collect();
```
The `filter().map()` chain is zero-cost — the compiler fuses it into a single loop with no intermediate heap allocations. The only allocation is the final `Vec`.

---

## Part 5: Common Misconceptions to Unlearn

**Misconception 1: "Go is compiled but slower than C# because it has a GC"**

Not exactly. Go's tri-color GC is highly optimised for low-pause operation, and Go's simple code model allows the compiler to make very predictable optimisations. A well-written Go service typically outperforms a well-written C# service in raw throughput on CPU-bound workloads, while having lower and more predictable latency due to shorter GC pauses.

**Misconception 2: "Rust is fast because it has no GC"**

Partially correct. Rust is fast because (a) no GC pauses, (b) zero-cost abstractions, (c) aggressive LLVM optimisation, and (d) the ownership system enables the compiler to make optimisations that would be unsafe in languages with aliasing. The absence of a GC removes a source of latency spikes but is not the primary reason Rust executes quickly.

**Misconception 3: "I need to manually free memory in Rust like in C++"**

Absolutely not. You never call `free()` in safe Rust. The compiler inserts all cleanup code automatically via the `Drop` trait. The difference from a GC is *when* cleanup happens: deterministically at scope exit (Rust) vs. non-deterministically during a GC cycle (C#/Go).

**Misconception 4: "Go binaries are large because they include the runtime"**

Relative to what? A Go "Hello World" binary is 2MB. A bare-minimum C# self-contained binary is 60MB+. A Rust binary is 400KB. For actual production services with real logic, the differences narrow considerably — but Go is genuinely more compact than .NET in deployment footprint.

---

## Summary: The Three Runtime Contracts

After this week, you should be able to articulate the contract each language makes with you:

| | C# | Go | Rust |
|---|---|---|---|
| **What you give up** | Control over when memory is freed. Warm-up latency. Runtime installation on servers. | Control over which code is executed at startup. Slightly higher memory baseline than Rust. | The ability to compile in seconds. The ability to write code that the compiler can't prove is safe without `unsafe`. |
| **What you gain** | Maximum developer velocity. Rich reflection and metaprogramming. Familiar OOP patterns. | Simple, readable code. Fast compilation. Excellent concurrency. Small deployment. | Maximum runtime performance. Zero GC pauses. Compile-time memory safety. Smallest deployment. |
| **The execution contract** | Your code runs inside a managed CLR sandbox. The CLR handles everything, including killing your app cleanly if needed. | Your code runs as goroutines inside a lightweight runtime. The runtime makes scheduling decisions for you. | Your code is the runtime. What you write is exactly what runs. |
