# Week 01 · Code Comparison Rosetta Stone
### Project: `sysdiag` — A Cross-Language System Diagnostic CLI

> **Goal:** Build the exact same CLI tool in C#, Go, and Rust. Study the differences not just in syntax but in *philosophy, project structure, module conventions, and runtime introspection capabilities*.
>
> **Expected output:** Every implementation prints identical information. The journey to get there teaches you everything about Week 01's theory.

---

## Before You Write Any Code: Project Setup Side-by-Side

One of the most jarring early differences when switching from C# is that Go and Rust have strong, **enforced** conventions about project layout that are not optional. Understanding these before you start saves hours of confusion.

### C# Project Initialisation

```bash
mkdir sysdiag-csharp && cd sysdiag-csharp
dotnet new console --name SysDiag
```

Produces:
```
SysDiag/
├── SysDiag.csproj       ← Project metadata, SDK version, NuGet dependencies
├── Program.cs            ← Entry point
└── obj/                  ← Build intermediates (never check in)
```

The `.csproj` file is XML and controls the entire build. The SDK (`Microsoft.NET.Sdk`) defines implicit using statements, target framework, and build behaviour. NuGet packages are added as `<PackageReference>` elements.

### Go Project Initialisation

```bash
mkdir sysdiag-go && cd sysdiag-go
go mod init sysdiag
```

Produces:
```
sysdiag-go/
└── go.mod               ← Module declaration and dependency versions
```

You create `main.go` yourself. Go is deliberately minimal: there is no "project template". The convention is:
- A directory with `go.mod` is a **module**
- Files in the same directory belong to the same **package**
- The package named `main` with a `func main()` is the entry point
- Go **enforces** that every imported package is used (unused imports are compile errors, not warnings)

Go dependencies are specified by **URL** in `go.mod`:
```
module sysdiag

go 1.22

require (
    github.com/google/uuid v1.5.0  // Full GitHub URL — no central registry
)
```

### Rust Project Initialisation

```bash
cargo new sysdiag-rust
```

Produces:
```
sysdiag-rust/
├── Cargo.toml            ← Package metadata and dependencies (TOML format)
├── Cargo.lock            ← Exact locked versions of ALL transitive dependencies
└── src/
    └── main.rs           ← Entry point (enforced location by convention)
```

`Cargo.lock` is critical and C# has no equivalent by default. It records the exact version hash of every crate (package), transitive dependency, and their dependencies. Checking in `Cargo.lock` guarantees that every team member and every CI run builds with **byte-for-byte identical** dependency code. NuGet's package restore does not provide this guarantee by default.

```toml
[package]
name = "sysdiag"
version = "0.1.0"
edition = "2021"          # Rust editions: 2015, 2018, 2021 (like C# language versions)

[dependencies]
# Empty for now — we use only the standard library this week
```

---

## The Complete Implementation: All Three Languages

### Implementation 1: C# (.NET 8)

**`SysDiag/Program.cs`**

```csharp
using System.Diagnostics;
using System.Runtime.InteropServices;

// C# 9+ top-level statements — no class/method boilerplate required
var sw = Stopwatch.StartNew();

// --- Argument Handling ---
// In C#, args is provided by the runtime before Main() executes
string environment = args.Length > 0 ? args[0] : "LOCAL";

PrintHeader($"C# / .NET — System Diagnostics [{environment}]");

// --- Section 1: Runtime & Framework Identity ---
PrintSection("Runtime Identity");
Print("Framework",    RuntimeInformation.FrameworkDescription);   // e.g., ".NET 8.0.5"
Print("OS",           RuntimeInformation.OSDescription);          // e.g., "Linux 6.5.0 ..."
Print("Architecture", RuntimeInformation.ProcessArchitecture.ToString()); // X64, Arm64, etc.
Print("OS Platform",  GetOSPlatform());

// --- Section 2: Process & CPU ---
PrintSection("Process & CPU");
Print("Process ID",     Environment.ProcessId.ToString());
Print("Logical Cores",  Environment.ProcessorCount.ToString());
Print("Machine Name",   Environment.MachineName);
Print("64-bit Process", Environment.Is64BitProcess ? "Yes" : "No");

// --- Section 3: .NET CLR Memory Statistics ---
// This section highlights what the CLR tracks that Go/Rust cannot
PrintSection("CLR Memory & GC Statistics");

using var process = Process.GetCurrentProcess();
Print("Working Set (OS)",  $"{process.WorkingSet64 / 1_048_576} MB");
Print("Private Memory",    $"{process.PrivateMemorySize64 / 1_048_576} MB");

// Force no GC collection — read current state as-is
long heapBytes = GC.GetTotalMemory(forceFullCollection: false);
Print("GC Managed Heap",   $"{heapBytes / 1024} KB");
Print("GC Gen 0 Count",    GC.CollectionCount(0).ToString());
Print("GC Gen 1 Count",    GC.CollectionCount(1).ToString());
Print("GC Gen 2 Count",    GC.CollectionCount(2).ToString());

// .NET 5+ detailed GC info
var gcInfo = GC.GetGCMemoryInfo();
Print("GC Total Available", $"{gcInfo.TotalAvailableMemoryBytes / 1_048_576} MB");

// --- Section 4: Environment Variables ---
PrintSection("Key Environment Variables");
string? path = Environment.GetEnvironmentVariable("PATH");
Print("PATH (length)", path?.Length.ToString() ?? "not set");
Print("HOME",          Environment.GetEnvironmentVariable("HOME") ?? "not set");
Print("DOTNET_ROOT",   Environment.GetEnvironmentVariable("DOTNET_ROOT") ?? "not set");

// --- Timing ---
sw.Stop();
Console.WriteLine();
Console.WriteLine($"  ⏱  Completed in {sw.Elapsed.TotalMicroseconds:F0} µs ({sw.ElapsedMilliseconds} ms)");

// --- Helper Functions ---
void PrintHeader(string title)
{
    Console.WriteLine();
    Console.WriteLine($"  ╔══════════════════════════════════════════════╗");
    Console.WriteLine($"  ║  {title,-44}║");
    Console.WriteLine($"  ╚══════════════════════════════════════════════╝");
}

void PrintSection(string name)
{
    Console.WriteLine();
    Console.WriteLine($"  ── {name} ──────────────");
}

void Print(string label, string value)
{
    Console.WriteLine($"  {label,-22}: {value}");
}

string GetOSPlatform()
{
    if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return "Windows";
    if (RuntimeInformation.IsOSPlatform(OSPlatform.Linux))   return "Linux";
    if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))     return "macOS";
    return "Unknown";
}
```

**Build and run:**
```bash
# Development (with JIT warm-up overhead):
dotnet run -- PRODUCTION

# Build self-contained single-file native AOT:
dotnet publish -r linux-x64 -c Release \
    -p:PublishSingleFile=true \
    -p:PublishAot=true \
    -o ./dist

ls -lh ./dist/SysDiag   # Observe binary size
```

---

### Implementation 2: Go (1.22+)

**`go.mod`**
```
module sysdiag

go 1.22
```

**`main.go`**

```go
package main

import (
	"fmt"
	"os"
	"runtime"
	"strings"
	"time"
)

func main() {
	start := time.Now()

	// --- Argument Handling ---
	// os.Args[0] is ALWAYS the binary path — unlike C# where args[0] is the first argument!
	// os.Args[1] is the first actual user argument.
	environment := "LOCAL"
	if len(os.Args) > 1 {
		environment = os.Args[1]
	}

	printHeader(fmt.Sprintf("Go — System Diagnostics [%s]", environment))

	// --- Section 1: Runtime & Architecture ---
	printSection("Runtime Identity")
	print("Go Version", runtime.Version())         // e.g., "go1.22.3"
	print("OS",         runtime.GOOS)              // e.g., "linux", "darwin", "windows"
	print("Arch",       runtime.GOARCH)            // e.g., "amd64", "arm64"
	print("Compiler",   runtime.Compiler)          // "gc" (the standard Go compiler)
	// Note: GOOS and GOARCH are also available at compile time via build tags

	// --- Section 2: Process & CPU ---
	printSection("Process & CPU")
	print("Process ID",       fmt.Sprintf("%d", os.Getpid()))
	print("Parent Process ID",fmt.Sprintf("%d", os.Getppid()))
	print("Logical Cores",    fmt.Sprintf("%d", runtime.NumCPU()))
	// NumCPU() returns the number of logical CPUs usable by the current process,
	// accounting for CPU affinity settings (relevant in containers/Kubernetes)

	hostname, err := os.Hostname()
	if err != nil {
		hostname = fmt.Sprintf("error: %v", err)
	}
	print("Machine Name", hostname)

	// --- Section 3: Go Runtime Memory Statistics ---
	// Go's runtime.MemStats is extremely detailed — this is unique to Go.
	// C# has GC.GetGCMemoryInfo() but Rust has NOTHING equivalent (zero runtime).
	printSection("Go Runtime & GC Statistics")

	// IMPORTANT: ReadMemStats stops the world briefly to take a consistent snapshot.
	// In production code, avoid calling this in request hot-paths!
	var m runtime.MemStats
	runtime.ReadMemStats(&m)

	print("Goroutines Active",    fmt.Sprintf("%d", runtime.NumGoroutine()))
	print("Heap In-Use",         fmt.Sprintf("%d KB", m.HeapInuse/1024))
	print("Heap Allocated",      fmt.Sprintf("%d KB", m.HeapAlloc/1024))
	print("Heap Objects",        fmt.Sprintf("%d", m.HeapObjects))
	print("Stack In-Use",        fmt.Sprintf("%d KB", m.StackInuse/1024))
	print("Total GC Cycles",     fmt.Sprintf("%d", m.NumGC))
	print("Last GC Pause",       fmt.Sprintf("%.2f µs", float64(m.PauseNs[(m.NumGC+255)%256])/1000))
	print("Total GC Pause",      fmt.Sprintf("%.2f ms", float64(m.PauseTotalNs)/1_000_000))
	print("Next GC Trigger",     fmt.Sprintf("%d MB", m.NextGC/1_048_576))

	// --- Section 4: Environment ---
	printSection("Key Environment Variables")
	path := os.Getenv("PATH")
	print("PATH (length)", fmt.Sprintf("%d chars", len(path)))
	print("HOME",          getEnvOrDefault("HOME", "not set"))
	print("GOPATH",        getEnvOrDefault("GOPATH", "not set"))
	print("GOROOT",        runtime.GOROOT()) // Built into the runtime — no env var needed

	// --- Timing ---
	elapsed := time.Since(start)
	fmt.Printf("\n  ⏱  Completed in %d µs (%d ms)\n", elapsed.Microseconds(), elapsed.Milliseconds())
}

func getEnvOrDefault(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}

func printHeader(title string) {
	line := strings.Repeat("═", 48)
	fmt.Printf("\n  ╔%s╗\n", line)
	fmt.Printf("  ║  %-46s║\n", title)
	fmt.Printf("  ╚%s╝\n", line)
}

func printSection(name string) {
	fmt.Printf("\n  ── %s %s\n", name, strings.Repeat("─", 30-len(name)))
}

func print(label, value string) {
	fmt.Printf("  %-22s: %s\n", label, value)
}
```

**Build and run:**
```bash
# Development (instant — no JIT warm-up):
go run main.go PRODUCTION

# Build optimised binary (strip debug symbols for smallest size):
go build -ldflags="-s -w" -o sysdiag-go main.go

# Build fully static binary (no libc dependency — pure static):
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -ldflags="-s -w" -o sysdiag-go-static main.go

# Inspect binary
ls -lh sysdiag-go sysdiag-go-static
ldd sysdiag-go        # Shows dynamic library links
ldd sysdiag-go-static # Should show "not a dynamic executable"
```

> **Key observation for C# developers:** Notice that `runtime.GOROOT()` is available without any environment variables — the Go runtime knows where its root is because the toolchain embeds this at compile time. Similarly, `runtime.GOOS` and `runtime.GOARCH` are compile-time constants resolved before the binary executes — they are not checked at runtime.

---

### Implementation 3: Rust (2021 Edition)

**`Cargo.toml`**
```toml
[package]
name = "sysdiag"
version = "0.1.0"
edition = "2021"

# No external dependencies this week!
# We use only std:: to understand what Rust provides at zero cost.
[dependencies]
```

**`src/main.rs`**

```rust
use std::env;
use std::time::Instant;

fn main() {
    let start = Instant::now();

    // --- Argument Handling ---
    // env::args() returns an Iterator<Item = String>
    // We collect() it into a Vec<String> to index into it.
    // args[0] is always the binary path, just like Go's os.Args[0].
    let args: Vec<String> = env::args().collect();
    let environment = if args.len() > 1 {
        args[1].as_str()
    } else {
        "LOCAL"
    };

    print_header(&format!("Rust — System Diagnostics [{}]", environment));

    // --- Section 1: Compile-Time Platform Constants ---
    // CRITICAL: In Rust, OS and architecture are COMPILE-TIME constants.
    // env::consts::OS is resolved when the binary is compiled, not when it runs.
    // A binary compiled for linux/amd64 will ALWAYS print "linux"/"x86_64"
    // regardless of where it runs — because this is baked into the binary!
    print_section("Runtime Identity");
    print_kv("Rust Version",  env!("CARGO_PKG_RUST_VERSION"));  // From Cargo.toml
    print_kv("Package Name",  env!("CARGO_PKG_NAME"));          // Resolved at compile time
    print_kv("OS",            env::consts::OS);                  // "linux", "macos", "windows"
    print_kv("Arch",          env::consts::ARCH);                // "x86_64", "aarch64", etc.
    print_kv("OS Family",     env::consts::FAMILY);              // "unix" or "windows"
    print_kv("EXE Extension", env::consts::EXE_SUFFIX);         // "" on Linux, ".exe" on Windows

    // --- Section 2: Process & CPU ---
    print_section("Process & CPU");

    // std::thread::available_parallelism() is the idiomatic way to get logical CPU count.
    // It respects CPU affinity limits set by the OS (important in containers).
    let logical_cores = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1);
    print_kv("Logical Cores", &logical_cores.to_string());

    // Note: Rust's std has no built-in way to get process ID on stable
    // without a platform-specific call. This is a deliberate design choice:
    // Rust's std only exposes truly cross-platform APIs.
    #[cfg(unix)]
    {
        // cfg! directives are compile-time — this block doesn't exist in Windows builds
        let pid = unsafe { libc_pid() };
        print_kv("Process ID (unix)", &pid.to_string());
    }

    // --- Section 3: Memory (The Critical Section) ---
    // THIS IS WHERE RUST IS FUNDAMENTALLY DIFFERENT.
    // Rust has NO runtime memory statistics to report because there IS no runtime.
    // There is no GC cycle count. There is no heap monitor.
    // Memory is allocated and freed deterministically by the allocator.
    print_section("Memory Model (No GC — RAII/Ownership)");
    print_kv("GC Cycle Count",   "N/A — No garbage collector exists");
    print_kv("Heap Monitor",     "N/A — No runtime heap tracking");
    print_kv("Memory Reclaim",   "Deterministic RAII (Drop at scope exit)");
    print_kv("Global Allocator", "System default (jemalloc via jemallocator crate optionally)");

    // What we CAN do: demonstrate stack allocation is free and immediate
    {
        // This struct lives ENTIRELY on the stack.
        // When this block ends, memory is released instantly (no GC needed).
        struct StackData { values: [u64; 1000] }
        let _data = StackData { values: [42u64; 1000] }; // 8000 bytes on the stack, zero heap
        print_kv("Stack Demo",   "8000-byte struct on stack — freed at } with zero overhead");
    } // _data dropped here — compiler inserts cleanup code at this EXACT point

    // --- Section 4: Environment Variables ---
    print_section("Key Environment Variables");
    print_kv("PATH (length)", &env::var("PATH").map(|v| v.len().to_string()).unwrap_or("not set".into()));
    print_kv("HOME",          &env::var("HOME").unwrap_or_else(|_| "not set".into()));
    print_kv("CARGO_HOME",    &env::var("CARGO_HOME").unwrap_or_else(|_| "not set".into()));
    print_kv("RUSTUP_HOME",   &env::var("RUSTUP_HOME").unwrap_or_else(|_| "not set".into()));

    // Compile-time environment: env!() is resolved at BUILD TIME, not runtime
    // This is how Rust can embed build metadata without any runtime reflection.
    print_section("Compile-Time Build Information");
    print_kv("Built at profile", if cfg!(debug_assertions) { "DEBUG" } else { "RELEASE" });
    print_kv("Target OS",        env::consts::OS);
    print_kv("Target Arch",      env::consts::ARCH);

    // --- Timing ---
    let elapsed = start.elapsed();
    println!();
    println!("  ⏱  Completed in {} µs ({} ms)", elapsed.as_micros(), elapsed.as_millis());
}

// Platform-specific code using conditional compilation
#[cfg(unix)]
fn libc_pid() -> u32 {
    // In a real project you'd use the `nix` crate.
    // Here we demonstrate that Rust CAN do platform-specific things,
    // but keeps them explicitly labelled with #[cfg].
    std::process::id()
}

// --- Formatting Helpers ---
fn print_header(title: &str) {
    let line = "═".repeat(48);
    println!();
    println!("  ╔{}╗", line);
    println!("  ║  {:<46}║", title);
    println!("  ╚{}╝", line);
}

fn print_section(name: &str) {
    let dashes = "─".repeat(30usize.saturating_sub(name.len()));
    println!("\n  ── {} {}", name, dashes);
}

fn print_kv(label: &str, value: &str) {
    println!("  {:<22}: {}", label, value);
}
```

**Build and run:**
```bash
# Development (debug build — contains overflow checks and debug symbols):
cargo run -- PRODUCTION

# Watch what debug vs release actually compiles:
cargo build 2>&1 | head -5          # Quick, unoptimised
cargo build --release 2>&1 | head -5 # Slower, LLVM-optimised

# Inspect binary sizes:
ls -lh target/debug/sysdiag target/release/sysdiag

# See ALL compiler optimisation decisions (release mode):
RUSTFLAGS="-C remark=all" cargo build --release 2>&1 | grep "inlined\|vectorized" | head -20

# Build truly static binary (zero dynamic library dependencies):
rustup target add x86_64-unknown-linux-musl
cargo build --release --target x86_64-unknown-linux-musl
ldd target/x86_64-unknown-linux-musl/release/sysdiag  # "statically linked"
```

---

## Comparing the Three Outputs Side-by-Side

When you run all three, the output will look similar — but look at Section 3 (Memory):

```
C# Output:
  ── CLR Memory & GC Statistics ──
  GC Managed Heap       : 2 KB
  GC Gen 0 Count        : 0
  GC Gen 1 Count        : 0
  GC Gen 2 Count        : 0
  GC Total Available    : 16084 MB

Go Output:
  ── Go Runtime & GC Statistics ──
  Goroutines Active     : 1
  Heap In-Use           : 192 KB
  Heap Allocated        : 128 KB
  Heap Objects          : 1543
  Stack In-Use          : 32 KB
  Total GC Cycles       : 0
  Last GC Pause         : 0.00 µs

Rust Output:
  ── Memory Model (No GC — RAII/Ownership) ──
  GC Cycle Count        : N/A — No garbage collector exists
  Heap Monitor          : N/A — No runtime heap tracking
  Memory Reclaim        : Deterministic RAII (Drop at scope exit)
```

This output difference is **not a limitation of Rust's standard library**. It is the entire point. Rust cannot report GC statistics because there is nothing to report. The memory the program used has already been freed at the exact points the compiler determined — there is no periodic collection cycle, no heap monitor, no overhead.

---

## Critical Observations for C# Developers

### 1. `env::consts::OS` in Rust vs. `RuntimeInformation.OSDescription` in C#

In C#, `RuntimeInformation.OSDescription` is evaluated at **runtime** — the process asks the OS what it is.

In Rust, `env::consts::OS` is a **compile-time constant** embedded in the binary. A binary compiled on Linux will always say "linux" regardless of where you run it. This is not a bug — it is by design. Rust's portability model means: you compile for your target, and the binary carries its identity.

### 2. Unused Variables Are Errors (Go) or Warnings (Rust)

```go
// Go: This WILL NOT COMPILE
func main() {
    x := 42  // declared and not used — compile error
}
```

```rust
// Rust: This COMPILES but shows a WARNING
fn main() {
    let x = 42;  // warning: unused variable `x` — prefix with _ to silence
    let _y = 42; // No warning — _ prefix signals intentionally unused
}
```

C# only warns about unused variables in certain contexts. Go and Rust treat it more seriously because unused variables are frequently bugs.

### 3. The Error Handling Philosophy Starts Here

Notice in the Go code:
```go
hostname, err := os.Hostname()
if err != nil {
    hostname = fmt.Sprintf("error: %v", err)
}
```

And in Rust:
```rust
let hostname = env::var("HOME").unwrap_or_else(|_| "not set".into());
```

There are no `try-catch` blocks. Go returns errors as values. Rust uses `Result<T, E>` with explicit handling. We will study these in depth in Week 8 — but notice now that you never see a bare `.Value` access that could throw a null reference exception at runtime. The fallibility is always explicit.
