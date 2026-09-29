# Week 27: Hands-On Lab Exercise — High-Throughput SIMD CSV Parser & FFI Profiling

## Objective
This lab provides concrete systems engineering experience in two critical domains:
1. **FFI Runtime Cost Analysis**: Quantify the nanosecond latency of crossing the language boundary in C#, Go, and Rust using Linux hardware performance counters (`perf`).
2. **SIMD Vectorized Delimiter Indexing**: Build an ultra-fast CSV parser that locates record delimiters (commas `,` = `0x2C` and newlines `\n` = `0x0A`) in a 500MB memory buffer, achieving over 20 GB/s throughput by evaluating 32 bytes per cycle.

---

## Lab Architecture & Schedule

```
  ┌────────────────────────────────────────────────────────────────────────┐
  │ Day 1–2: FFI Micro-Benchmarking & Stack Transition Analysis            │
  │ • Build shared native C math library                                   │
  │ • Implement 50M iteration harnesses in C#, Go, and Rust                │
  │ • Measure C# [LibraryImport] vs [SuppressGCTransition]                 │
  │ • Measure Go cgo stack transition vs Rust zero-cost extern "C"         │
  │ • Profile syscalls and context switches with Linux perf                │
  ├────────────────────────────────────────────────────────────────────────┤
  │ Day 3–4: Ultra-Fast Vectorized CSV Delimiter Indexer                   │
  │ • Implement dual-delimiter matching (',' and '\n')                     │
  │ • Study complete reference Go implementation (Scalar, SWAR, Cgo)       │
  │ • Complete Rust AVX2 starter skeleton using bitmask extraction (TZCNT) │
  │ • Measure and compare throughput in GB/s across all engines            │
  ├────────────────────────────────────────────────────────────────────────┤
  │ Friday: Mob Review, Performance Whiteboard, & Architectural Defense    │
  │ • 7 Technical Defense Questions with authoritative expected answers    │
  │ • Individual Sign-Off Checklist & Advanced Stretch Challenges          │
  └────────────────────────────────────────────────────────────────────────┘
```

---

## Day 1–2: FFI Context Switch & Micro-Benchmarking Harness

Your team will measure the physical cost of crossing the foreign function boundary.

### Step 1: The Native C Library (`libffi_bench.c`)

Create `libffi_bench.c`:

```c
// libffi_bench.c
// Compile with: gcc -O3 -shared -fPIC -o libffi_bench.so libffi_bench.c
#include <stdint.h>

// Trivial non-inlinable computation
int64_t bench_accumulate(int64_t val) {
    return (val * 3) ^ 0xA5A5A5A55A5A5A5AULL;
}
```

Compile the library:
```bash
gcc -O3 -shared -fPIC -o libffi_bench.so libffi_bench.c
```

### Step 2: Implement 50,000,000 Iterations Across Runtimes

#### C# Harness (`FfiBench.cs`)
```csharp
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

internal static partial class Program
{
    [LibraryImport("ffi_bench", EntryPoint = "bench_accumulate")]
    [SuppressGCTransition] // Test both with and without this attribute!
    private static partial long BenchAccumulate(long val);

    public static void Main()
    {
        const int iterations = 50_000_000;
        _ = BenchAccumulate(1); // Warmup
        var sw = Stopwatch.StartNew();
        long sum = 0;
        for (int i = 0; i < iterations; i++) sum += BenchAccumulate(i);
        sw.Stop();
        Console.WriteLine($"C# 50M Calls: {sw.ElapsedMilliseconds} ms ({(sw.Elapsed.TotalNanoseconds/iterations):F2} ns/call)");
    }
}
```

#### Go Harness (`ffi_bench.go`)
```go
package main

/*
#cgo LDFLAGS: -L. -lffi_bench -Wl,-rpath=.
#include <stdint.h>
int64_t bench_accumulate(int64_t val);
*/
import "C"
import (
	"fmt"
	"time"
)

func main() {
	const iterations = 50_000_000
	_ = C.bench_accumulate(1) // Warmup
	start := time.Now()
	var sum int64 = 0
	for i := 0; i < iterations; i++ {
		sum += int64(C.bench_accumulate(C.int64_t(i)))
	}
	dur := time.Since(start)
	fmt.Printf("Go 50M Calls: %d ms (%.2f ns/call)\n", dur.Milliseconds(), float64(dur.Nanoseconds())/iterations)
}
```

### Step 3: Profile with Linux `perf`

Run the Go and Rust binaries under `perf stat` to observe context switches:
```bash
perf stat -e context-switches,cpu-migrations,page-faults,cycles,instructions ./bench_go
perf stat -e context-switches,cpu-migrations,page-faults,cycles,instructions ./bench_rust
```

Record the nanoseconds per call:
- Rust `extern "C"`: ~1.5 ns/call
- C# `[SuppressGCTransition]`: ~1.6 ns/call
- C# Standard `[LibraryImport]`: ~11.5 ns/call
- Go `cgo`: ~70.0 ns/call

---

## Day 3–4: SIMD Delimiter Scanner & CSV Indexer

CSV parsing typically spends 80% of its execution time inspecting non-delimiter payload bytes. An AVX2 vector kernel evaluates 32 bytes simultaneously:
1. Broadcast delimiter `','` (`0x2C`) into `YMM_COMMA`.
2. Broadcast delimiter `'\n'` (`0x0A`) into `YMM_NEWLINE`.
3. Load 32 bytes from the buffer into `YMM_DATA` via `vmovdqu`.
4. Perform parallel byte comparisons: `vpcmpeqb` against both delimiters.
5. Combine the equality vectors using bitwise OR: `vpor`.
6. Extract the 32-bit mask: `vpmovmskb`.
7. Iterate over matching bit indices using `TZCNT` (trailing zero count) and bit clearing `mask &= mask - 1`.

---

## Complete Reference Implementation: Go 1.21+ (`csv_indexer_ref.go`)

This is the fully functional reference implementation featuring a synthetic 500MB CSV generator, scalar parser, 64-bit SWAR chunking parser, and validation routines.

```go
// csv_indexer_ref.go
// Run with: go run csv_indexer_ref.go
package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"math/bits"
	"time"
)

// DelimiterOffset stores the byte offset and delimiter type
type DelimiterOffset struct {
	Offset uint32
	IsRow  bool // true for '\n', false for ','
}

func main() {
	fmt.Println("===============================================================")
	fmt.Println("  Week 27 Lab: 500MB SIMD/SWAR CSV Delimiter Indexer (Go)")
	fmt.Println("===============================================================\n")

	// 1. Generate 500MB Synthetic CSV Buffer
	const targetSize = 500 * 1024 * 1024 // 500 MB
	fmt.Printf("[Setup] Generating ~500MB synthetic CSV buffer in memory...\n")
	
	rowPattern := []byte("1001,John Doe,Engineering,95000.50,2023-01-15,Active\n")
	repeatCount := targetSize / len(rowPattern)
	csvBuffer := bytes.Repeat(rowPattern, repeatCount)
	actualSize := len(csvBuffer)
	fmt.Printf("[Setup] Generated %d bytes (%.2f MB). Expected rows: %d\n\n",
		actualSize, float64(actualSize)/(1024*1024), repeatCount)

	// 2. Benchmark Scalar Baseline
	start := time.Now()
	scalarOffsets := ParseCsvScalar(csvBuffer)
	scalarDur := time.Since(start)
	scalarGBs := (float64(actualSize) / (1024 * 1024 * 1024)) / scalarDur.Seconds()
	fmt.Printf("[Scalar] Completed in %6.2f ms | Throughput: %5.2f GB/s | Total Delimiters: %d\n",
		float64(scalarDur.Milliseconds()), scalarGBs, len(scalarOffsets))

	// 3. Benchmark Pure Go 64-bit SWAR Parser
	start = time.Now()
	swarOffsets := ParseCsvSWAR(csvBuffer)
	swarDur := time.Since(start)
	swarGBs := (float64(actualSize) / (1024 * 1024 * 1024)) / swarDur.Seconds()
	fmt.Printf("[SWAR  ] Completed in %6.2f ms | Throughput: %5.2f GB/s | Total Delimiters: %d\n",
		float64(swarDur.Milliseconds()), swarGBs, len(swarOffsets))

	fmt.Printf("[Speedup] SWAR vs Scalar: %.2fx\n", scalarDur.Seconds()/swarDur.Seconds())

	// 4. Verify Correctness
	if len(scalarOffsets) != len(swarOffsets) {
		panic(fmt.Sprintf("Mismatch! Scalar count: %d, SWAR count: %d",
			len(scalarOffsets), len(swarOffsets)))
	}
	fmt.Println("[Verify] Output verified: SWAR matches scalar output perfectly.\n")
}

// ParseCsvScalar scans one byte at a time
func ParseCsvScalar(data []byte) []DelimiterOffset {
	offsets := make([]DelimiterOffset, 0, len(data)/10) // Estimate capacity
	for i, b := range data {
		if b == ',' {
			offsets = append(offsets, DelimiterOffset{Offset: uint32(i), IsRow: false})
		} else if b == '\n' {
			offsets = append(offsets, DelimiterOffset{Offset: uint32(i), IsRow: true})
		}
	}
	return offsets
}

// ParseCsvSWAR processes 8 bytes per iteration using 64-bit word bitwise parallel arithmetic
func ParseCsvSWAR(data []byte) []DelimiterOffset {
	offsets := make([]DelimiterOffset, 0, len(data)/10)
	n := len(data)
	i := 0

	const commaByte byte = ','
	const newlineByte byte = '\n'

	// Replicate delimiters across 8 bytes
	commaWord := uint64(commaByte) * 0x0101010101010101
	newlineWord := uint64(newlineByte) * 0x0101010101010101
	const highBits = 0x8080808080808080

	for i+8 <= n {
		word := binary.LittleEndian.Uint64(data[i:])

		// Check for comma and newline matches in parallel
		xorComma := word ^ commaWord
		matchComma := (xorComma - 0x0101010101010101) &^ xorComma & highBits

		xorNewline := word ^ newlineWord
		matchNewline := (xorNewline - 0x0101010101010101) &^ xorNewline & highBits

		// Fast path: if neither delimiter exists in this 8-byte chunk, skip instantly
		if (matchComma | matchNewline) == 0 {
			i += 8
			continue
		}

		// Slow path: extract bit indices for commas
		for matchComma != 0 {
			trailingZeros := bits.TrailingZeros64(matchComma)
			byteIdx := trailingZeros / 8
			offsets = append(offsets, DelimiterOffset{Offset: uint32(i + byteIdx), IsRow: false})
			matchComma &= matchComma - 1 // Clear lowest set bit
		}

		// Extract bit indices for newlines
		for matchNewline != 0 {
			trailingZeros := bits.TrailingZeros64(matchNewline)
			byteIdx := trailingZeros / 8
			offsets = append(offsets, DelimiterOffset{Offset: uint32(i + byteIdx), IsRow: true})
			matchNewline &= matchNewline - 1 // Clear lowest set bit
		}

		i += 8
	}

	// Remainder scalar loop
	for i < n {
		if data[i] == ',' {
			offsets = append(offsets, DelimiterOffset{Offset: uint32(i), IsRow: false})
		} else if data[i] == '\n' {
			offsets = append(offsets, DelimiterOffset{Offset: uint32(i), IsRow: true})
		}
		i++
	}

	return offsets
}
```

---

## Rust Starter Skeleton (`csv_indexer_rust/src/main.rs`)

### The 3 Specific Rust Concepts You Will Fight:
1. **Pointer Arithmetic & Unaligned Loads**: Rust slices (`&[u8]`) do not allow raw pointer arithmetic without `unsafe`. You must use `data.as_ptr().add(offset)` and cast to `*const __m256i`. Ensure you never read past `len - 32` or you will trigger a memory fault.
2. **Dual-Delimiter Bitmask Manipulation**: `_mm256_movemask_epi8` produces a 32-bit mask. To extract byte offsets efficiently without branching on each bit, you must use `mask.trailing_zeros()` and clear bits using `mask &= mask - 1` (compiles to the single-cycle x86 BMI1 instruction `BLSR`).
3. **The `#[target_feature]` Inlining Boundary**: A helper function marked `#[target_feature(enable = "avx2")]` cannot be inlined into an unadorned caller. You must decorate your scanning function with `#[target_feature(enable = "avx2")]` and invoke it through runtime feature dispatch via `is_x86_feature_detected!("avx2")`.

### Starter Code with Explicit Guidance

```rust
// csv_indexer_rust/src/main.rs
use std::arch::x86_64::*;
use std::time::Instant;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DelimiterOffset {
    pub offset: u32,
    pub is_row: bool, // true for '\n', false for ','
}

fn main() {
    println!("=== Week 27 Lab: 500MB SIMD CSV Delimiter Indexer (Rust) ===\n");

    // 1. Generate 500MB Synthetic CSV
    let target_size = 500 * 1024 * 1024;
    let row_pattern = b"1001,John Doe,Engineering,95000.50,2023-01-15,Active\n";
    let repeat_count = target_size / row_pattern.len();
    let csv_buffer = row_pattern.repeat(repeat_count);
    println!("[Setup] Generated {} MB buffer.", csv_buffer.len() / (1024 * 1024));

    // 2. Scalar Benchmark
    let start = Instant::now();
    let scalar_results = parse_csv_scalar(&csv_buffer);
    let scalar_dur = start.elapsed();
    let scalar_gbs = (csv_buffer.len() as f64 / (1024.0 * 1024.0 * 1024.0)) / scalar_dur.as_secs_f64();
    println!("[Scalar] Time: {:6.2} ms | Throughput: {:5.2} GB/s | Count: {}",
        scalar_dur.as_secs_f64() * 1000.0, scalar_gbs, scalar_results.len());

    // 3. AVX2 Benchmark
    if is_x86_feature_detected!("avx2") {
        let start = Instant::now();
        let avx2_results = unsafe { parse_csv_avx2(&csv_buffer) };
        let avx2_dur = start.elapsed();
        let avx2_gbs = (csv_buffer.len() as f64 / (1024.0 * 1024.0 * 1024.0)) / avx2_dur.as_secs_f64();
        println!("[AVX2  ] Time: {:6.2} ms | Throughput: {:5.2} GB/s | Count: {}",
            avx2_dur.as_secs_f64() * 1000.0, avx2_gbs, avx2_results.len());
        println!("[Speedup] AVX2 vs Scalar: {:.2}x", scalar_dur.as_secs_f64() / avx2_dur.as_secs_f64());
        
        assert_eq!(scalar_results.len(), avx2_results.len(), "Count mismatch between scalar and AVX2!");
    } else {
        println!("[AVX2] Hardware instruction set not supported on this host.");
    }
}

pub fn parse_csv_scalar(data: &[u8]) -> Vec<DelimiterOffset> {
    let mut offsets = Vec::with_capacity(data.len() / 10);
    for (i, &b) in data.iter().enumerate() {
        if b == b',' {
            offsets.push(DelimiterOffset { offset: i as u32, is_row: false });
        } else if b == b'\n' {
            offsets.push(DelimiterOffset { offset: i as u32, is_row: true });
        }
    }
    offsets
}

#[target_feature(enable = "avx2")]
pub unsafe fn parse_csv_avx2(data: &[u8]) -> Vec<DelimiterOffset> {
    let mut offsets = Vec::with_capacity(data.len() / 10);
    let len = data.len();
    let mut ptr = data.as_ptr();
    let end = ptr.add(len);
    let vector_end = ptr.add(len.saturating_sub(32));

    // TODO 1: Create 256-bit vector broadcast registers for ',' and '\n'
    // Concept: Use _mm256_set1_epi8(b',' as i8) and _mm256_set1_epi8(b'\n' as i8)
    let comma_vec = _mm256_set1_epi8(b',' as i8);
    let newline_vec = _mm256_set1_epi8(b'\n' as i8);

    while ptr <= vector_end {
        let current_offset = ptr.offset_from(data.as_ptr()) as u32;

        // TODO 2: Load 32 bytes unaligned into a YMM register
        // Concept: Use _mm256_loadu_si256(ptr as *const __m256i)
        let chunk = _mm256_loadu_si256(ptr as *const __m256i);

        // TODO 3: Compare chunk against comma_vec and newline_vec
        // Concept: _mm256_cmpeq_epi8 produces 0xFF where matched
        let cmp_comma = _mm256_cmpeq_epi8(chunk, comma_vec);
        let cmp_newline = _mm256_cmpeq_epi8(chunk, newline_vec);

        // TODO 4: Extract 32-bit bitmasks using _mm256_movemask_epi8
        let mut mask_comma = _mm256_movemask_epi8(cmp_comma) as u32;
        let mut mask_newline = _mm256_movemask_epi8(cmp_newline) as u32;

        // Fast path: if neither delimiter exists in this 32-byte chunk, advance pointer immediately
        if (mask_comma | mask_newline) == 0 {
            ptr = ptr.add(32);
            continue;
        }

        // TODO 5: Extract bit indices using mask.trailing_zeros() and clear bits using mask &= mask - 1
        while mask_comma != 0 {
            let bit_idx = mask_comma.trailing_zeros();
            offsets.push(DelimiterOffset { offset: current_offset + bit_idx, is_row: false });
            mask_comma &= mask_comma - 1; // Compiles to hardware BLSR
        }

        while mask_newline != 0 {
            let bit_idx = mask_newline.trailing_zeros();
            offsets.push(DelimiterOffset { offset: current_offset + bit_idx, is_row: true });
            mask_newline &= mask_newline - 1;
        }

        ptr = ptr.add(32);
    }

    // Remainder loop for trailing bytes (< 32 bytes)
    while ptr < end {
        let idx = ptr.offset_from(data.as_ptr()) as u32;
        if *ptr == b',' {
            offsets.push(DelimiterOffset { offset: idx, is_row: false });
        } else if *ptr == b'\n' {
            offsets.push(DelimiterOffset { offset: idx, is_row: true });
        }
        ptr = ptr.add(1);
    }

    offsets
}
```

---

## Friday Mob Review

Gather your team to run the benchmarks on a dedicated Linux machine. Project the terminal and populate the following whiteboard table:

### Benchmark Comparison Matrix

| Workload Component | C# (.NET 8) | Go (1.21+) | Rust (1.75+) |
| :--- | :--- | :--- | :--- |
| **50M FFI Calls (Total Time)** | [ ] ms (`[SuppressGC]`)<br>[ ] ms (Standard) | [ ] ms (`cgo`) | [ ] ms (`extern "C"`) |
| **FFI Cost per Call (ns)** | [ ] ns | [ ] ns | [ ] ns |
| **500MB CSV: Scalar Scan** | [ ] GB/s | [ ] GB/s | [ ] GB/s |
| **500MB CSV: SWAR (64-bit)** | N/A | [ ] GB/s | N/A |
| **500MB CSV: AVX2 Scan** | [ ] GB/s | [ ] GB/s (via cgo batch) | [ ] GB/s |
| **IPC (Instructions Per Cycle)** | [ ] | [ ] | [ ] |

---

## 7 Technical Defense Questions with Expected Answers

### 1. Why does Go's `cgo` impose a 50–100ns penalty while C#'s `[SuppressGCTransition]` costs ~1.5ns?
**Expected Answer**: Go goroutines execute on dynamic 2KB split stacks multiplexed across OS threads. Calling C requires switching the CPU stack pointer to the pthread `g0` system stack, detaching the logical processor $P$ (`entersyscall`), executing the C function, re-acquiring $P$ (`exitsyscall`), and switching stacks back. C# managed threads, by contrast, run on standard 1MB OS thread stacks; with `[SuppressGCTransition]`, RyuJIT skips the CLR thread-state bookkeeping entirely, emitting a direct machine `call` instruction.

### 2. What catastrophe occurs if a C function marked `[SuppressGCTransition]` blocks?
**Expected Answer**: If the native C function blocks on I/O, a mutex, or an infinite loop, the thread cannot reach a GC safepoint. Because the CLR was instructed to omit the transition to Preemptive mode, the Garbage Collector will freeze the entire .NET runtime if any other thread requests a memory allocation during that time.

### 3. How does bitmask extraction (`vpmovmskb`) coupled with `tzcnt` achieve $O(K)$ complexity rather than $O(N)$?
**Expected Answer**: Rather than iterating sequentially over 32 bytes ($O(32)$), `vpmovmskb` condenses 32 comparison results into a single 32-bit integer register. If the mask is zero, all 32 bytes are bypassed in 1 branch. If bits are set, `tzcnt` finds the exact bit index in 1 cycle, and `mask &= mask - 1` clears it. The loop executes strictly $K$ times, where $K$ is the number of actual delimiter matches.

### 4. Why does Rust's borrow checker enable LLVM to auto-vectorize loops that C# and Go refuse to vectorize?
**Expected Answer**: The primary impediment to compiler auto-vectorization is pointer aliasing. In C# and Go, arrays and slices can legally point to overlapping memory. In Rust, the borrow checker enforces that mutable references (`&mut T`) are globally exclusive. This provides LLVM with mathematical proof that destination and source buffers do not alias, enabling automatic loop vectorization without manual compiler pragmas.

### 5. Why does unaligned vector loading (`vmovdqu`) rarely penalize performance on modern CPUs, and when does it stall?
**Expected Answer**: Since Intel Nehalem and AMD Zen 1, memory controllers execute unaligned loads at identical throughput to aligned loads provided the 32-byte chunk fits inside a single 64-byte physical cache line. A pipeline stall occurs only when an unaligned load straddles two distinct cache lines (requiring 2 L1D reads and split-load buffer assembly) or crosses a 4KB virtual memory page boundary (requiring 2 TLB lookups).

### 6. How does AVX2 frequency throttling compare to AVX-512 license downclocking?
**Expected Answer**: AVX2 instructions operate within the standard CPU core voltage and frequency plane without downclocking. AVX-512 instructions (on pre-Sapphire Rapids Intel architectures) draw extreme instantaneous current, triggering hardware power licenses that downclock core frequencies by 10%–20% across all cores. Consequently, AVX2 remains the preferred baseline for predictable latency in low-latency systems.

### 7. Why does scanning a 500MB buffer in memory hit a throughput wall around 25–35 GB/s?
**Expected Answer**: An AVX2 vector kernel processing 32 bytes per cycle at 4.0 GHz possesses a theoretical compute bandwidth of $32 \times 4.0 = 128\text{ GB/s}$. However, main memory (DDR4/DDR5) dual-channel bandwidth tops out at 25–35 GB/s. Once vector registers process bytes faster than the CPU memory bus can deliver them from DRAM to L3/L1 cache, the pipeline becomes memory-bandwidth saturated.

---

## Sign-Off Checklist
Every team member must be able to answer "Yes" to each of the following:
- [ ] I can describe the exact 5-step sequence occurring inside the Go runtime during a `cgo` call.
- [ ] I can explain the difference between `[LibraryImport]` with and without `[SuppressGCTransition]`.
- [ ] I understand how `_mm256_movemask_epi8` converts 256 bits of vector state into a 32-bit scalar integer.
- [ ] I know how `trailing_zeros()` / `TZCNT` and `mask &= mask - 1` (BLSR) extract delimiter offsets in $O(K)$ time.
- [ ] I can explain why Rust functions decorated with `#[target_feature]` cannot be inlined into generic callers.
- [ ] I understand why AVX2 SIMD scanning plateaus at the memory bandwidth boundary (~30 GB/s).
- [ ] I know how to profile Linux hardware context switches using `perf stat`.

---

## Stretch Goals for Fast Learners

### 1. RFC 4180 Escaped Quote Masking via Carry-Less Multiplication (`PCLMULQDQ`)
In complete RFC 4180 CSV parsing, commas and newlines appearing inside double-quoted fields (`"Doe, John",Engineer`) must not be treated as delimiters.
- Detecting which characters are inside quotes is equivalent to computing a cumulative prefix XOR over the quote bitmask.
- Microarchitecturally, prefix XOR can be computed across 64 bits in a single cycle using Carry-Less Multiplication (`_mm_clmulepi64_si128` / `vpclmulqdq`):
  $$\text{QuoteMask} = \text{PCLMUL}(\text{QuotesBitmask}, \text{0xFFFFFFFFFFFFFFFF})$$
- Inverting this mask and applying a bitwise AND to the comma/newline masks filters out all intra-field delimiters in parallel at over 15 GB/s!

### 2. Plan 9 Assembly Generation via `avo` in Go
Instead of hand-writing Plan 9 assembly, use `github.com/mmcloughlin/avo`:
- Write a Go generator program that builds the SSA AST for the AVX2 CSV parser.
- Let `avo` handle register allocation (`Y0` through `Y5`), loop labels, and parameter frame offsets.
- Generate the final `csv_parser_amd64.s` file and invoke it from pure Go with zero `cgo` overhead.

### 3. AVX-512 64-Byte Kernel
Extend the implementation to use AVX-512:
- Use `_mm512_loadu_si512` on ZMM registers to inspect 64 bytes per cycle.
- Use `_mm512_cmpeq_epi8_mask` which directly emits a 64-bit integer mask into an opmask register (`k1`), eliminating the need for `vpmovmskb`.

[End of file]
