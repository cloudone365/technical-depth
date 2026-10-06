# Week 01 · Hands-On Lab: The Polyglot Build Lab
### From Zero to Running Binaries on All Three Runtimes

> **Format:** Individual setup (Mon–Tue) → Pair programming (Wed–Thu) → Team mob review (Friday 1hr)  
> **Deliverable:** One GitHub repository with three directories — `csharp/`, `go/`, `rust/` — each containing a working `fastbench` CLI tool.

---

## Day 1–2: Individual Toolchain Setup & Verification

Before writing any shared code, each of the 5 team members must independently set up all three toolchains and verify they work. This ensures nobody is blocked during pair programming.

### Step 1: Verify or Install .NET SDK

```bash
# Check existing version
dotnet --version
# Expected: 8.0.x or higher

# If not installed, on Ubuntu/Debian:
wget https://packages.microsoft.com/config/ubuntu/22.04/packages-microsoft-prod.deb -O packages-microsoft-prod.deb
sudo dpkg -i packages-microsoft-prod.deb
sudo apt-get update && sudo apt-get install -y dotnet-sdk-8.0

# Verify the full SDK:
dotnet --info
# Should show: SDK version, Runtime version, Host version, and installed workloads
```

### Step 2: Install and Verify Go

```bash
# Download Go 1.22+
wget https://go.dev/dl/go1.22.3.linux-amd64.tar.gz
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf go1.22.3.linux-amd64.tar.gz

# Add to PATH (add to ~/.bashrc or ~/.zshrc for persistence):
export PATH=$PATH:/usr/local/go/bin
export GOPATH=$HOME/go
export PATH=$PATH:$GOPATH/bin

# Reload shell
source ~/.bashrc

# Verify:
go version        # go version go1.22.3 linux/amd64
go env GOROOT     # Should print /usr/local/go
go env GOPATH     # Should print ~/go
```

### Step 3: Install and Verify Rust (via rustup)

```bash
# The official installer — DO NOT use apt install rust-all (outdated)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
# Follow prompts, choose option 1 (default install)

# Reload shell
source "$HOME/.cargo/env"

# Verify:
rustup --version          # rustup 1.26.0
cargo --version           # cargo 1.76.0
rustc --version           # rustc 1.76.0

# Install commonly needed tooling:
rustup component add clippy    # Rust linter (like dotnet format but smarter)
rustup component add rustfmt   # Code formatter (like dotnet-format)
rustup component add rust-analyzer  # LSP server for VS Code / Neovim

# Add Linux static build target:
rustup target add x86_64-unknown-linux-musl
```

### Step 4: Configure VS Code (or JetBrains Rider for C# habit)

```bash
# Install VS Code extensions:
code --install-extension golang.go           # Go language server (gopls)
code --install-extension rust-lang.rust-analyzer  # Rust language server
code --install-extension ms-dotnettools.csharp    # C# language support

# Verify gopls (Go language server) is installed:
go install golang.org/x/tools/gopls@latest
gopls version
```

### Step 5: Sanity Check — Build "Hello Polyglot"

Create and run the smallest possible program in all three languages to confirm toolchains work:

```bash
# C#
mkdir hello-cs && cd hello-cs
dotnet new console -n Hello
cd Hello && dotnet run   # Should print "Hello, World!"
cd ../..

# Go
mkdir hello-go && cd hello-go
go mod init hello
echo 'package main; import "fmt"; func main() { fmt.Println("Hello, Go!") }' > main.go
go run main.go          # Should print "Hello, Go!"
cd ..

# Rust
cargo new hello-rust
cd hello-rust
cargo run               # Should print "Hello, world!"
cd ..
```

If all three work, your environment is ready.

---

## Day 3–4: Pair Programming — Build `fastbench`

Split your 5-person team into two pairs (and one person works solo on the Go implementation to ensure all three are covered). Each pair builds the tool in one language; rotate for review.

### Specification: The `fastbench` CLI Tool

`fastbench` is a command-line performance measurement tool. It has two subcommands:

**Subcommand `info`:** Prints system and runtime information (like `sysdiag` from the Rosetta Stone, but structured as proper subcommand CLI).

**Subcommand `bench <mode> [iterations]`:** Runs one of three CPU workloads and measures execution time. Modes:
- `fib <n>` — Computes Fibonacci(n) recursively (worst case, good for recursion overhead comparison)
- `sqrt <count>` — Computes `count` square roots of sequential floats (good for FPU throughput)
- `alloc <count>` — Allocates `count` small structs (good for allocator overhead comparison)

**Full expected CLI behaviour:**
```bash
./fastbench info
./fastbench bench fib 42
./fastbench bench sqrt 10000000
./fastbench bench alloc 1000000
./fastbench --help
```

---

### Complete Go Implementation (Reference — Study This First)

The Go version is given to you as a complete reference. Your job is to understand every line.

**`fastbench-go/main.go`**

```go
package main

import (
	"fmt"
	"math"
	"os"
	"runtime"
	"strconv"
	"time"
)

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	switch os.Args[1] {
	case "info":
		runInfo()
	case "bench":
		if len(os.Args) < 4 {
			fmt.Fprintf(os.Stderr, "bench requires: bench <mode> <iterations>\n")
			os.Exit(1)
		}
		runBench(os.Args[2], os.Args[3])
	case "--help", "-h", "help":
		printUsage()
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", os.Args[1])
		printUsage()
		os.Exit(1)
	}
}

func runInfo() {
	var m runtime.MemStats
	runtime.ReadMemStats(&m)

	fmt.Println("\n[ Go Runtime System Info ]")
	fmt.Printf("  Go Version     : %s\n", runtime.Version())
	fmt.Printf("  OS / Arch      : %s / %s\n", runtime.GOOS, runtime.GOARCH)
	fmt.Printf("  CPU Cores      : %d\n", runtime.NumCPU())
	fmt.Printf("  GOMAXPROCS     : %d\n", runtime.GOMAXPROCS(0))
	// GOMAXPROCS is the number of OS threads Go can run concurrently.
	// In containers/K8s it may be LESS than NumCPU() — this matters for perf!
	fmt.Printf("  Goroutines     : %d\n", runtime.NumGoroutine())
	fmt.Printf("  Heap Allocated : %d KB\n", m.HeapAlloc/1024)
	fmt.Printf("  Heap Objects   : %d\n", m.HeapObjects)
	fmt.Printf("  GC Cycles      : %d\n", m.NumGC)
	fmt.Printf("  PID            : %d\n", os.Getpid())
}

func runBench(mode, iterStr string) {
	iterations, err := strconv.Atoi(iterStr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "iterations must be an integer, got: %s\n", iterStr)
		os.Exit(1)
	}

	fmt.Printf("\n[ Benchmark: %s × %d ]\n", mode, iterations)

	var result int64
	start := time.Now()

	switch mode {
	case "fib":
		result = int64(fib(iterations))
	case "sqrt":
		var sum float64
		for i := 0; i < iterations; i++ {
			sum += math.Sqrt(float64(i))
		}
		result = int64(sum)
	case "alloc":
		type Record struct{ id, val int64 }
		slice := make([]Record, 0, iterations)
		for i := 0; i < iterations; i++ {
			slice = append(slice, Record{id: int64(i), val: int64(i * 2)})
		}
		result = int64(len(slice))
	default:
		fmt.Fprintf(os.Stderr, "unknown bench mode: %s (use fib, sqrt, alloc)\n", mode)
		os.Exit(1)
	}

	elapsed := time.Since(start)

	// Print timing with result (printing result prevents dead code elimination)
	fmt.Printf("  Result         : %d\n", result)
	fmt.Printf("  Elapsed        : %v\n", elapsed)
	fmt.Printf("  Per-iteration  : %.2f ns\n", float64(elapsed.Nanoseconds())/float64(iterations))

	// Show GC impact after alloc test
	if mode == "alloc" {
		var m runtime.MemStats
		runtime.ReadMemStats(&m)
		fmt.Printf("  GC Cycles Now  : %d\n", m.NumGC)
		fmt.Printf("  Heap After     : %d KB\n", m.HeapAlloc/1024)
	}
}

// Naive recursive Fibonacci — intentionally slow to test recursion/stack overhead
func fib(n int) int {
	if n <= 1 {
		return n
	}
	return fib(n-1) + fib(n-2)
}

func printUsage() {
	fmt.Println(`
Usage: fastbench <command> [arguments]

Commands:
  info                      Print runtime and system information
  bench fib <n>             Compute Fibonacci(n) recursively
  bench sqrt <count>        Compute <count> square roots
  bench alloc <count>       Allocate <count> in-memory records

Examples:
  fastbench info
  fastbench bench fib 42
  fastbench bench sqrt 10000000
  fastbench bench alloc 1000000`)
}
```

---

### Rust Implementation (Your Task — Build This Yourself)

Build the equivalent `fastbench` in Rust. The specification is identical. Below are guidance notes — not the full solution.

**Skeleton to start from (`src/main.rs`):**

```rust
use std::env;
use std::time::Instant;

fn main() {
    let args: Vec<String> = env::args().collect();

    match args.get(1).map(|s| s.as_str()) {
        Some("info")  => run_info(),
        Some("bench") => {
            let mode       = args.get(2).map(|s| s.as_str()).unwrap_or("fib");
            let iter_str   = args.get(3).map(|s| s.as_str()).unwrap_or("1000");
            let iterations = iter_str.parse::<usize>().unwrap_or_else(|_| {
                eprintln!("Error: iterations must be a positive integer");
                std::process::exit(1);
            });
            run_bench(mode, iterations);
        }
        Some("--help") | Some("-h") | Some("help") => print_usage(),
        Some(unknown) => {
            eprintln!("Unknown command: {}", unknown);
            print_usage();
            std::process::exit(1);
        }
        None => {
            print_usage();
            std::process::exit(1);
        }
    }
}

fn run_info() {
    // TODO: Implement
    // Hints:
    //   - env::consts::OS, env::consts::ARCH for platform info
    //   - std::thread::available_parallelism() for CPU count
    //   - std::process::id() for PID
    //   - cfg!(debug_assertions) to detect build profile
    //   Remember: Rust has NO GC to report on — what do you report instead?
    todo!("Implement run_info()")
}

fn run_bench(mode: &str, iterations: usize) {
    // TODO: Implement all three modes
    // Hints for fib: fn fib(n: u64) -> u64 { if n <= 1 { n } else { fib(n-1) + fib(n-2) } }
    // Hints for sqrt: use f64::sqrt(), add to accumulator to prevent dead code elimination
    // Hints for alloc: use Vec::with_capacity(iterations), push structs
    // For timing: let start = Instant::now(); ... start.elapsed().as_nanos()
    todo!("Implement run_bench()")
}

fn print_usage() {
    todo!("Implement print_usage()")
}
```

**Rust-specific challenges you will encounter while building this:**

1. **Pattern matching on `Option<&str>`:** `args.get(2).map(|s| s.as_str())` returns `Option<&str>`. Practice handling both `Some(value)` and `None` cases.

2. **`todo!()` macro vs `unimplemented!()` macro:** `todo!()` panics with "not yet implemented" — useful during incremental development. `unimplemented!()` signals intentionally missing functionality.

3. **`eprintln!` vs `println!`:** Error messages go to `stderr` (`eprintln!`). Regular output goes to `stdout` (`println!`). This matters when piping output in shell scripts.

4. **Integer types matter in Rust:** `usize` (for indexing/counts), `i64`/`u64` (for arithmetic), `f64` (for floating point). Unlike C#'s implicit numeric conversions, Rust requires explicit `as` casts between numeric types.

---

## Friday Team Mob Review (1 Hour)

### Agenda

**15 min — Demo All Three**
1. Run `fastbench info` in all three languages side-by-side in a terminal split view.
2. Note the differences in output — specifically the GC/memory section.
3. What does each language tell you about its own runtime?

**20 min — Benchmark Comparison**
Run these exact commands and record results in a shared spreadsheet:

```bash
# Fibonacci benchmark (tests recursion and function call overhead)
time ./fastbench-go bench fib 42
time ./fastbench-rust-release bench fib 42

# Square root benchmark (tests floating point throughput)
time ./fastbench-go bench sqrt 50000000
time ./fastbench-rust-release bench sqrt 50000000

# Allocation benchmark (tests allocator and GC overhead)
time ./fastbench-go bench alloc 5000000
time ./fastbench-rust-release bench alloc 5000000
```

**Record these in your shared sheet:**

| Benchmark | Go Time | Rust Debug Time | Rust Release Time |
|-----------|---------|-----------------|-------------------|
| fib(42) | | | |
| sqrt × 50M | | | |
| alloc × 5M | | | |

**Questions to answer together:**
- By what factor does Rust Release beat Rust Debug? This shows you the power of LLVM optimisation.
- Is Go or Rust Release faster for the `fib` benchmark? For `sqrt`? Why might they differ?
- In the `alloc` benchmark — does Go show GC cycles triggering? Can you see them?

**20 min — Code Review Questions**

Go through each person's Rust implementation and discuss:

1. **Did anyone use `unwrap()` without a comment explaining why it's safe?** In production Rust code, bare `unwrap()` is a red flag. What should you use instead? (`unwrap_or`, `unwrap_or_else`, `?`, explicit `match`)

2. **How did you handle `args.get(2)`?** Show two different valid approaches your team used.

3. **Unused variables:** Did the Rust compiler warn about anything? How did you resolve it — `_prefix`, `let _ =`, or actually removing the variable?

4. **Type inference:** Where did Rust successfully infer the type? Where did you need to annotate explicitly? Compare this to C#'s `var`.

**5 min — Weekly Sign-off Criteria**

Before leaving the session, confirm each of the 5 team members can answer YES to:

- [ ] All three toolchains installed and working on my machine
- [ ] I can explain what CIL is and why Go/Rust don't use it
- [ ] I can explain what "Tiered JIT" means and why Rust doesn't need it
- [ ] I understand why `cargo build --release` makes such a large performance difference
- [ ] I understand why Rust's `info` output cannot report GC statistics
- [ ] I can build, run, and inspect binaries in all three languages without consulting notes

---

## Stretch Goals (For Faster Team Members)

**Stretch 1: Cross-compilation**
```bash
# Build a Go binary for Windows from Linux:
GOOS=windows GOARCH=amd64 go build -o fastbench.exe main.go
file fastbench.exe   # Should show PE32+ format (Windows binary)

# Build a Rust binary for Apple Silicon from Linux:
rustup target add aarch64-apple-darwin
cargo build --release --target aarch64-apple-darwin
# (May fail without macOS SDK — note why and document)
```

**Stretch 2: Profile-Guided Optimisation in Rust**
```bash
# Build with PGO instrumentation:
RUSTFLAGS="-C profile-generate=/tmp/pgo-data" cargo build --release
# Run the binary to generate profiling data:
./target/release/fastbench bench sqrt 10000000
# Rebuild with the profile data:
llvm-profdata merge -output=/tmp/pgo-data/merged.profdata /tmp/pgo-data
RUSTFLAGS="-C profile-use=/tmp/pgo-data/merged.profdata" cargo build --release
# Compare before/after performance
```

**Stretch 3: Disassemble and Compare**
```bash
# View the machine code for the Go fib function:
go build -o fastbench-go main.go
objdump -d fastbench-go | grep -A 30 "main.fib>"

# View the machine code for the Rust fib function in debug vs release:
objdump -d target/debug/fastbench | grep -A 30 "<fastbench::fib>"
objdump -d target/release/fastbench | grep -A 30 "<fastbench::fib>"
# Notice: release mode will likely have the function INLINED AWAY entirely!
```
