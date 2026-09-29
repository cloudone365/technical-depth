# Week 29: Code Comparison Rosetta - Daemons, Signals & IPC

## Scenario: The Resilient Production Linux Daemon

In high-reliability Linux infrastructure, microservices cannot simply crash or abruptly terminate when configuration changes or when deployment orchestrators roll out updates. A production daemon must adhere to the standard Unix service contract:
1. **Asynchronous HTTP Service:** Concurrently processes client requests with deterministic tracking of in-flight transactions.
2. **Dynamic Configuration Reload (`SIGHUP`):** When an operator or orchestration tool updates `/tmp/daemon_config.json`, sending `SIGHUP` triggers the daemon to reload and atomically swap configuration in memory without dropping connections, restarting the process, or interrupting active transactions.
3. **Graceful Connection Drain (`SIGTERM` / `SIGINT`):** When systemd, Kubernetes, or an operator issues `SIGTERM` or `SIGINT`, the daemon immediately closes its listening socket (rejecting new incoming connections) and allows active in-flight requests up to 15 seconds to complete before exiting cleanly with status code `0`.
4. **Zero-Copy Memory-Mapped IPC (`mmap`):** Reads high-frequency catalog metadata directly from a shared memory file (`/dev/shm/daemon_catalog.dat`). Instead of paying CPU serialization overhead, the daemon maps the page directly into its virtual address space and decodes structured headers using zero-copy pointer access.

Below are complete, production-grade, compilable implementations in **C# (.NET 8)**, **Go (1.22+)**, and **Rust (2021 Edition)**.

---

## 1. C# (.NET 8) Implementation

### Project Configuration (`DaemonService.csproj`)

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <AllowUnsafeBlocks>true</AllowUnsafeBlocks>
    <ServerGarbageCollection>true</ServerGarbageCollection>
  </PropertyGroup>
</Project>
```

### Source Code (`Program.cs`)

```csharp
using System;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Net;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace DaemonService;

// Binary header layout mapped directly over /dev/shm/daemon_catalog.dat
[StructLayout(LayoutKind.Sequential, Pack = 1)]
public struct CatalogHeader
{
    public uint Magic;                // Expected: 0x43415444 ("CATD")
    public uint Version;              // Data schema version
    public ulong RecordCount;         // Number of indexed records
    public ulong LastUpdatedTimestamp;// Unix epoch timestamp
}

public sealed class DaemonConfig
{
    public string ServiceName { get; init; } = "C# Systems Daemon";
    public int RequestTimeoutMs { get; init; } = 2500;
}

public static class Program
{
    // Volatile reference ensures cross-thread visibility without locking the request pipeline
    private static volatile DaemonConfig _currentConfig = new();
    private static long _activeTransactions = 0; // Deterministic drain tracking
    private const string ConfigPath = "/tmp/daemon_config.json";
    private const string MmapPath = "/dev/shm/daemon_catalog.dat";

    public static async Task Main(string[] args)
    {
        Console.WriteLine($"[C#] Daemon starting. PID: {Environment.ProcessId}");
        InitTestFiles();
        ReloadConfiguration();

        // MemoryMappedFile maps internally to the POSIX mmap() system call on Linux
        using var mmf = MemoryMappedFile.CreateFromFile(MmapPath, FileMode.Open, null, 0, MemoryMappedFileAccess.Read);
        using var accessor = mmf.CreateViewAccessor(0, 4096, MemoryMappedFileAccess.Read);
        using var cts = new CancellationTokenSource();

        // PosixSignalRegistration registers handlers through CoreCLR's PAL layer
        using var sigTerm = PosixSignalRegistration.Create(PosixSignal.SIGTERM, ctx => {
            ctx.Cancel = true; // Prevents default ungraceful process abortion by runtime
            Console.WriteLine("\n[C#] SIGTERM received. Initiating graceful drain...");
            cts.Cancel();
        });
        using var sigInt = PosixSignalRegistration.Create(PosixSignal.SIGINT, ctx => {
            ctx.Cancel = true;
            Console.WriteLine("\n[C#] SIGINT received. Initiating graceful drain...");
            cts.Cancel();
        });
        using var sigHup = PosixSignalRegistration.Create(PosixSignal.SIGHUP, ctx => {
            Console.WriteLine("\n[C#] SIGHUP received. Reloading configuration from disk...");
            ReloadConfiguration();
        });

        // Initialize HTTP listener on Linux loopback
        using var listener = new HttpListener();
        listener.Prefixes.Add("http://127.0.0.1:8080/");
        listener.Start();
        Console.WriteLine("[C#] HTTP listener active on http://127.0.0.1:8080/");

        _ = Task.Run(async () =>
        {
            while (!cts.Token.IsCancellationRequested)
            {
                try
                {
                    var ctx = await listener.GetContextAsync().ConfigureAwait(false);
                    Interlocked.Increment(ref _activeTransactions); // Track active in-flight request

                    _ = Task.Run(async () =>
                    {
                        try { await HandleRequestAsync(ctx, accessor).ConfigureAwait(false); }
                        finally { Interlocked.Decrement(ref _activeTransactions); }
                    });
                }
                catch (HttpListenerException) when (cts.Token.IsCancellationRequested) { break; }
                catch (Exception ex) { if (!cts.Token.IsCancellationRequested) Console.WriteLine($"Listener error: {ex.Message}"); }
            }
        });

        // Block until cancellation signal is received
        var tcs = new TaskCompletionSource<bool>();
        cts.Token.Register(() => tcs.TrySetResult(true));
        await tcs.Task.ConfigureAwait(false);

        // Graceful Drain: Stop accepting new TCP connections immediately
        listener.Stop();
        Console.WriteLine("[C#] Listener stopped. Draining in-flight transactions (Max 15s)...");

        using var timeoutCts = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        while (Interlocked.Read(ref _activeTransactions) > 0 && !timeoutCts.Token.IsCancellationRequested)
        {
            await Task.Delay(50).ConfigureAwait(false);
        }

        Console.WriteLine($"[C#] Drain finished. Active: {Interlocked.Read(ref _activeTransactions)}. Clean exit.");
    }

    private static unsafe void InspectMmapHeader(MemoryMappedViewAccessor accessor, out CatalogHeader header)
    {
        byte* ptr = null;
        accessor.SafeMemoryMappedViewHandle.AcquirePointer(ref ptr);
        try { header = Unsafe.Read<CatalogHeader>(ptr); } // Zero-copy dereference directly over mapped page
        finally { accessor.SafeMemoryMappedViewHandle.ReleasePointer(); }
    }

    private static async Task HandleRequestAsync(HttpListenerContext ctx, MemoryMappedViewAccessor accessor)
    {
        var config = _currentConfig;
        InspectMmapHeader(accessor, out var header);
        await Task.Delay(40).ConfigureAwait(false); // Simulate processing

        string response = JsonSerializer.Serialize(new {
            Service = config.ServiceName,
            InFlight = Interlocked.Read(ref _activeTransactions),
            CatalogMagic = $"0x{header.Magic:X}",
            CatalogRecords = header.RecordCount
        });

        byte[] buf = Encoding.UTF8.GetBytes(response + "\n");
        ctx.Response.ContentType = "application/json";
        ctx.Response.ContentLength64 = buf.Length;
        await ctx.Response.OutputStream.WriteAsync(buf, 0, buf.Length).ConfigureAwait(false);
        ctx.Response.Close();
    }

    private static void ReloadConfiguration()
    {
        try
        {
            if (File.Exists(ConfigPath))
            {
                var loaded = JsonSerializer.Deserialize<DaemonConfig>(File.ReadAllText(ConfigPath));
                if (loaded != null) { _currentConfig = loaded; Console.WriteLine($"[C#] Config reloaded: {loaded.ServiceName}"); }
            }
        }
        catch (Exception ex) { Console.WriteLine($"[C#] Config load error: {ex.Message}"); }
    }

    private static void InitTestFiles()
    {
        if (!File.Exists(MmapPath))
        {
            using var fs = new FileStream(MmapPath, FileMode.Create, FileAccess.Write);
            using var w = new BinaryWriter(fs);
            w.Write(0x43415444); w.Write(1u); w.Write(500000UL); w.Write((ulong)DateTimeOffset.UtcNow.ToUnixTimeSeconds());
            fs.SetLength(4096);
        }
        if (!File.Exists(ConfigPath))
            File.WriteAllText(ConfigPath, JsonSerializer.Serialize(new DaemonConfig()));
    }
}
```

---

## 2. Go (1.22+) Implementation

### Project Configuration (`go.mod`)

```go
module systems_daemon

go 1.22

require golang.org/x/sys v0.20.0
```

### Source Code (`main.go`)

```go
package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/unix"
)

// CatalogHeader represents the 1:1 binary memory layout mapped from /dev/shm
type CatalogHeader struct {
	Magic                uint32 // 0x43415444 ("CATD")
	Version              uint32
	RecordCount          uint64
	LastUpdatedTimestamp uint64
}

type DaemonConfig struct {
	ServiceName      string `json:"ServiceName"`
	RequestTimeoutMs int    `json:"RequestTimeoutMs"`
}

const (
	configPath = "/tmp/daemon_config.json"
	mmapPath   = "/dev/shm/daemon_catalog.dat"
)

func main() {
	fmt.Printf("[Go] Daemon starting. PID: %d\n", os.Getpid())
	initTestFiles()

	// atomic.Pointer provides race-free, lock-free dynamic configuration swapping
	var configPtr atomic.Pointer[DaemonConfig]
	loadConfig(&configPtr)
	var activeTransactions atomic.Int64

	// Open shared memory file descriptor on Linux tmpfs
	fd, err := unix.Open(mmapPath, unix.O_RDWR, 0644)
	if err != nil {
		log.Fatalf("[Go] Open failed: %v", err)
	}
	defer unix.Close(fd)

	// MAP_SHARED maps pages directly into virtual memory, shared across processes
	mmapData, err := unix.Mmap(fd, 0, 4096, unix.PROT_READ|unix.PROT_WRITE, unix.MAP_SHARED)
	if err != nil {
		log.Fatalf("[Go] mmap failed: %v", err)
	}
	defer unix.Munmap(mmapData)

	// Zero-copy pointer cast directly over the mapped virtual memory buffer
	header := (*CatalogHeader)(unsafe.Pointer(&mmapData[0]))
	fmt.Printf("[Go] Mapped catalog. Magic=0x%X, Records=%d\n", header.Magic, header.RecordCount)

	mux := http.NewServeMux()
	mux.HandleFunc("/work", func(w http.ResponseWriter, r *http.Request) {
		activeTransactions.Add(1)
		defer activeTransactions.Add(-1)

		cfg := configPtr.Load()
		time.Sleep(40 * time.Millisecond) // Simulate work

		resp := map[string]any{
			"Service":        cfg.ServiceName,
			"InFlight":       activeTransactions.Load(),
			"CatalogMagic":   fmt.Sprintf("0x%X", header.Magic),
			"CatalogRecords": header.RecordCount,
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(resp)
	})

	srv := &http.Server{Addr: "127.0.0.1:8080", Handler: mux}
	go func() {
		// Go's netpoller transparently manages readiness via edge-triggered epoll
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[Go] Server error: %v", err)
		}
	}()
	fmt.Println("[Go] HTTP listener active on http://127.0.0.1:8080/")

	// Buffered signal channel prevents loss of signals during runtime trampoline dispatch
	sigChan := make(chan os.Signal, 8)
	signal.Notify(sigChan, syscall.SIGTERM, syscall.SIGINT, syscall.SIGHUP)

	for sig := range sigChan {
		switch sig {
		case syscall.SIGHUP:
			fmt.Println("\n[Go] SIGHUP received. Reloading configuration from disk...")
			loadConfig(&configPtr)

		case syscall.SIGTERM, syscall.SIGINT:
			fmt.Printf("\n[Go] %v received. Initiating graceful drain...\n", sig)

			// Shutdown stops listening and drains connections up to 15s timeout
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()

			if err := srv.Shutdown(ctx); err != nil {
				fmt.Printf("[Go] Shutdown error: %v\n", err)
			}
			fmt.Printf("[Go] Drain complete. Active: %d. Clean exit.\n", activeTransactions.Load())
			return
		}
	}
}

func loadConfig(ptr *atomic.Pointer[DaemonConfig]) {
	data, err := os.ReadFile(configPath)
	if err != nil { return }
	var cfg DaemonConfig
	if err := json.Unmarshal(data, &cfg); err == nil {
		ptr.Store(&cfg)
		fmt.Printf("[Go] Config reloaded: ServiceName='%s'\n", cfg.ServiceName)
	}
}

func initTestFiles() {
	if _, err := os.Stat(mmapPath); os.IsNotExist(err) {
		f, _ := os.Create(mmapPath)
		_ = binary.Write(f, binary.LittleEndian, uint32(0x43415444))
		_ = binary.Write(f, binary.LittleEndian, uint32(1))
		_ = binary.Write(f, binary.LittleEndian, uint64(500000))
		_ = binary.Write(f, binary.LittleEndian, uint64(time.Now().Unix()))
		_ = f.Truncate(4096)
		_ = f.Close()
	}
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		data, _ := json.Marshal(DaemonConfig{ServiceName: "Go Systems Daemon", RequestTimeoutMs: 2500})
		_ = os.WriteFile(configPath, data, 0644)
	}
}
```

---

## 3. Rust (2021 Edition) Implementation

### Project Configuration (`Cargo.toml`)

```toml
[package]
name = "systems_daemon"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1.38", features = ["full"] }
memmap2 = "0.9"
arc-swap = "1.7"
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
```

### Source Code (`src/main.rs`)

```rust
use arc_swap::ArcSwap;
use memmap2::Mmap;
use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::signal::unix::{signal, SignalKind};
use tokio::time::sleep;

#[repr(C, packed)]
#[derive(Debug, Copy, Clone)]
pub struct CatalogHeader {
    pub magic: u32,                  // 0x43415444 ("CATD")
    pub version: u32,
    pub record_count: u64,
    pub last_updated_timestamp: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DaemonConfig {
    #[serde(rename = "ServiceName")]
    pub service_name: String,
    #[serde(rename = "RequestTimeoutMs")]
    pub request_timeout_ms: u64,
}

const CONFIG_PATH: &str = "/tmp/daemon_config.json";
const MMAP_PATH: &str = "/dev/shm/daemon_catalog.dat";

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("[Rust] Daemon starting. PID: {}", std::process::id());
    init_test_files()?;

    let initial_config = load_config(CONFIG_PATH)?;
    // ArcSwap provides lock-free RCU semantics across asynchronous tasks
    let config_storage = Arc::new(ArcSwap::from_pointee(initial_config));
    let active_transactions = Arc::new(AtomicUsize::new(0));

    let file = OpenOptions::new().read(true).open(MMAP_PATH)?;
    // SAFETY: mmap is unsafe because external truncation triggers hardware SIGBUS.
    let mmap = Arc::new(unsafe { Mmap::map(&file)? });
    println!("[Rust] Mapped catalog successfully via POSIX mmap.");

    // Signal streams wired into Tokio reactor via UNIX self-pipe trick
    let mut sig_term = signal(SignalKind::terminate())?;
    let mut sig_int = signal(SignalKind::interrupt())?;
    let mut sig_hup = signal(SignalKind::hangup())?;

    // Dedicated SIGHUP configuration reload task
    let cfg_swap_clone = config_storage.clone();
    tokio::spawn(async move {
        while sig_hup.recv().await.is_some() {
            println!("\n[Rust] SIGHUP received. Reloading configuration from disk...");
            if let Ok(new_cfg) = load_config(CONFIG_PATH) {
                println!("[Rust] Config reloaded: ServiceName='{}'", new_cfg.service_name);
                cfg_swap_clone.store(Arc::new(new_cfg)); // Lock-free pointer swap
            }
        }
    });

    let listener = TcpListener::bind("127.0.0.1:8080").await?;
    println!("[Rust] HTTP listener active on http://127.0.0.1:8080/");

    loop {
        tokio::select! {
            _ = sig_term.recv() => { println!("\n[Rust] SIGTERM received. Initiating graceful drain..."); break; }
            _ = sig_int.recv() => { println!("\n[Rust] SIGINT received. Initiating graceful drain..."); break; }
            accept_res = listener.accept() => {
                if let Ok((mut socket, _)) = accept_res {
                    let active = active_transactions.clone();
                    let cfg = config_storage.clone();
                    let mmap_ref = mmap.clone();

                    tokio::spawn(async move {
                        active.fetch_add(1, Ordering::SeqCst);
                        let mut buf = [0u8; 1024];
                        let _ = socket.read(&mut buf).await;
                        sleep(Duration::from_millis(40)).await; // Simulate transactional latency

                        let header = unsafe { &*(mmap_ref.as_ptr() as *const CatalogHeader) };
                        let current_cfg = cfg.load();
                        let body = format!(
                            "{{\"Service\":\"{}\",\"InFlight\":{},\"CatalogMagic\":\"0x{:X}\",\"CatalogRecords\":{}}}\n",
                            current_cfg.service_name, active.load(Ordering::SeqCst), header.magic, header.record_count
                        );

                        let resp = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body);
                        let _ = socket.write_all(resp.as_bytes()).await;
                        let _ = socket.shutdown().await;
                        active.fetch_sub(1, Ordering::SeqCst);
                    });
                }
            }
        }
    }

    // Graceful Drain: Drop listener to immediately reject new TCP SYN packets
    drop(listener);
    println!("[Rust] TCP Listener closed. Waiting for active transactions to drain (Max 15s)...");

    let drain_start = tokio::time::Instant::now();
    let max_drain_duration = Duration::from_secs(15);
    while active_transactions.load(Ordering::SeqCst) > 0 {
        if drain_start.elapsed() > max_drain_duration {
            eprintln!("[Rust] WARNING: Drain timeout reached!");
            break;
        }
        sleep(Duration::from_millis(50)).await;
    }

    println!("[Rust] Drain complete. Remaining: {}. Clean exit.", active_transactions.load(Ordering::SeqCst));
    Ok(())
}

fn load_config(path: &str) -> Result<DaemonConfig, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string(path)?;
    Ok(serde_json::from_str(&content)?)
}

fn init_test_files() -> Result<(), std::io::Error> {
    if !Path::new(MMAP_PATH).exists() {
        let mut file = File::create(MMAP_PATH)?;
        let header = CatalogHeader { magic: 0x43415444, version: 1, record_count: 500_000, last_updated_timestamp: 1720000000 };
        let slice = unsafe { std::slice::from_raw_parts(&header as *const _ as *const u8, std::mem::size_of::<CatalogHeader>()) };
        file.write_all(slice)?;
        file.set_len(4096)?;
    }
    if !Path::new(CONFIG_PATH).exists() {
        let cfg = DaemonConfig { service_name: "Rust Systems Daemon".to_string(), request_timeout_ms: 2500 };
        std::fs::write(CONFIG_PATH, serde_json::to_string_pretty(&cfg).unwrap())?;
    }
    Ok(())
}
```

---

## 4. Build and Execution Verification

Run the following commands in Linux to compile and test each daemon:

```bash
# C# (.NET 8)
dotnet publish -c Release -o ./bin/csharp_daemon
./bin/csharp_daemon/DaemonService

# Go (1.22+)
go build -o ./bin/go_daemon main.go
./bin/go_daemon

# Rust (2021)
cargo build --release
./target/release/systems_daemon
```

### Signal and Graceful Drain Verification
```bash
# Query endpoint under load
curl -s http://127.0.0.1:8080/work

# Send SIGHUP to trigger live config reload without dropped requests
PID=$(pgrep -f "daemon")
kill -SIGHUP $PID

# Modify config on disk and verify hot-reload
sed -i 's/Systems/Reloaded-Live/g' /tmp/daemon_config.json
kill -SIGHUP $PID
curl -s http://127.0.0.1:8080/work

# Trigger graceful shutdown and watch active drain
kill -SIGTERM $PID
```

---

## 5. Critical Observations for C# Developers

### 1. Zero-Copy Pointer Casting vs Managed Accessors
In C#, accessing memory-mapped data typically goes through `MemoryMappedViewAccessor.Read*` methods, which perform boundary checks and buffer copies. To match native systems performance, we had to use `SafeMemoryMappedViewHandle.AcquirePointer` and `Unsafe.Read<T>`, which pins the address in user space and performs a direct dereference. In Go, `unix.Mmap` yields a raw `[]byte` slice; casting `unsafe.Pointer(&mmapData[0])` directly to `*CatalogHeader` creates a typed pointer with zero overhead. In Rust, `memmap2::Mmap` explicitly requires `unsafe` for mapping and pointer casting. Rust forces the developer to acknowledge that if another process truncates `/dev/shm/daemon_catalog.dat`, the MMU will raise a `SIGBUS` fault that safe Rust cannot protect against.

### 2. Signal Routing: Pal Callbacks vs Go Channels vs Tokio Self-Pipes
Notice the three contrasting paradigms for signal integration:
- **C#:** `PosixSignalRegistration` relies on the CoreCLR PAL registering a native POSIX handler. When a signal arrives, the PAL handler queues a work item to the managed `ThreadPool`. This allows your callback to allocate memory and use standard C# libraries safely, but introduces minor thread dispatch latency.
- **Go:** Signals are caught by an assembly trampoline (`sigtrampgo`) running on an alternate stack (`sigaltstack`). The signal number is pushed to a lock-free queue, waking Go's runtime signal goroutine which delivers the signal into a standard buffered channel (`chan os.Signal`).
- **Rust:** Tokio has no background runtime thread pool or garbage collector. It implements the POSIX **Self-Pipe Trick**: the C-level signal handler writes a single byte to a non-blocking UNIX pipe descriptor. Tokio's reactor thread is waiting on that pipe descriptor using `epoll_wait`. The byte wake-up seamlessly resolves the async `sig_term.recv()` future.

### 3. Lock-Free Atomic Configuration Swapping
All three implementations reload `/tmp/daemon_config.json` without acquiring locks on the critical request path:
- C# relies on `volatile DaemonConfig _currentConfig` and assignment. Because reference assignments are atomic on x86/ARM64, reading threads always see either the old or new instance snapshot.
- Go uses `atomic.Pointer[DaemonConfig]`, which wraps atomic load and store operations with compiler-enforced memory fences.
- Rust uses `ArcSwap<DaemonConfig>`. In Rust, you cannot simply swap pointers inside an `Arc` across threads without synchronization due to ownership guarantees. `ArcSwap` provides an RCU (Read-Copy-Update) primitive that allows atomic replacement of the heap-allocated config while in-flight request tasks hold cloned `Arc` snapshots.

### 4. Deterministic Graceful Drain Mechanics
All three services follow the strict two-phase Unix termination protocol:
1. **Unbind Listener:** Dropping or stopping the socket stops accepting new TCP connections (`listener.Stop()`, `srv.Shutdown()`, `drop(listener)`). Any client attempting to initiate a new connection receives an immediate `ECONNREFUSED` or `RST`, allowing upstream load balancers to route traffic to alternative replicas.
2. **In-Flight Drain Window:** Active transactions increment an atomic counter upon arrival and decrement it in a `finally` or `defer` block. The shutdown loop polls this counter against a 15-second deadline. This ensures that long-running transactions are not severed mid-flight, preventing transient 502/504 errors during rolling deployments.
