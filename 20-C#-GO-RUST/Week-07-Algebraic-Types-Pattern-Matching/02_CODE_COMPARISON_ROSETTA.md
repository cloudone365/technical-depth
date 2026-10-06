# Rosetta Stone: Order Lifecycle State Machine

This exercise demonstrates how to build an exhaustive, strictly-enforced state machine in C#, Go, and Rust. We are modeling an Order Lifecycle:
`Created -> PaymentPending -> Paid(Receipt) -> Shipped(Carrier, Tracking) -> Delivered(Signature) | Failed(Reason)`

Notice how Rust enforces data integrity at compile time, while C# and Go require runtime validation and structural compromises.

---

## 1. Rust Implementation (The Gold Standard)

Rust uses Algebraic Data Types (Enums) to ensure that states and their data are physically inseparable.

**`Cargo.toml`**
```toml
[package]
name = "order_state_machine"
version = "0.1.0"
edition = "2021"
```

**`src/main.rs`**
```rust
// 1. The Sum Type Enum
// Notice how each state encapsulates exactly and ONLY the data it needs.
#[derive(Debug)]
pub enum OrderState {
    Created,
    PaymentPending,
    Paid { receipt_id: String },
    Shipped { carrier: String, tracking_id: String },
    Delivered { signature: String },
    Failed { reason: String },
}

// 2. The Order Struct holding the state
pub struct Order {
    pub id: u64,
    pub state: OrderState,
}

impl Order {
    pub fn new(id: u64) -> Self {
        Order { id, state: OrderState::Created }
    }

    // 3. State Transition logic using exhaustive pattern matching
    pub fn process(&mut self) {
        // The compiler FORCES us to handle every variant of OrderState.
        // If we forget `Delivered`, this will not compile.
        match &self.state {
            OrderState::Created => {
                println!("Order {} created. Moving to payment...", self.id);
                self.state = OrderState::PaymentPending;
            }
            OrderState::PaymentPending => {
                // Simulating a successful payment
                self.state = OrderState::Paid { 
                    receipt_id: format!("REC-{}", self.id) 
                };
            }
            OrderState::Paid { receipt_id } => {
                // The receipt_id is destructured and guaranteed to exist.
                println!("Order {} paid with receipt {}. Shipping...", self.id, receipt_id);
                self.state = OrderState::Shipped { 
                    carrier: "FedEx".to_string(), 
                    tracking_id: "TRK-999".to_string() 
                };
            }
            OrderState::Shipped { carrier, tracking_id } => {
                println!("Order {} is on the way via {} ({})", self.id, carrier, tracking_id);
                self.state = OrderState::Delivered { 
                    signature: "John Doe".to_string() 
                };
            }
            OrderState::Delivered { signature } => {
                println!("Order {} complete. Signed by {}.", self.id, signature);
            }
            OrderState::Failed { reason } => {
                println!("Order {} failed permanently: {}", self.id, reason);
            }
        }
    }
}

fn main() {
    let mut order = Order::new(101);
    
    // Drive the state machine to completion
    for _ in 0..5 {
        order.process();
    }
}
```

---

## 2. Go Implementation (The Workaround)

Go lacks Enums and Sum Types. We must use a struct (Product Type) with pointers to simulate optional state data, or use an interface with type switching. Here, we show the interface/type-switch pattern, which is the closest Go has to Sum Types, though it lacks exhaustiveness checking.

**`main.go`**
```go
package main

import (
	"fmt"
)

// 1. Define the "Enum" Interface
// Any struct implementing this empty method satisfies the state.
type OrderState interface {
	isOrderState()
}

// 2. Define the Variants (Structs)
type Created struct{}
func (Created) isOrderState() {}

type PaymentPending struct{}
func (PaymentPending) isOrderState() {}

type Paid struct {
	ReceiptID string
}
func (Paid) isOrderState() {}

type Shipped struct {
	Carrier    string
	TrackingID string
}
func (Shipped) isOrderState() {}

type Delivered struct {
	Signature string
}
func (Delivered) isOrderState() {}

type Failed struct {
	Reason string
}
func (Failed) isOrderState() {}

// 3. The Order Struct
type Order struct {
	ID    uint64
	State OrderState // Holds a fat pointer to one of the above structs
}

// 4. State Transition Logic
func (o *Order) Process() {
	// Type Switch
	// WARNING: The Go compiler does NOT guarantee exhaustiveness here!
	// If you forget 'Delivered', the compiler won't care, leading to runtime bugs.
	switch s := o.State.(type) {
	case Created:
		fmt.Printf("Order %d created. Moving to payment...\n", o.ID)
		o.State = PaymentPending{}
	case PaymentPending:
		o.State = Paid{ReceiptID: fmt.Sprintf("REC-%d", o.ID)}
	case Paid:
		fmt.Printf("Order %d paid with receipt %s. Shipping...\n", o.ID, s.ReceiptID)
		o.State = Shipped{Carrier: "FedEx", TrackingID: "TRK-999"}
	case Shipped:
		fmt.Printf("Order %d is on the way via %s (%s)\n", o.ID, s.Carrier, s.TrackingID)
		o.State = Delivered{Signature: "John Doe"}
	case Delivered:
		fmt.Printf("Order %d complete. Signed by %s.\n", o.ID, s.Signature)
	case Failed:
		fmt.Printf("Order %d failed permanently: %s\n", o.ID, s.Reason)
	default:
		// Required defensive programming because Go lacks exhaustive checks
		panic("Unknown state encountered!") 
	}
}

func main() {
	order := &Order{ID: 101, State: Created{}}

	for i := 0; i < 5; i++ {
		order.Process()
	}
}
```

---

## 3. C# Implementation (Records and Switch Expressions)

Modern C# (C# 9+) uses abstract records to simulate Sum Types and switch expressions to enforce exhaustiveness. This is a vast improvement over legacy C# classes, though it still relies heavily on the CLR heap allocation.

**`Program.cs`**
```csharp
using System;

namespace OrderStateMachine
{
    // 1. The Abstract Base Record (simulating a Sum Type base)
    public abstract record OrderState;

    // 2. The Variants
    public record Created : OrderState;
    public record PaymentPending : OrderState;
    public record Paid(string ReceiptId) : OrderState;
    public record Shipped(string Carrier, string TrackingId) : OrderState;
    public record Delivered(string Signature) : OrderState;
    public record Failed(string Reason) : OrderState;

    // 3. The Order Struct
    public class Order
    {
        public ulong Id { get; }
        public OrderState State { get; private set; }

        public Order(ulong id)
        {
            Id = id;
            State = new Created();
        }

        // 4. State Transition Logic
        public void Process()
        {
            // C# 8+ Switch Expression with Pattern Matching
            // The compiler CAN warn about non-exhaustiveness here!
            State = State switch
            {
                Created => MoveToPayment(Id),
                PaymentPending => new Paid($"REC-{Id}"),
                Paid p => ShipOrder(Id, p.ReceiptId),
                Shipped s => DeliverOrder(Id, s.Carrier, s.TrackingId),
                Delivered d => Complete(Id, d.Signature),
                Failed f => Fail(Id, f.Reason),
                _ => throw new InvalidOperationException("Unknown state") // Still requires a fallback
            };
        }

        private static OrderState MoveToPayment(ulong id)
        {
            Console.WriteLine($"Order {id} created. Moving to payment...");
            return new PaymentPending();
        }

        private static OrderState ShipOrder(ulong id, string receiptId)
        {
            Console.WriteLine($"Order {id} paid with receipt {receiptId}. Shipping...");
            return new Shipped("FedEx", "TRK-999");
        }

        private static OrderState DeliverOrder(ulong id, string carrier, string tracking)
        {
            Console.WriteLine($"Order {id} is on the way via {carrier} ({tracking})");
            return new Delivered("John Doe");
        }

        private static OrderState Complete(ulong id, string signature)
        {
            Console.WriteLine($"Order {id} complete. Signed by {signature}.");
            return new Delivered(signature); // Terminal state loop
        }

        private static OrderState Fail(ulong id, string reason)
        {
            Console.WriteLine($"Order {id} failed: {reason}");
            return new Failed(reason);
        }
    }

    class Program
    {
        static void Main(string[] args)
        {
            var order = new Order(101);

            for (int i = 0; i < 5; i++)
            {
                order.Process();
            }
        }
    }
}
```

---

## Critical Observations for C# Developers

1.  **Memory Representation:** In Rust, `OrderState` is an inline tagged union. The entire struct lives contiguously on the stack without pointers (unless Boxed). In C#, every single state transition (`new Paid(...)`, `new Shipped(...)`) allocates a brand new object on the managed heap, triggering garbage collection pressure.
2.  **Exhaustiveness guarantees:** Rust's `match` will flat-out refuse to compile if a state is missing. C#'s `switch` expression will issue a compiler *warning* (CS8509) if a state is missing, but it will still compile and throw a `SwitchExpressionException` at runtime. Go offers absolutely zero help; you are completely on your own.
3.  **Encapsulation of State Data:** Note how in all three implementations, we successfully avoided the "Nullable Trap." A developer physically cannot access `ReceiptId` unless the current state is explicitly matched as `Paid`. This is the power of type-driven design.
