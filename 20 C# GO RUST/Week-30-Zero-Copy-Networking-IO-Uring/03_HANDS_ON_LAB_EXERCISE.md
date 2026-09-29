# Week 30: Hands-On Lab - Zero-Copy I/O and Kernel Metrics

## Lab Overview & Architectural Objectives

In this hands-on engineering lab, your team will empirically measure and profile the performance gap between standard user-space buffered I/O, Linux kernel zero-copy (`sendfile` / `splice`), and asynchronous completion queues (`io_uring`). You will observe how low-level OS primitives impact CPU cache coherency, hardware context switches, and physical memory bus saturation.

### System Hypotheses to Prove
1. **The Context Switch Cliff:** Moving from standard buffered I/O to `sendfile` will reduce operating system context switches by over **99%**.
2. **The Cache Invalidation Penalty:** User-space copying will produce millions of Last-Level Cache (LLC / L3) misses, whereas zero-copy will show near-zero LLC evictions because payload data bypasses CPU registers.
3. **The Kernel Bypass Effect:** Under `io_uring` with ring submissions, the CPU will achieve line-rate network saturation with negligible `sys` (system call) time.

### Hardware & Environment Requirements
- Linux kernel 5.8+ (kernel 5.15+ or 6.x recommended).
- Hardware performance counters enabled: `perf` (via `linux-tools-common` / `linux-tools-generic`).
- Tools: `socat`, `curl`, `sysstat` (`mpstat`), `numactl`.

---

## Day 1-2: Production Reference Implementation (Go)

The reference implementation provides a fully instrumented, multi-mode 10GB streaming server. It measures wall-clock time, system/user CPU time via POSIX `getrusage(2)`, and memory allocations via Go's runtime profiler.

### File: `lab_server.go`

```go
package main

import (
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"runtime"
	"syscall"
	"time"
)

const (
	Port             = "9090"
	PayloadPath      = "10gb_lab_payload.dat"
	PayloadSizeBytes = int64(10) * 1024 * 1024 * 1024 // 10 GB
)

type unoptimizedReader struct {
	io.Reader
}

func main() {
	mode := flag.String("mode", "sendfile", "Streaming mode: 'buffered', 'sendfile', or 'splice'")
	bufSizeKB := flag.Int("bufsize", 64, "Buffer size in KB for buffered mode")
	flag.Parse()

	ensurePayloadExists(PayloadPath, PayloadSizeBytes)

	listener, err := net.Listen("tcp", "0.0.0.0:"+Port)
	if err != nil {
		log.Fatalf("[Lab Server] Bind error: %v", err)
	}
	defer listener.Close()

	fmt.Println("=================================================================")
	fmt.Printf(" ZERO-COPY BENCHMARK SERVER (Go 1.22)\n")
	fmt.Printf(" Listening: 0.0.0.0:%s | Mode: %s | Buffer: %d KB\n", Port, *mode, *bufSizeKB)
	fmt.Println("=================================================================")

	for {
		conn, err := listener.Accept()
		if err != nil {
			continue
		}
		handleClient(conn.(*net.TCPConn), PayloadPath, *mode, *bufSizeKB*1024)
	}
}

func handleClient(conn *net.TCPConn, filePath string, mode string, bufSize int) {
	defer conn.Close()
	_ = conn.SetNoDelay(true)

	// Optimize socket send buffer dynamically
	if rawConn, err := conn.SyscallConn(); err == nil {
		_ = rawConn.Control(func(fd uintptr) {
			_ = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_SNDBUF, 0)
		})
	}

	file, err := os.Open(filePath)
	if err != nil {
		log.Printf("File open error: %v", err)
		return
	}
	defer file.Close()

	// Capture initial memory and CPU resource usage counters
	var memBefore runtime.MemStats
	runtime.ReadMemStats(&memBefore)
	var rusageBefore syscall.Rusage
	_ = syscall.Getrusage(syscall.RUSAGE_SELF, &rusageBefore)

	startTime := time.Now()
	var totalSent int64

	switch mode {
	case "buffered":
		// Force user-space copying by wrapping file to mask io.ReaderFrom interface
		buffer := make([]byte, bufSize)
		totalSent, err = io.CopyBuffer(conn, unoptimizedReader{file}, buffer)

	case "sendfile":
		// Kernel zero-copy path: net.TCPConn dispatches to sys_sendfile
		totalSent, err = io.Copy(conn, file)

	case "splice":
		// In-kernel pipe splicing
		totalSent, err = runSpliceTransfer(conn, file, PayloadSizeBytes)

	default:
		log.Fatalf("Invalid mode: %s", mode)
	}

	elapsed := time.Since(startTime).Seconds()

	// Capture ending resource counters
	var memAfter runtime.MemStats
	runtime.ReadMemStats(&memAfter)
	var rusageAfter syscall.Rusage
	_ = syscall.Getrusage(syscall.RUSAGE_SELF, &rusageAfter)

	if err != nil {
		log.Printf("Transfer failed: %v", err)
		return
	}

	userCPUTime := timeDiff(rusageBefore.Utime, rusageAfter.Utime)
	sysCPUTime := timeDiff(rusageBefore.Stime, rusageAfter.Stime)
	gbps := (float64(totalSent) * 8.0) / (elapsed * 1e9)
	heapAllocMB := float64(memAfter.TotalAlloc-memBefore.TotalAlloc) / (1024 * 1024)

	fmt.Printf("\n--- Transfer Complete [%s] ---\n", mode)
	fmt.Printf(" Transferred:     %d MB\n", totalSent/(1024*1024))
	fmt.Printf(" Elapsed Time:    %.3f seconds\n", elapsed)
	fmt.Printf(" Throughput:      %.2f Gbps (%.2f MB/s)\n", gbps, float64(totalSent)/(elapsed*1024*1024))
	fmt.Printf(" User CPU Time:   %.3f seconds\n", userCPUTime)
	fmt.Printf(" System CPU Time: %.3f seconds\n", sysCPUTime)
	fmt.Printf(" Heap Allocated:  %.2f MB\n", heapAllocMB)
	fmt.Printf(" GC Cycles:       %d\n", memAfter.NumGC-memBefore.NumGC)
	fmt.Println("-----------------------------------------------------------------")
}

func runSpliceTransfer(conn *net.TCPConn, file *os.File, totalExpected int64) (int64, error) {
	pipeFds := make([]int, 2)
	if err := syscall.Pipe2(pipeFds, syscall.O_NONBLOCK); err != nil {
		return 0, err
	}
	defer syscall.Close(pipeFds[0])
	defer syscall.Close(pipeFds[1])

	var sockFd int
	rawConn, _ := conn.SyscallConn()
	_ = rawConn.Control(func(fd uintptr) { sockFd = int(fd) })

	fileFd := int(file.Fd())
	var totalTransferred int64
	chunk := 1024 * 1024 // 1 MB splice chunk

	for totalTransferred < totalExpected {
		bytesToSplice := chunk
		if rem := totalExpected - totalTransferred; rem < int64(bytesToSplice) {
			bytesToSplice = int(rem)
		}

		nIn, err := syscall.Splice(fileFd, nil, pipeFds[1], nil, bytesToSplice, syscall.SPLICE_F_MOVE|syscall.SPLICE_F_MORE)
		if nIn <= 0 || err != nil {
			if err == syscall.EAGAIN { continue }
			return totalTransferred, err
		}

		nOut, err := syscall.Splice(pipeFds[0], nil, sockFd, nil, int(nIn), syscall.SPLICE_F_MOVE|syscall.SPLICE_F_MORE)
		if nOut <= 0 || err != nil {
			if err == syscall.EAGAIN { continue }
			return totalTransferred, err
		}
		totalTransferred += nOut
	}
	return totalTransferred, nil
}

func timeDiff(start, end syscall.Timeval) float64 {
	sec := end.Sec - start.Sec
	usec := end.Usec - start.Usec
	return float64(sec) + float64(usec)/1e6
}

func ensurePayloadExists(path string, size int64) {
	if info, err := os.Stat(path); err == nil && info.Size() == size { return }
	fmt.Printf("[Lab] Generating sparse %d GB file...\n", size/(1024*1024*1024))
	f, err := os.Create(path)
	if err != nil { log.Fatalf("Create error: %v", err) }
	defer f.Close()
	_ = f.Truncate(size)
}
```

---

## Day 3-4: The Rust Challenge (Starter Skeleton)

Your engineering mission for Days 3 and 4 is to build the high-performance Rust streamer using direct `io_uring` submission and completion queues.

### The 3 Specific Rust Systems Concepts You Will Fight

1. **Buffer Lifetime and Ownership in Asynchronous Completion:**
   In standard synchronous code or readiness-based async (`epoll`), you pass a mutable reference `&mut [u8]` to a read function. With `io_uring`, the kernel holds the physical memory address *after* the submission call returns. If the future is dropped or the buffer goes out of scope while the kernel DMA is still writing, you invoke undefined behavior (use-after-free). You must ensure the buffer has a `'static` lifetime or is strictly owned across submission and harvesting.

2. **Raw File Descriptor Safety (`AsRawFd` vs `OwnedFd`):**
   Rust strictly enforces file descriptor ownership via RAII. If a `File` or `TcpStream` is closed while an SQE referencing its integer FD is pending in the kernel ring, the descriptor might be reallocated by another thread, causing your pending I/O to read from or corrupt an unrelated socket!

3. **Lock-Free Ring Synchronization & CQ Reaping:**
   Unlike synchronous APIs where results return immediately, the `io_uring` Submission Queue (SQ) and Completion Queue (CQ) are decoupled. You must manage queue depth, handle partial read/write returns, advance the file offset correctly, and flush pending entries via `submit_and_wait()`.

### Starter Skeleton: `src/main.rs`

```rust
use clap::{Parser, ValueEnum};
use io_uring::{opcode, types, IoUring};
use std::fs::File;
use std::io;
use std::net::SocketAddr;
use std::os::fd::{AsRawFd, RawFd};
use std::path::Path;
use std::time::Instant;
use tokio::net::{TcpListener, TcpStream};

const PORT: u16 = 9090;
const PAYLOAD_PATH: &str = "10gb_lab_payload.dat";
const PAYLOAD_SIZE: u64 = 10 * 1024 * 1024 * 1024; // 10 GB
const QUEUE_DEPTH: u32 = 64;
const CHUNK_SIZE: usize = 128 * 1024; // 128 KB buffer

#[derive(ValueEnum, Clone, Copy, Debug)]
enum Mode {
    Sendfile,
    IoUring,
}

#[derive(Parser, Debug)]
struct Args {
    #[arg(short, long, value_enum, default_value_t = Mode::IoUring)]
    mode: Mode,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    ensure_payload_exists(PAYLOAD_PATH, PAYLOAD_SIZE)?;

    let addr: SocketAddr = format!("0.0.0.0:{}", PORT).parse()?;
    let listener = TcpListener::bind(addr).await?;
    println!("[Rust Lab] Listening on {} | Mode: {:?}", addr, args.mode);

    loop {
        let (stream, peer) = listener.accept().await?;
        println!("[Rust Lab] Client connected: {}", peer);

        tokio::task::spawn_blocking(move || {
            let file = File::open(PAYLOAD_PATH).expect("Failed to open payload");
            let file_fd = file.as_raw_fd();
            let sock_fd = stream.as_raw_fd();

            let start = Instant::now();
            let transferred = match args.mode {
                Mode::Sendfile => run_sendfile_challenge(file_fd, sock_fd, PAYLOAD_SIZE),
                Mode::IoUring => run_io_uring_challenge(file_fd, sock_fd, PAYLOAD_SIZE),
            }.expect("Streaming transfer failed");

            let elapsed = start.elapsed().as_secs_f64();
            let gbps = (transferred as f64 * 8.0) / (elapsed * 1e9);
            println!("[Rust Lab] Done! Transferred {} MB in {:.2}s ({:.2} Gbps)",
                transferred / (1024 * 1024), elapsed, gbps);
        }).await?;
    }
}

// TODO: LAB EXERCISE - Implement the io_uring submission/completion loop
fn run_io_uring_challenge(file_fd: RawFd, sock_fd: RawFd, total_bytes: u64) -> io::Result<u64> {
    // Step 1: Initialize the IoUring instance with QUEUE_DEPTH
    let mut ring = IoUring::new(QUEUE_DEPTH)?;
    let mut buffer = vec![0u8; CHUNK_SIZE];
    let mut file_offset: u64 = 0;
    let mut total_transferred: u64 = 0;

    println!("[Challenge] Entering io_uring execution loop...");

    while total_transferred < total_bytes {
        let to_read = std::cmp::min(CHUNK_SIZE as u64, total_bytes - total_transferred) as u32;

        // -------------------------------------------------------------------------
        // STEP 2: Prepare and push the Read SQE
        // HINT: Use opcode::Read::new(types::Fd(file_fd), buffer.as_mut_ptr(), to_read)
        //       .offset(file_offset).build().user_data(0x01)
        // -------------------------------------------------------------------------
        let read_sqe = opcode::Read::new(types::Fd(file_fd), buffer.as_mut_ptr(), to_read)
            .offset(file_offset)
            .build()
            .user_data(0x01);

        unsafe {
            ring.submission()
                .push(&read_sqe)
                .map_err(|_| io::Error::new(io::ErrorKind::Other, "Submission queue full"))?;
        }

        // -------------------------------------------------------------------------
        // STEP 3: Submit SQE to kernel and wait for read completion
        // HINT: ring.submit_and_wait(1)
        // -------------------------------------------------------------------------
        ring.submit_and_wait(1)?;

        let cqe_read = ring
            .completion()
            .next()
            .ok_or_else(|| io::Error::new(io::ErrorKind::Other, "Missing read CQE"))?;

        let bytes_read = cqe_read.result();
        if bytes_read <= 0 {
            break;
        }

        // -------------------------------------------------------------------------
        // STEP 4: Prepare and push the Write SQE to the socket
        // HINT: Use opcode::Write::new(types::Fd(sock_fd), buffer.as_ptr(), bytes_read as u32)
        //       .build().user_data(0x02)
        // -------------------------------------------------------------------------
        let write_sqe = opcode::Write::new(types::Fd(sock_fd), buffer.as_ptr(), bytes_read as u32)
            .build()
            .user_data(0x02);

        unsafe {
            ring.submission()
                .push(&write_sqe)
                .map_err(|_| io::Error::new(io::ErrorKind::Other, "Submission queue full"))?;
        }

        ring.submit_and_wait(1)?;

        let cqe_write = ring
            .completion()
            .next()
            .ok_or_else(|| io::Error::new(io::ErrorKind::Other, "Missing write CQE"))?;

        let bytes_written = cqe_write.result();
        if bytes_written <= 0 {
            break;
        }

        file_offset += bytes_written as u64;
        total_transferred += bytes_written as u64;
    }

    Ok(total_transferred)
}

fn run_sendfile_challenge(file_fd: RawFd, sock_fd: RawFd, total_bytes: u64) -> io::Result<u64> {
    let mut offset: i64 = 0;
    let mut sent: u64 = 0;
    let chunk = 16 * 1024 * 1024; // 16 MB

    while sent < total_bytes {
        let to_send = std::cmp::min(chunk, (total_bytes - sent) as usize);
        match nix::sys::sendfile::sendfile(sock_fd, file_fd, Some(&mut offset), to_send) {
            Ok(0) => break,
            Ok(n) => sent += n as u64,
            Err(nix::errno::Errno::EAGAIN) => continue,
            Err(e) => return Err(io::Error::from_raw_os_error(e as i32)),
        }
    }
    Ok(sent)
}

fn ensure_payload_exists(path: &str, size: u64) -> io::Result<()> {
    if Path::new(path).exists() && std::fs::metadata(path)?.len() == size { return Ok(()); }
    let f = File::create(path)?;
    f.set_len(size)?;
    Ok(())
}
```

---

## Day 5 (Friday): Mob Review & Kernel Profiling Lab

On Friday, gather your team to execute the benchmarks and profile the hardware performance counters under Linux.

### Step-by-Step Profiling Procedure

1. **Terminal 1: Start Server in Baseline Buffered Mode**
   ```bash
   go run lab_server.go -mode=buffered -bufsize=64
   ```

2. **Terminal 2: Launch Client with Hardware Counter Profiling**
   Wrap `perf stat` around `socat` draining to `/dev/null`:
   ```bash
   perf stat -e context-switches,cpu-migrations,page-faults,cycles,instructions,L1-dcache-load-misses,LLC-load-misses \
     socat -b 1048576 TCP:127.0.0.1:9090 /dev/null
   ```

3. **Terminal 3: Monitor Kernel CPU Split Concurrently**
   ```bash
   mpstat -P ALL 1
   ```

4. **Repeat for Zero-Copy Modes:**
   - Run server with `-mode=sendfile` and profile.
   - Run server with `-mode=splice` and profile.
   - Run the Rust `io_uring` binary and profile.

### Benchmark Results Comparison Table

Fill in this table during the mob review session:

| Architectural Metric | Buffered (64 KB) | Buffered (1 MB) | Zero-Copy sendfile | Linux splice | Rust io_uring |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Throughput (Gbps)** | ~4.2 Gbps | ~7.8 Gbps | ~28.5 Gbps | ~26.1 Gbps | ~32.0 Gbps |
| **Elapsed Time (s)** | ~19.5 s | ~10.4 s | ~2.9 s | ~3.1 s | ~2.6 s |
| **Voluntary Context Switches** | > 320,000 | > 20,000 | < 150 | < 250 | **< 20** |
| **Involuntary Context Switches**| > 1,500 | > 800 | < 50 | < 50 | **< 10** |
| **CPU Migrations** | > 120 | > 40 | < 5 | < 5 | **< 2** |
| **Instructions Per Cycle (IPC)**| ~0.65 | ~0.95 | ~2.10 | ~1.95 | ~2.45 |
| **LLC (L3) Cache Misses** | > 45,000,000 | > 18,000,000 | < 120,000 | < 250,000 | **< 80,000** |
| **Host CPU % (Sys vs User)** | 65% Sys / 35% Usr| 50% Sys / 50% Usr| 98% Sys / 2% Usr | 95% Sys / 5% Usr | **< 3% Total** |

---

## Friday Mob Review: 7 Technical Questions & Deep Systems Answers

### Question 1: Why did the context switch count plummet from over 300,000 down to less than 150 between buffered copying and `sendfile`?
**Systems Answer:** In buffered mode with a 64KB chunk, streaming 10GB requires $\approx 163,840$ `read()` calls and $\approx 163,840$ `write()` calls. Every individual syscall forces a hardware privilege level transition (Ring 3 to Ring 0) and stack swap, totaling at least 327,680 voluntary context switches. With `sendfile`, the kernel streams up to gigabytes in a single invocation or loops inside the driver; the thread yields to the scheduler only when the socket send buffer fills up and blocks, cutting context switches by 99.9%.

### Question 2: What is the physical mechanism causing Last-Level Cache (LLC / L3) misses to collapse during zero-copy transfers?
**Systems Answer:** In standard buffered copying, the CPU core executes `memcpy`, loading each cache line (64 bytes) from the page cache into L1 data cache, then L2, then L3, and writing it out to the destination buffer. Because a 10GB payload dwarfs the size of CPU L3 caches (typically 16MB to 64MB), every single megabyte copied sweeps through and completely flushes the CPU caches, causing continuous cache line evictions. In zero-copy `sendfile` with Scatter-Gather DMA (`NETIF_F_SG`), the CPU never reads the payload bytes into its registers; the physical memory addresses of the page cache are passed directly to the NIC controller, leaving CPU caches completely unpolluted.

### Question 3: Why does `splice(2)` require a pipe descriptor as an intermediary, and what is `struct pipe_buffer` actually doing in kernel memory?
**Systems Answer:** A Linux pipe is architecturally an in-kernel circular array of `struct pipe_buffer` descriptors, each holding a pointer to a physical `struct page`. Linux uses the pipe as a standardized, type-agnostic zero-copy conduit. When splicing from a file or socket into a pipe, the kernel does not copy bytes; it increments the page's reference counter (`page_ref_inc`) and places the page pointer in the pipe buffer. When splicing from the pipe to the socket, the kernel extracts those page pointers and assigns them to the outgoing `sk_buff`.

### Question 4: In `io_uring`, why does `IORING_SETUP_SQPOLL` eliminate syscalls entirely, and what are the trade-offs regarding dedicated CPU cores?
**Systems Answer:** With `SQPOLL`, the kernel spawns a dedicated kernel kthread (`io_uring-sq`) pinned to a CPU core that runs a continuous polling loop checking the SQ ring's tail pointer in shared memory. When user-space writes an SQE and issues an atomic release store on `tail`, the kernel thread immediately sees the new entry and processes it without any `SYSCALL` instruction being executed. The trade-off is dedicated CPU utilization: the polling thread spins and consumes 100% of its assigned CPU core even when idle, unless configured with an idle timeout (`sq_thread_idle`).

### Question 5: How does the kernel guarantee that a page being transmitted via TCP `MSG_ZEROCOPY` is not corrupted, and what happens if user-space overwrites it?
**Systems Answer:** The kernel pins the user-space physical pages using `get_user_pages_fast()`, preventing the OS virtual memory subsystem from swapping or remapping the physical page frames. However, the kernel *cannot* prevent the user-space thread from writing new data into that virtual address! If user-space mutates the buffer before the NIC finishes DMA transmission, corrupted or torn packet data is clocked out onto the wire. To prevent this, the application must await completion notifications on the socket error queue (`MSG_ERRQUEUE`).

### Question 6: Why does C# `Span<T>` fail to represent buffers submitted to `io_uring`, while Rust's owned buffer models succeed?
**Systems Answer:** `Span<T>` in C# is a ref struct allocated strictly on the execution stack and cannot cross `await` boundaries or escape to asynchronous threads. More fundamentally, `io_uring` requires the buffer to remain valid and immovable at a fixed physical or virtual address across an arbitrary time window until a completion event arrives in the CQ. Rust's ownership model allows transferring full ownership of a heap buffer (`Vec<u8>`) to the future or completion state machine, guaranteeing at compile-time that no other thread can read, write, or drop the buffer until the kernel releases it.

### Question 7: Under what conditions will zero-copy `sendfile(2)` perform *worse* than a standard buffered copy?
**Systems Answer:** Zero-copy performs worse under two conditions:
1. **Small payload sizes (< 8KB-16KB):** The overhead of page table pinning, scatter-gather descriptor allocation, and error-queue bookkeeping exceeds the negligible cost of a tiny CPU `memcpy`.
2. **Heavy in-flight data manipulation:** If the data must be encrypted with TLS, compressed with zstd, or inspected by an application-level firewall, the CPU *must* read every byte anyway. Passing through zero-copy primitives only to copy the data back for transformation introduces synchronization bottlenecks.

---

## Team Sign-Off Checklist (7 Yes/No Validation Gates)

Every team member must be able to answer **YES** to all 7 criteria before this lab is signed off:

- [ ] **1. Kernel Metrics Verification:** Did you verify via `perf stat` that context switches decreased by at least 95% when moving from buffered copying to `sendfile`?
- [ ] **2. Cache Invalidation Measurement:** Did you record a dramatic drop in LLC (Last-Level Cache) load misses during the zero-copy benchmarks?
- [ ] **3. Netpoller Awareness:** Can you explain why wrapping an `*os.File` in Go silently disables the `sendfile` fast path inside `io.Copy`?
- [ ] **4. Ownership Soundness:** In Rust, do you understand why passing an unpinned reference `&mut [u8]` to `io_uring` is a memory safety hazard?
- [ ] **5. CPU Time Segregation:** Did you observe the shift from high `sys` time in buffered I/O to near-zero CPU time in `io_uring`?
- [ ] **6. Scatter-Gather DMA:** Can you describe to a peer how the Linux kernel `sk_buff` interacts with physical page cache frames without copying memory?
- [ ] **7. Completion Queue Harvesting:** Did you successfully run and verify the Rust `io_uring` submission and completion ring loop?

---

## Stretch Goals for Fast Learners

1. **Registered Fixed Buffers (`IORING_REGISTER_BUFFERS`):** Modify the Rust `io_uring` implementation to pre-register a pool of memory buffers using `ring.submitter().register_buffers()`. Measure the additional throughput gain from bypassing page table walk overhead.
2. **Kernel Polling Engine (`IORING_SETUP_SQPOLL`):** Enable kernel submission polling and isolate the kernel kthread to a dedicated CPU core using `taskset` or `numactl`. Benchmark the streamer and observe literal **0 context switches**.
3. **Zero-Copy TCP Proxy via `splice(2)`:** Implement a two-way network proxy in Go or Rust that accepts incoming client TCP connections and splices data directly to an upstream backend server using dual kernel pipes without copying any bytes through user memory.
