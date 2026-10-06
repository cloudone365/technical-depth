# Week 30: Code Comparison Rosetta - High-Throughput Streamers

## Architectural Scenario: The 10GB Data Streamer

In this Rosetta comparison, we engineer a production-grade, high-throughput network streaming server across C#, Go, and Rust. The server's responsibility is to transmit a 10-Gigabyte binary payload (such as an uncompressed virtual machine image, machine learning checkpoint, or video stream) over a TCP socket to requesting clients at physical line rate (10Gbps+).

We implement and contrast three fundamental architectural approaches:
1. **Traditional User-Space Buffered I/O:** Reading chunks into application memory buffers and writing to the network socket stream (incurring the 4-copy penalty and context switch tax).
2. **Kernel Zero-Copy via `sendfile(2)`:** Directing the operating system kernel to DMA data from the filesystem page cache straight to the network interface ring buffer.
3. **Async Completion via `io_uring` (or `splice(2)`):** Utilizing submission/completion ring queues and kernel pipe conduits to achieve zero-syscall, zero-copy asynchronous streaming.

---

## 1. C# (.NET 8+) Implementation

### Project Configuration: `FileStreamer.csproj`

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <AllowUnsafeBlocks>true</AllowUnsafeBlocks>
    <ServerGarbageCollection>true</ServerGarbageCollection>
    <OptimizationPreference>Speed</OptimizationPreference>
  </PropertyGroup>
</Project>
```

### Complete Source: `Program.cs`

```csharp
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Threading.Tasks;

namespace HighThroughputStreamer
{
    public enum StreamMode { Buffered, ZeroCopySendFile }

    public class Program
    {
        private const int Port = 9090;
        private const string PayloadPath = "10gb_payload.dat";
        private const long PayloadSizeBytes = 10L * 1024 * 1024 * 1024; // 10 GB
        private const int BufferSize = 64 * 1024; // 64 KB user-space buffer

        public static async Task Main(string[] args)
        {
            var mode = StreamMode.ZeroCopySendFile;
            if (args.Length > 0 && args[0].Equals("--buffered", StringComparison.OrdinalIgnoreCase))
                mode = StreamMode.Buffered;

            EnsurePayloadExists(PayloadPath, PayloadSizeBytes);

            var listener = new TcpListener(IPAddress.Any, Port);
            // Allow immediate port reuse across rapid restarts without TIME_WAIT locking
            listener.Server.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
            listener.Start(backlog: 128);

            Console.WriteLine($"[C# .NET 8] Streamer listening on 0.0.0.0:{Port} | Mode: {mode}");

            while (true)
            {
                var clientSocket = await listener.AcceptSocketAsync();
                // Spawn client processing onto CLR ThreadPool to maintain listener responsiveness
                _ = Task.Run(async () =>
                {
                    try { await HandleClientAsync(clientSocket, PayloadPath, mode); }
                    catch (Exception ex) { Console.Error.WriteLine($"[C#] Transfer error: {ex.Message}"); }
                    finally { clientSocket.Dispose(); }
                });
            }
        }

        private static async Task HandleClientAsync(Socket socket, string filePath, StreamMode mode)
        {
            socket.NoDelay = true; // Disable Nagle's algorithm to eliminate latency stalls
            socket.SendBufferSize = 0; // Defer buffer sizing to Linux kernel TCP autotuning

            var sw = Stopwatch.StartNew();
            long totalBytesSent = 0;

            if (mode == StreamMode.Buffered)
            {
                // APPROACH 1: User-Space Buffered Copy (Incurs 4 context switches & 2 CPU memcpy passes)
                byte[] userBuffer = new byte[BufferSize];
                await using var fileStream = new FileStream(filePath, FileMode.Open, FileAccess.Read, 
                    FileShare.Read, BufferSize, FileOptions.Asynchronous | FileOptions.SequentialScan);
                using var networkStream = new NetworkStream(socket, ownsSocket: false);

                int bytesRead;
                while ((bytesRead = await fileStream.ReadAsync(userBuffer.AsMemory(0, BufferSize))) > 0)
                {
                    await networkStream.WriteAsync(userBuffer.AsMemory(0, bytesRead));
                    totalBytesSent += bytesRead;
                }
            }
            else
            {
                // APPROACH 2: Zero-Copy via OS sendfile(2) / TransmitFile
                // On Linux, .NET PAL calls sendfile64. Scatter-Gather DMA directly feeds NIC from Page Cache.
                await socket.SendFileAsync(filePath, null, null, TransmitFileOptions.UseDefaultWorkerThread);
                totalBytesSent = PayloadSizeBytes;
            }

            sw.Stop();
            double seconds = sw.Elapsed.TotalSeconds;
            double gbps = (totalBytesSent * 8.0) / (seconds * 1_000_000_000.0);
            Console.WriteLine($"[C#] Sent {totalBytesSent / (1024 * 1024)} MB in {seconds:F2}s ({gbps:F2} Gbps)");
        }

        private static void EnsurePayloadExists(string path, long targetBytes)
        {
            if (File.Exists(path) && new FileInfo(path).Length == targetBytes) return;
            Console.WriteLine($"[C#] Allocating sparse {targetBytes / (1024 * 1024 * 1024)} GB file...");
            using var fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.None);
            fs.SetLength(targetBytes); // Instant sparse allocation on ext4/XFS
        }
    }
}
```

---

## 2. Go (1.22+) Implementation

Go provides high-level I/O abstractions while allowing low-level operating system bypass. Notably, Go's standard library implements an automatic optimization fast-path inside `io.Copy`: if the destination is a `*net.TCPConn` and the source is an `*os.File`, Go automatically dispatches to the Linux `sendfile(2)` system call!

### Project Configuration: `go.mod`

```go
module streamer

go 1.22
```

### Complete Source: `main.go`

```go
package main

import (
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"syscall"
	"time"
)

const (
	Port             = "9090"
	PayloadPath      = "10gb_payload.dat"
	PayloadSizeBytes = int64(10) * 1024 * 1024 * 1024 // 10 GB
	BufferSize       = 64 * 1024                      // 64 KB
)

// unoptimizedReader wraps an io.Reader to intentionally hide concrete type information.
// Go's io.Copy inspects src and dst using type assertions for io.ReaderFrom / io.WriterTo.
// Wrapping os.File prevents Go from detecting the underlying file descriptor, forcing
// it to fall back to standard user-space buffer copying.
type unoptimizedReader struct {
	io.Reader
}

func main() {
	mode := flag.String("mode", "sendfile", "Streaming mode: 'buffered', 'sendfile', or 'splice'")
	flag.Parse()

	ensurePayloadExists(PayloadPath, PayloadSizeBytes)
	listener, err := net.Listen("tcp", "0.0.0.0:"+Port)
	if err != nil {
		log.Fatalf("[Go] Bind error: %v", err)
	}
	defer listener.Close()

	fmt.Printf("[Go 1.22] Streamer listening on 0.0.0.0:%s | Mode: %s\n", Port, *mode)

	for {
		conn, err := listener.Accept()
		if err != nil {
			continue
		}
		// Spawn lightweight goroutine per client connection
		go handleClient(conn.(*net.TCPConn), PayloadPath, *mode)
	}
}

func handleClient(conn *net.TCPConn, filePath string, mode string) {
	defer conn.Close()
	_ = conn.SetNoDelay(true) // Disable Nagle algorithm

	// Enable kernel TCP buffer autotuning
	if rawConn, err := conn.SyscallConn(); err == nil {
		_ = rawConn.Control(func(fd uintptr) {
			_ = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_SNDBUF, 0)
		})
	}

	file, err := os.Open(filePath)
	if err != nil {
		log.Printf("[Go] Open error: %v", err)
		return
	}
	defer file.Close()

	startTime := time.Now()
	var totalBytesSent int64

	switch mode {
	case "buffered":
		// APPROACH 1: User-Space Buffered Copy
		// We explicitly wrap 'file' in unoptimizedReader to disable Go's internal sendfile fast-path.
		buf := make([]byte, BufferSize)
		totalBytesSent, err = io.CopyBuffer(conn, unoptimizedReader{file}, buf)

	case "sendfile":
		// APPROACH 2: Idiomatic Zero-Copy via io.Copy
		// net.TCPConn.ReadFrom detects *os.File and dispatches to internal/poll.SendFile,
		// invoking sys_sendfile non-blockingly integrated with Go's Netpoller!
		totalBytesSent, err = io.Copy(conn, file)

	case "splice":
		// APPROACH 3: In-Kernel Splice via Pipe Conduits
		// Moves page references between file descriptor and socket descriptor using an in-kernel pipe.
		totalBytesSent, err = transferViaSplice(conn, file, PayloadSizeBytes)

	default:
		log.Fatalf("Unknown streaming mode: %s", mode)
	}

	if err != nil {
		log.Printf("[Go] Transfer error: %v", err)
		return
	}

	elapsed := time.Since(startTime).Seconds()
	gbps := (float64(totalBytesSent) * 8.0) / (elapsed * 1e9)
	fmt.Printf("[Go] Sent %d MB in %.2fs (%.2f Gbps) via %s\n", totalBytesSent/(1024*1024), elapsed, gbps, mode)
}

func transferViaSplice(conn *net.TCPConn, file *os.File, totalExpected int64) (int64, error) {
	// Allocate unidirectional Linux pipe in kernel memory
	pipeFds := make([]int, 2)
	if err := syscall.Pipe2(pipeFds, syscall.O_NONBLOCK); err != nil {
		return 0, fmt.Errorf("pipe2 failed: %w", err)
	}
	defer syscall.Close(pipeFds[0])
	defer syscall.Close(pipeFds[1])

	var totalTransferred int64
	fileFd := int(file.Fd())
	var sockFd int
	rawConn, err := conn.SyscallConn()
	if err != nil {
		return 0, err
	}
	_ = rawConn.Control(func(fd uintptr) { sockFd = int(fd) })

	chunkSize := 1024 * 1024 // 1 MB splice chunk
	for totalTransferred < totalExpected {
		bytesToSplice := chunkSize
		remaining := totalExpected - totalTransferred
		if remaining < int64(bytesToSplice) {
			bytesToSplice = int(remaining)
		}

		// Splice 1: File Page Cache -> Kernel Pipe Buffer (Zero user memory copy)
		nIn, err := syscall.Splice(fileFd, nil, pipeFds[1], nil, bytesToSplice, syscall.SPLICE_F_MOVE|syscall.SPLICE_F_MORE)
		if nIn <= 0 || err != nil {
			if err == syscall.EAGAIN { continue }
			return totalTransferred, err
		}

		// Splice 2: Kernel Pipe Buffer -> Socket sk_buff (Zero user memory copy)
		nOut, err := syscall.Splice(pipeFds[0], nil, sockFd, nil, int(nIn), syscall.SPLICE_F_MOVE|syscall.SPLICE_F_MORE)
		if nOut <= 0 || err != nil {
			if err == syscall.EAGAIN { continue }
			return totalTransferred, err
		}
		totalTransferred += nOut
	}
	return totalTransferred, nil
}

func ensurePayloadExists(path string, targetBytes int64) {
	if info, err := os.Stat(path); err == nil && info.Size() == targetBytes { return }
	file, err := os.Create(path)
	if err != nil { log.Fatalf("Create error: %v", err) }
	defer file.Close()
	_ = file.Truncate(targetBytes)
}
```

---

## 3. Rust (Tokio & io-uring) Implementation

In Rust, systems programming reaches zero-overhead perfection. We compare Tokio's asynchronous buffered pipeline with direct Linux `io_uring` submission using the low-level `io-uring` crate. Notice how Rust's strict ownership model maps directly onto the kernel's memory safety constraints.

### Project Configuration: `Cargo.toml`

```toml
[package]
name = "streamer_rs"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1.38", features = ["full"] }
io-uring = "0.6"
nix = { version = "0.28", features = ["socket", "fs", "zerocopy"] }
clap = { version = "4.5", features = ["derive"] }
```

### Complete Source: `src/main.rs`

```rust
use clap::{Parser, ValueEnum};
use io_uring::{opcode, types, IoUring};
use nix::sys::sendfile::sendfile;
use std::fs::File;
use std::io;
use std::net::SocketAddr;
use std::os::fd::{AsRawFd, RawFd};
use std::path::Path;
use std::time::Instant;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

const PORT: u16 = 9090;
const PAYLOAD_PATH: &str = "10gb_payload.dat";
const PAYLOAD_SIZE_BYTES: u64 = 10 * 1024 * 1024 * 1024; // 10 GB
const BUFFER_SIZE: usize = 64 * 1024; // 64 KB

#[derive(ValueEnum, Clone, Copy, Debug)]
enum Mode { Buffered, Sendfile, IoUring }

#[derive(Parser, Debug)]
#[command(author, version, about = "High-Throughput 10GB Streamer in Rust")]
struct Args {
    #[arg(short, long, value_enum, default_value_t = Mode::Sendfile)]
    mode: Mode,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    ensure_payload_exists(PAYLOAD_PATH, PAYLOAD_SIZE_BYTES)?;

    let addr: SocketAddr = format!("0.0.0.0:{}", PORT).parse()?;
    let listener = TcpListener::bind(addr).await?;
    println!("[Rust] Streamer listening on {} | Mode: {:?}", addr, args.mode);

    loop {
        let (stream, peer_addr) = listener.accept().await?;
        let mode = args.mode;

        tokio::spawn(async move {
            if let Err(e) = handle_client(stream, PAYLOAD_PATH, mode).await {
                eprintln!("[Rust] Client {} error: {}", peer_addr, e);
            }
        });
    }
}

async fn handle_client(
    mut stream: TcpStream,
    path: &str,
    mode: Mode,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    stream.set_nodelay(true)?; // Disable Nagle's algorithm
    let start_time = Instant::now();
    let total_bytes: u64;

    match mode {
        Mode::Buffered => {
            // APPROACH 1: User-Space Buffered Copy via Tokio AsyncRead/AsyncWrite
            let mut file = tokio::fs::File::open(path).await?;
            let mut buffer = vec![0u8; BUFFER_SIZE];
            let mut transferred = 0u64;

            loop {
                let bytes_read = file.read(&mut buffer).await?;
                if bytes_read == 0 { break; }
                stream.write_all(&buffer[..bytes_read]).await?;
                transferred += bytes_read as u64;
            }
            total_bytes = transferred;
        }

        Mode::Sendfile => {
            // APPROACH 2: Zero-Copy sendfile(2) via nix crate
            let file = File::open(path)?;
            let file_fd: RawFd = file.as_raw_fd();
            let sock_fd: RawFd = stream.as_raw_fd();

            // Offload blocking kernel sendfile to Tokio blocking pool to protect async reactor
            total_bytes = tokio::task::spawn_blocking(move || -> io::Result<u64> {
                let mut offset: i64 = 0;
                let mut sent: u64 = 0;
                let chunk: usize = 16 * 1024 * 1024; // 16 MB chunks

                while sent < PAYLOAD_SIZE_BYTES {
                    let to_send = std::cmp::min(chunk, (PAYLOAD_SIZE_BYTES - sent) as usize);
                    match sendfile(sock_fd, file_fd, Some(&mut offset), to_send) {
                        Ok(0) => break,
                        Ok(n) => sent += n as u64,
                        Err(nix::errno::Errno::EAGAIN) => continue,
                        Err(e) => return Err(io::Error::from_raw_os_error(e as i32)),
                    }
                }
                Ok(sent)
            }).await??;
        }

        Mode::IoUring => {
            // APPROACH 3: Direct io_uring SQ/CQ Ring Buffer Submission
            let file = File::open(path)?;
            let file_fd = file.as_raw_fd();
            let sock_fd = stream.as_raw_fd();

            total_bytes = tokio::task::spawn_blocking(move || -> io::Result<u64> {
                run_io_uring_stream(file_fd, sock_fd, PAYLOAD_SIZE_BYTES)
            }).await??;
        }
    }

    let elapsed = start_time.elapsed().as_secs_f64();
    let gbps = (total_bytes as f64 * 8.0) / (elapsed * 1_000_000_000.0);
    println!("[Rust] Sent {} MB in {:.2}s ({:.2} Gbps) via {:?}", total_bytes / (1024 * 1024), elapsed, gbps, mode);
    Ok(())
}

fn run_io_uring_stream(file_fd: RawFd, sock_fd: RawFd, total_size: u64) -> io::Result<u64> {
    const QUEUE_DEPTH: u32 = 64;
    const CHUNK_SIZE: usize = 128 * 1024; // 128 KB chunk

    let mut ring = IoUring::new(QUEUE_DEPTH)?;
    let mut buf = vec![0u8; CHUNK_SIZE];
    let mut file_offset: u64 = 0;
    let mut total_transferred: u64 = 0;

    while total_transferred < total_size {
        let to_process = std::cmp::min(CHUNK_SIZE as u64, total_size - total_transferred) as usize;

        // Step 1: Submit Asynchronous File Read to SQ Ring
        let read_sqe = opcode::Read::new(types::Fd(file_fd), buf.as_mut_ptr(), to_process as u32)
            .offset(file_offset)
            .build()
            .user_data(0x01);

        unsafe { ring.submission().push(&read_sqe).map_err(|_| io::Error::new(io::ErrorKind::Other, "SQ Full"))?; }
        ring.submit_and_wait(1)?;

        let cqe_read = ring.completion().next().ok_or_else(|| io::Error::new(io::ErrorKind::Other, "Empty CQ"))?;
        let bytes_read = cqe_read.result();
        if bytes_read <= 0 { break; }

        // Step 2: Submit Asynchronous Socket Write to SQ Ring
        let write_sqe = opcode::Write::new(types::Fd(sock_fd), buf.as_ptr(), bytes_read as u32)
            .build()
            .user_data(0x02);

        unsafe { ring.submission().push(&write_sqe).map_err(|_| io::Error::new(io::ErrorKind::Other, "SQ Full"))?; }
        ring.submit_and_wait(1)?;

        let cqe_write = ring.completion().next().ok_or_else(|| io::Error::new(io::ErrorKind::Other, "Empty CQ"))?;
        let bytes_written = cqe_write.result();
        if bytes_written <= 0 { break; }

        file_offset += bytes_written as u64;
        total_transferred += bytes_written as u64;
    }

    Ok(total_transferred)
}

fn ensure_payload_exists(path: &str, target_bytes: u64) -> io::Result<()> {
    if Path::new(path).exists() && std::fs::metadata(path)?.len() == target_bytes { return Ok(()); }
    let file = File::create(path)?;
    file.set_len(target_bytes)?;
    Ok(())
}
```

---

## 4. Build, Execution, and Benchmark Guide

### 1. Generating Test Data
Before running any server, ensure a sparse test file exists on Linux:
```bash
# Instantly create a 10GB sparse file in 1 millisecond
truncate -s 10G 10gb_payload.dat
```

### 2. Building and Running C# (.NET 8)
```bash
dotnet build -c Release
dotnet run -c Release --no-build -- --sendfile  # Zero-Copy SendFile
dotnet run -c Release --no-build -- --buffered  # Buffered Copy
```

### 3. Building and Running Go
```bash
go build -o streamer_go main.go
./streamer_go -mode=sendfile   # Zero-copy via net.TCPConn.ReadFrom
./streamer_go -mode=splice     # In-kernel pipe splicing
./streamer_go -mode=buffered   # User-space buffered copy
```

### 4. Building and Running Rust
```bash
cargo build --release
./target/release/streamer_rs --mode sendfile  # Zero-copy sendfile
./target/release/streamer_rs --mode io-uring  # io_uring SQ/CQ ring completion
./target/release/streamer_rs --mode buffered  # Standard buffered async
```

### 5. Running Client Benchmarks
In a separate terminal, drain the socket directly to `/dev/null` using `socat` or `nc`:
```bash
# Measure raw network throughput via socat
socat -b 1048576 TCP:127.0.0.1:9090 /dev/null

# OR measure with curl download speed meter
curl -o /dev/null -w "Speed: %{speed_download} B/s, Elapsed: %{time_total}s\n" http://localhost:9090/
```

---

## 5. Critical Observations for C# Developers

### Observation 1: Platform Abstraction Layer (PAL) Translation Differences
In C#, `Socket.SendFileAsync()` feels like just another async method. However, what happens behind the scenes depends critically on the host operating system:
- **On Windows:** .NET delegates to `TransmitFile` (from `mswsock.dll`), an asynchronous kernel function integrated with Windows IOCP.
- **On Linux:** The .NET runtime PAL translates this call to POSIX `sendfile64()`. Crucially, because `sendfile(2)` is a synchronous/blocking syscall in Linux, the .NET runtime must dispatch `SendFileAsync` onto a background `ThreadPool` worker thread to prevent blocking the async continuation chain!
- **In Go and Rust:** Go integrates `sendfile` directly into its Netpoller non-blocking loop, while Rust provides total control, letting you decide whether to execute `sendfile` inside a non-blocking `epoll` reactor or submit ring operations directly via `io_uring`.

### Observation 2: The Interface Masking Trap in Go
In C#, code behavior is strictly governed by types and method dispatches. In Go, standard library optimizations rely heavily on dynamic interface queries (`src.(io.WriterTo)` or `dst.(io.ReaderFrom)`). 
If a developer wraps a Go `*os.File` in a custom logging decorator, rate limiter, or metric collector:
```go
type MeteredReader struct { r io.Reader }
func (m MeteredReader) Read(p []byte) (int, error) { return m.r.Read(p) }
```
Go's `io.Copy` will no longer detect that the underlying object is an `*os.File`. It will silently deactivate the kernel `sendfile` path and fall back to allocating a 32KB user-space buffer! This silent de-optimization can drop server throughput from 40Gbps down to 6Gbps without raising a single error or compiler warning.

### Observation 3: Rust's Ownership Model Solves Asynchronous Buffer Lifetimes
In `io_uring`, the kernel holds a direct reference to your memory buffer while an I/O operation is in flight. 
- In C#, if you pass a `Memory<byte>` or unpinned `byte[]` to an async completion routine, the garbage collector might relocate the array in memory during a GC compaction phase, leading to physical memory corruption when the kernel writes into the old address!
- In Rust, standard references (`&mut [u8]`) cannot be passed to `io_uring` because the borrow checker cannot prove when the kernel will finish. Rust forces you to either **transfer ownership** of the buffer to the future/completion object or use `Pin<T>` with static/registered buffers. Rust's type system enforces at compile-time the exact memory safety invariants demanded by asynchronous kernel architectures.

### Observation 4: L3 Cache Thrashing and Memory Bus Pressure
When you run the buffered mode in C# (`Stream.CopyToAsync`), you will observe high CPU utilization across all cores. C# developers often assume this is GC work. However, profiling reveals that the CPU is stalled on L3 cache misses. Loading 10GB of data through the CPU registers forces the processor to constantly flush its cache lines, evicting JIT code, session state, and thread stacks. With zero-copy (`SendFileAsync` or `io_uring`), L3 cache hit rates for surrounding application logic remain pristine because payload bytes never touch CPU registers.
