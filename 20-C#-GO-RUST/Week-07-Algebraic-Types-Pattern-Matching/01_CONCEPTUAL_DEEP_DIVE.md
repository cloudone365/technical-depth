# Week 07 · Conceptual Deep Dive: Algebraic Data Types & Exhaustive Pattern Matching
### Type Theory, Memory Layouts, Niche Value Optimization & Making Invalid States Unrepresentable

> **Target Audience:** Senior .NET Engineers transitioning to Go and Rust.  
> **Core Objective:** Master the mathematical foundation and physical memory reality of Algebraic Data Types (ADTs). Deconstruct the flaw in C# nullable-field entity modeling that permits impossible states, contrast Go's rudimentary `iota` enums and runtime type switches, and deeply inspect Rust's tagged union memory representation, discriminant alignment, and the **Niche Value Optimization (NVO)** that renders `Option<&T>` zero-overhead.

---

## 1. Why This Week Matters for Your Career Transition

In enterprise C# backend engineering, domain models are almost universally constructed using classes with nullable properties:
```csharp
public class Order
{
    public OrderStatus Status { get; set; }
    public string? TrackingNumber { get; set; }   // Populated ONLY when Shipped
    public string? FailureReason { get; set; }    // Populated ONLY when Failed
    public string? PaymentReceipt { get; set; }   // Populated ONLY when Paid
}
```
Every senior engineer knows what happens next:
* Can an order be `Status = OrderStatus.Shipped` with `TrackingNumber = null`? **Yes! The compiler happily allows it.**
* Can an order be `Status = OrderStatus.Delivered` while simultaneously carrying a `FailureReason = "Insufficient Funds"`? **Yes!**
* To protect against these corrupt combinations, your codebase becomes littered with defensive `if (order.TrackingNumber == null)` runtime checks, unit tests checking impossible permutations, and bug reports when an edge case slips through.

When you transition to **Go** and **Rust**, you encounter two polar opposite philosophies:
* **Go** intentionally rejected Algebraic Data Types. It provides `iota` integer constants and type switches over empty interfaces (`any`), forcing developers to rely on runtime convention and manual defensive programming.
* **Rust** embraces **Algebraic Data Types (ADTs)** and **Exhaustive Pattern Matching** as foundational pillars of system design.

In Rust, you do not write defensive code to check for invalid states; you **make invalid states unrepresentable in the type system**. The compiler mathematically guarantees that every possible state transition is handled, that required data is present when and only when a state is active, and that forgotten cases result in a compile-time failure.

---

## 2. Type Theory: Product Types vs. Sum Types

In computer science type theory, types are categorized by the **cardinality** (the number of possible values) of their state space.

### 2.1 Product Types (Structs, Classes, Tuples)
A **Product Type** represents an **AND** relationship. A struct composed of type $A$ and type $B$ contains an instance of $A$ **AND** an instance of $B$.

$$\text{Cardinality}(A \times B) = |A| \times |B|$$

```csharp
// Cardinality: 256 (byte) * 2 (bool) = 512 possible states
public struct SensorReading
{
    public byte SensorId; // 256 possible values (0..255)
    public bool IsActive; // 2 possible values (true, false)
}
```
Product types are essential for bundling related data together.

### 2.2 Sum Types (Tagged Unions, Discriminated Unions, Enums with Data)
A **Sum Type** represents an **OR** relationship. A sum type composed of variant $A$ and variant $B$ contains an instance of $A$ **OR** an instance of $B$—**never both at the same time**.

$$\text{Cardinality}(A + B) = |A| + |B|$$

```rust
// Cardinality: 256 + 2 = 258 possible states!
pub enum DeviceCommand {
    Calibrate(u8), // 256 possible values
    Power(bool),   // 2 possible values
}
```

#### Why Sum Types Eliminate Invalid States:
In a C# class with 4 nullable properties, the state space is a massive product type ($S_1 \times S_2 \times S_3 \times S_4$), creating millions of invalid combinations. A Rust sum type collapses this state space to the exact sum of valid business possibilities ($S_1 + S_2 + S_3 + S_4$).

---

## 3. Rust Enum Memory Layout & The Niche Value Optimization

How does the hardware actually represent a Rust `enum` in silicon? It uses a **Tagged Union**.

### 3.1 The Standard Tagged Union Layout
Consider this enum:
```rust
pub enum NetworkEvent {
    Connected,                          // Variant 0: No payload
    Data(Vec<u8>),                      // Variant 1: Vec (24 bytes)
    Error { code: u32, message: String },// Variant 2: u32 (4B) + String (24B) = 28 bytes
}
```

How is `NetworkEvent` laid out in memory?
1. **The Discriminant (Tag):** A hidden integer (usually 1 byte: `u8`) that records which variant is currently active (`0`, `1`, or `2`).
2. **The Payload Union:** A shared memory block sized to fit the **largest variant** (here, Variant 2 is $24 + 4 = 28$ bytes, padded to 32 bytes to satisfy 8-byte pointer alignment).
3. **Struct Padding:** The total struct size must be a multiple of the largest field's alignment (8 bytes).

```
Physical Memory Layout of NetworkEvent (32 Bytes Total):
┌──────────┬──────────────────────┬──────────────────────────────────────────┐
│ Tag: u8  │ 7 Bytes Dead Padding │ Payload Union: 24 Bytes                  │
│ (Byte 0) │ (Bytes 1..7)         │ (Sized to largest variant: Vec / String) │
└──────────┴──────────────────────┴──────────────────────────────────────────┘
```

---

### 3.2 The Niche Value Optimization (NVO): Zero-Overhead `Option<&T>`
In C#, reference types are nullable by default: a reference can hold either a valid 8-byte pointer or `null` (`0x0000_0000_0000_0000`).

In Rust, references (`&T`) **can never be null**. Every valid reference points to an actual initialized object. To represent an optional value, Rust provides `Option<T>`:
```rust
pub enum Option<T> {
    None,
    Some(T),
}
```

Normally, a tagged union requires $1 \text{ byte (tag)} + \text{padding} + \text{payload}$. For an 8-byte reference `&T`, naive layout would require:
$$1 \text{ byte (tag)} + 7 \text{ bytes (padding)} + 8 \text{ bytes (pointer)} = \mathbf{16\text{ bytes}}.$$

**The Rust Compiler's Genius Optimization:**
Because `rustc` knows that a valid reference `&T` can **never have an address of zero** (`0x0`), the bit pattern `0x0000_0000_0000_0000` is a **"niche" (an unused, invalid bit pattern)**!

The compiler uses this niche to represent `None`:
* If the 8 bytes are `0x0000_0000_0000_0000` $\longrightarrow$ It is `Option::None`.
* If the 8 bytes are any non-zero memory address $\longrightarrow$ It is `Option::Some(&T)`.

```
Memory Footprint:
┌─────────────────────────────────────────────────────────────┐
│  Raw Reference `&T`:         8 Bytes                        │
├─────────────────────────────────────────────────────────────┤
│  `Option<&T>`:               8 Bytes  (ZERO MEMORY OVERHEAD)│
├─────────────────────────────────────────────────────────────┤
│  `Option<Box<T>>`:           8 Bytes  (ZERO MEMORY OVERHEAD)│
├─────────────────────────────────────────────────────────────┤
│  `Option<NonZeroU64>`:       8 Bytes  (ZERO MEMORY OVERHEAD)│
└─────────────────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> In Rust, wrapping a pointer or non-zero integer in `Option<T>` adds **zero bytes of memory overhead** and **zero CPU instruction penalty**. You get total null safety at the exact same hardware cost as a raw C pointer.

---

## 4. Go: The Omission of Sum Types & Its Consequences

Go deliberately chose not to include Algebraic Data Types. How do Go developers model states, and what are the trade-offs?

### 4.1 The `iota` Enumeration Trap
In Go, enums are represented using untyped integer constants generated via `iota`:

```go
type OrderStatus int

const (
    StatusCreated OrderStatus = iota // 0
    StatusPaid                       // 1
    StatusShipped                    // 2
    StatusFailed                     // 3
)
```

#### Why `iota` Fails Enterprise Domain Modeling:
1. **Zero Type Safety:** `OrderStatus` is just an alias for `int`. Anyone can pass `OrderStatus(9999)` without a compiler warning.
2. **Zero Associated Data:** `StatusShipped` cannot carry tracking numbers or carrier names. You must add nullable fields to the parent struct.
3. **No Exhaustiveness Check:** In a `switch` statement on `OrderStatus`, if you omit `StatusFailed`, the Go compiler compiles silently without warning. If an order fails, execution falls through to the `default` block (or does nothing), creating silent production bugs.

### 4.2 The Interface Workaround
To simulate sum types, Go developers often create an interface with an unexported marker method:

```go
type OrderState interface {
    isOrderState() // Sealed interface marker
}

type StatePaid struct {
    TransactionID string
    Amount        float64
}
func (StatePaid) isOrderState() {}

type StateShipped struct {
    TrackingNumber string
}
func (StateShipped) isOrderState() {}
```

#### The Cost:
1. Handling states requires **runtime type switches** (`switch s := state.(type)`).
2. It incurs **heap allocations** because concrete structs must be boxed into interface fat pointers.
3. The compiler still **cannot guarantee exhaustiveness**—if a new state is added, old type switches compile without error.

---

## 5. Architectural Comparison Matrix

| Capability | C# (.NET 8+) | Go (1.22+) | Rust (Edition 2021) |
| :--- | :--- | :--- | :--- |
| **Sum Types** | Enums (int only) / `OneOf` library | Enums (`iota` ints only) | Native Enums with payloads (True ADTs) |
| **Associated Data** | Requires nullable fields on class | Requires nullable fields or interfaces | Variants carry distinct payload types |
| **Compile Exhaustiveness** | Warning on switch (opt-in) | **NO warning** (runtime default) | **Strict compile-time error** |
| **Memory Layout** | Managed heap object + fields | Struct + fields | Tagged union with niche optimization |
| **Null Representation** | `null` reference pointer | `nil` pointer / interface | `Option<T>` with zero-cost NVO |
| **Pattern Matching** | Switch expressions (`{ }`) | `switch` statement / type switch | Deep destructuring with guards (`match`) |

By mastering Algebraic Data Types in Rust, you transition from writing hundreds of lines of defensive runtime null-checks to constructing mathematical domain models where the compiler enforces correctness before your code ever runs.
