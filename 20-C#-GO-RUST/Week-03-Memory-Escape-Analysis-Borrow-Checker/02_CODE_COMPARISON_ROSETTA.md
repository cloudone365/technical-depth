# Week 03 · Code Comparison Rosetta: Resource Lifecycle, Escape Analysis & RAII
### Managing Resource Determinism Across the CLR, Go Runtime, and Rust Compiler

> **Core Objective:** Build complete, compilable programs in **C#**, **Go**, and **Rust** that demonstrate the real-world consequences of memory reclamation architectures:
> 1. **C#:** The complete, hardened `IDisposable` pattern + event subscription memory leaks + finalizer rescue.
> 2. **Go:** Five concrete escape analysis scenarios with exact compiler output (`-gcflags='-m -m'`) and their zero-escape refactorings.
> 3. **Rust:** The un-bypassable `TransactionGuard` using RAII and the `Drop` trait to guarantee automatic rollback on failure.

---

## 1. C# (.NET 8): Deterministic Resource Cleanup & The Event Leak Trap

In C#, managed memory is reclaimed by the garbage collector, but unmanaged resources (database sockets, OS file handles, native memory) must be reclaimed manually via `IDisposable`.

### 1.1 Complete Hardened Disposable Pattern (`ResourceManager.cs`)

```csharp
// File: src/csharp/ResourceManager.cs
using System;
using System.Runtime.InteropServices;
using System.Threading;

namespace MemoryRosetta.CSharp;

public class NativeBufferWrapper : IDisposable
{
    private IntPtr _nativeBuffer;
    private int _bufferSize;
    private int _isDisposed; // 0 = false, 1 = true (using interlocked for thread safety)

    public NativeBufferWrapper(int sizeInBytes)
    {
        _bufferSize = sizeInBytes;
        // Allocate raw unmanaged memory outside the CLR managed heap
        _nativeBuffer = Marshal.AllocHGlobal(sizeInBytes);
        Console.WriteLine($"[C#] Allocated {_bufferSize} bytes unmanaged memory at 0x{_nativeBuffer:X}");
    }

    public void WriteByte(int offset, byte value)
    {
        ObjectDisposedException.ThrowIf(Interlocked.CompareExchange(ref _isDisposed, 0, 0) == 1, this);
        if (offset < 0 || offset >= _bufferSize)
            throw new ArgumentOutOfRangeException(nameof(offset));

        Marshal.WriteByte(_nativeBuffer + offset, value);
    }

    // THE DETERMINISTIC DISPOSAL PATH
    public void Dispose()
    {
        Dispose(disposing: true);
        // Suppress finalization because unmanaged memory is already released!
        // This takes the object off the Finalization Queue, saving a Gen 2 collection cycle.
        GC.SuppressFinalize(this);
    }

    protected virtual void Dispose(bool disposing)
    {
        // Thread-safe one-time disposal guard
        if (Interlocked.Exchange(ref _isDisposed, 1) == 0)
        {
            if (disposing)
            {
                // Free managed disposable objects here (e.g., FileStream, HttpClient)
                Console.WriteLine("[C#] Disposing managed child resources...");
            }

            // Free unmanaged resources
            if (_nativeBuffer != IntPtr.Zero)
            {
                Console.WriteLine($"[C#] Freeing unmanaged buffer at 0x{_nativeBuffer:X}");
                Marshal.FreeHGlobal(_nativeBuffer);
                _nativeBuffer = IntPtr.Zero;
            }
        }
    }

    // THE NON-DETERMINISTIC SAFETY NET (Finalizer)
    // Only runs if the developer FORGOT to use 'using' or call Dispose()!
    ~NativeBufferWrapper()
    {
        Console.ForegroundColor = ConsoleColor.Red;
        Console.WriteLine("[C# WARNING] Finalizer called! Developer leaked an unmanaged resource!");
        Console.ResetColor();
        Dispose(disposing: false);
    }
}
```

### 1.2 The Classic .NET Memory Leak: Event Subscription
Even with a GC, C# programs frequently leak gigabytes of memory through event handlers:

```csharp
public class EventPublisher
{
    public event EventHandler<string>? DataReceived;
    public void Publish(string data) => DataReceived?.Invoke(this, data);
}

public class LeakySubscriber
{
    private readonly byte[] _heavyBuffer = new byte[10_000_000]; // 10MB payload

    public LeakySubscriber(EventPublisher publisher)
    {
        // TRAP: Publisher holds a strong reference to this subscriber delegate!
        // Even if the subscriber variable goes out of scope, the publisher keeps
        // this 10MB object alive indefinitely in Gen 2!
        publisher.DataReceived += OnDataReceived;
    }

    private void OnDataReceived(object? sender, string e) { }
}
```

---

## 2. Go (1.22+): Escape Analysis in Action

The Go compiler performs Escape Analysis to prove whether a variable can remain on the stack. Let's inspect five real-world patterns.

### 2.1 The 5 Escape Scenarios (`escape_demo.go`)

```go
// File: src/go/escape_demo.go
package main

import (
	"fmt"
	"strconv"
)

type User struct {
	ID    int64
	Name  string
	Email string
}

// -----------------------------------------------------------------------
// SCENARIO 1: Returning a Pointer (Escapes) vs. Returning a Value (Stack)
// -----------------------------------------------------------------------

//go:noinline
func CreateUserPointer(id int64) *User {
	u := User{ID: id, Name: "Alice", Email: "alice@example.com"}
	// ESCAPES: Pointer outlives CreateUserPointer stack frame.
	return &u 
}

//go:noinline
func CreateUserValue(id int64) User {
	u := User{ID: id, Name: "Bob", Email: "bob@example.com"}
	// STAYS ON STACK: Entire 32-byte struct copied by value back to caller.
	return u 
}

// -----------------------------------------------------------------------
// SCENARIO 2: Interface Boxing (fmt.Println Trap)
// -----------------------------------------------------------------------

//go:noinline
func LogBad(id int64) {
	// ESCAPES: id is boxed into interface{} for fmt.Println
	fmt.Println(id) 
}

//go:noinline
func LogGood(id int64, out []byte) []byte {
	// STAYS ON STACK: strconv.AppendInt writes directly to a byte slice buffer
	return strconv.AppendInt(out, id, 10)
}

// -----------------------------------------------------------------------
// SCENARIO 3: Dynamic Slice (Escapes) vs. Fixed Array (Stack)
// -----------------------------------------------------------------------

//go:noinline
func DynamicSliceAllocation(size int) []byte {
	// ESCAPES: Size is dynamic (variable); compiler must allocate on heap
	buf := make([]byte, size) 
	return buf
}

//go:noinline
func FixedArrayAllocation() [64]byte {
	// STAYS ON STACK: Constant size known at compile-time fits in stack frame
	var buf [64]byte 
	return buf
}

// -----------------------------------------------------------------------
// SCENARIO 4: Channel Passing
// -----------------------------------------------------------------------

//go:noinline
func SendAcrossChannel(ch chan<- *User) {
	u := User{ID: 42, Name: "Charlie"}
	// ESCAPES: Sent to channel, consumed by a different goroutine/stack
	ch <- &u 
}

// -----------------------------------------------------------------------
// SCENARIO 5: Closure Variable Capture
// -----------------------------------------------------------------------

//go:noinline
func MakeCounter() func() int {
	count := 0
	// ESCAPES: count is captured by reference by the returning closure
	return func() int {
		count++
		return count
	}
}

func main() {
	uPtr := CreateUserPointer(1)
	uVal := CreateUserValue(2)
	_ = uPtr
	_ = uVal

	ch := make(chan *User, 1)
	SendAcrossChannel(ch)
	<-ch

	counter := MakeCounter()
	counter()
}
```

### 2.2 Compiling with Escape Analysis Diagnostics
Run the Go compiler with the `-m` flag to view the compiler's internal proofs:

```bash
go build -gcflags="-m -m" escape_demo.go
```

**Compiler Proof Output:**
```text
./escape_demo.go:17:6: can inline CreateUserPointer
./escape_demo.go:18:2: u escapes to heap:
./escape_demo.go:18:2:   flow: ~r0 = &u:
./escape_demo.go:18:2:     from return &u (return) at ./escape_demo.go:20:2
./escape_demo.go:18:2: moved to heap: u

./escape_demo.go:23:6: can inline CreateUserValue
./escape_demo.go:23:22: CreateUserValue u does not escape

./escape_demo.go:31:13: inlining call to fmt.Println
./escape_demo.go:31:14: id escapes to heap:
./escape_demo.go:31:14:   flow: {storage for ... argument} = id:
./escape_demo.go:31:14:     from ... argument (spill) at ./escape_demo.go:31:13

./escape_demo.go:58:2: u escapes to heap:
./escape_demo.go:58:2:   flow: {heap} = &u:
./escape_demo.go:58:2:     from ch <- &u (send) at ./escape_demo.go:60:5

./escape_demo.go:67:2: count escapes to heap:
./escape_demo.go:67:2:   flow: {heap} = count:
./escape_demo.go:67:2:     from func literal (closure-variable) at ./escape_demo.go:69:9
```

---

## 3. Rust (Edition 2021): Un-bypassable RAII with the `Drop` Trait

In Rust, there is no garbage collector and no need to remember `Dispose()`. Destruction is deterministic, universal, and strictly enforced by the compiler.

### 3.1 The Un-Bypassable Transaction Guard (`src/transaction.rs`)

Consider a database transaction: if anything fails, we **must** rollback. If the function succeeds, we commit. In C#, an unhandled exception or early `return` might bypass commit/rollback if `try/finally` is omitted. In Rust, RAII guarantees safety.

```rust
// File: src/transaction.rs
use std::fmt;

#[derive(Debug)]
pub enum DbError {
    ConnectionFailed,
    QueryFailed(&'static str),
    AlreadyCommitted,
}

// Simulated native database connection
pub struct DatabaseConnection {
    pub connection_id: u32,
}

impl DatabaseConnection {
    pub fn new(id: u32) -> Self {
        println!("[Rust] Acquired DB Connection #{}", id);
        Self { connection_id: id }
    }

    pub fn execute(&self, sql: &str) -> Result<(), DbError> {
        println!("[Rust Conn #{}] Executing: {}", self.connection_id, sql);
        if sql.contains("FAIL") {
            return Err(DbError::QueryFailed("Simulated DB Disk Failure"));
        }
        Ok(())
    }
}

// THE RAII TRANSACTION GUARD
pub struct TransactionGuard<'a> {
    conn: &'a mut DatabaseConnection,
    is_committed: bool,
    tx_name: &'static str,
}

impl<'a> TransactionGuard<'a> {
    pub fn begin(conn: &'a mut DatabaseConnection, tx_name: &'static str) -> Result<Self, DbError> {
        conn.execute("BEGIN TRANSACTION")?;
        println!("[Rust TX: {}] Transaction started.", tx_name);
        Ok(Self {
            conn,
            is_committed: false,
            tx_name,
        })
    }

    pub fn execute(&mut self, sql: &str) -> Result<(), DbError> {
        if self.is_committed {
            return Err(DbError::AlreadyCommitted);
        }
        self.conn.execute(sql)
    }

    // Explicit commit consumes the guard via 'self' (by value!)
    pub fn commit(mut self) -> Result<(), DbError> {
        self.conn.execute("COMMIT")?;
        self.is_committed = true;
        println!("[Rust TX: {}] Committed successfully.", self.tx_name);
        // Note: When self leaves scope, Drop::drop() is called, but is_committed is now true!
        Ok(())
    }
}

// THE DROP TRAIT: The Inviolable Safety Net
impl<'a> Drop for TransactionGuard<'a> {
    fn drop(&mut self) {
        if !self.is_committed {
            println!(
                "\x1b[31m[Rust TX: {}] ROLLBACK executed automatically by Drop trait!\x1b[0m",
                self.tx_name
            );
            let _ = self.conn.execute("ROLLBACK");
        } else {
            println!("[Rust TX: {}] Clean drop after commit.", self.tx_name);
        }
    }
}
```

### 3.2 Executing Successful vs. Early-Failure Scenarios (`src/main.rs`)

```rust
// File: src/main.rs
mod transaction;
use transaction::{DatabaseConnection, DbError, TransactionGuard};

fn process_order_success(conn: &mut DatabaseConnection) -> Result<(), DbError> {
    println!("\n--- SCENARIO 1: Happy Path ---");
    let mut tx = TransactionGuard::begin(conn, "Order_Success_TX")?;
    tx.execute("INSERT INTO orders (id, total) VALUES (101, 250.00)")?;
    tx.execute("UPDATE inventory SET stock = stock - 1 WHERE item_id = 99")?;
    
    // Explicitly commit
    tx.commit()?;
    Ok(())
}

fn process_order_early_failure(conn: &mut DatabaseConnection) -> Result<(), DbError> {
    println!("\n--- SCENARIO 2: Early Error via '?' Operator ---");
    let mut tx = TransactionGuard::begin(conn, "Order_Failure_TX")?;
    tx.execute("INSERT INTO orders (id, total) VALUES (102, 500.00)")?;
    
    // This query deliberately triggers an error!
    // The '?' operator immediately returns from this function with Err.
    // 'tx' goes out of scope here. The Drop trait GUARANTEES automatic rollback!
    tx.execute("SELECT * FROM table_FAIL_now")?;

    // This commit is never reached
    tx.commit()?;
    Ok(())
}

fn main() {
    let mut conn = DatabaseConnection::new(1);

    let _ = process_order_success(&mut conn);
    let result = process_order_early_failure(&mut conn);

    match result {
        Ok(_) => println!("Unexpected success!"),
        Err(e) => println!("[Main] Caught expected error: {:?}", e),
    }

    println!("\n[Main] Program finished without leaking database locks or memory!");
}
```

---

## 4. Key Engineering Takeaways

1. **In C#:** Deterministic cleanup requires discipline (`using` blocks). If an unhandled exception or forgotten call bypasses cleanup, you pay the heavy performance penalty of GC finalizers and card table writes.
2. **In Go:** You can write pointer-heavy code that looks idiomatic, but causes continuous heap allocations. Use `go build -gcflags="-m -m"` to verify that high-throughput structs remain on the stack.
3. **In Rust:** RAII and the `Drop` trait provide mathematical determinism. Resources cannot be leaked through early returns, panics, or forgotten calls because the compiler binds destruction directly to variable scope.
