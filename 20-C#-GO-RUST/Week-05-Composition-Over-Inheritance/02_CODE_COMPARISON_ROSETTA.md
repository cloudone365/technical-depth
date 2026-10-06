# Week 05 Code Comparison: E-Commerce Domain Model

In this exercise, we will model an E-Commerce system dealing with Orders, Discounts, and Shipping. 

We will start with a classic C# object-oriented inheritance tree, observe how brittle it becomes when new requirements arrive, and then refactor it using Go's Struct Embedding and Rust's Trait Composition.

---

## 1. C# Implementation: The Brittle Inheritance Hierarchy

In traditional C#, we often try to solve varying behavior by subclassing. 

**Scenario:** We have a Base Order. Then we need Discounted Orders. Then we need International Orders. Then we need International Discounted Orders...

**Project Setup (`Ecommerce.csproj`)**
```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
</Project>
```

**Code (`Program.cs`)**
```csharp
using System;

// 1. The Base Class
public abstract class Order
{
    public decimal BasePrice { get; set; }
    
    public Order(decimal basePrice)
    {
        BasePrice = basePrice;
    }

    // Virtual dispatch allows subclasses to change behavior
    public virtual decimal CalculateTotal()
    {
        return BasePrice;
    }

    public virtual string GetShippingLabel()
    {
        return "Standard Domestic Shipping";
    }
}

// 2. The Derived Classes (Combinatorial Explosion begins)
public class DiscountedOrder : Order
{
    public decimal DiscountAmount { get; set; }

    public DiscountedOrder(decimal basePrice, decimal discount) : base(basePrice)
    {
        DiscountAmount = discount;
    }

    public override decimal CalculateTotal()
    {
        // Tight coupling to base class state
        return base.CalculateTotal() - DiscountAmount;
    }
}

public class InternationalOrder : Order
{
    public decimal ImportTax { get; set; }

    public InternationalOrder(decimal basePrice, decimal tax) : base(basePrice)
    {
        ImportTax = tax;
    }

    public override decimal CalculateTotal()
    {
        return base.CalculateTotal() + ImportTax;
    }

    public override string GetShippingLabel()
    {
        return "Customs Required - International Shipping";
    }
}

// THE PROBLEM: What if we need an International Discounted Order?
// C# does not support multiple inheritance. 
// We are forced into deep nesting or duplicating logic.
public class InternationalDiscountedOrder : InternationalOrder
{
    public decimal DiscountAmount { get; set; }

    public InternationalDiscountedOrder(decimal basePrice, decimal tax, decimal discount) 
        : base(basePrice, tax)
    {
        DiscountAmount = discount;
    }

    public override decimal CalculateTotal()
    {
        // We have to remember to subtract the discount from the inherited tax calculation
        return base.CalculateTotal() - DiscountAmount; 
    }
}

public class Program
{
    public static void Main()
    {
        var order = new InternationalDiscountedOrder(100m, 20m, 10m);
        Console.WriteLine($"Total: ${order.CalculateTotal()}"); // 110
        Console.WriteLine($"Label: {order.GetShippingLabel()}");
    }
}
```

**Run Command:** `dotnet run`

---

## 2. Go Implementation: Struct Embedding and Interfaces

Go solves the combinatorial explosion by using independent interfaces and composing data structures. We separate the concept of "Data" from "Calculation Policies".

**Project Setup:**
```bash
go mod init ecommerce
```

**Code (`main.go`)**
```go
package main

import "fmt"

// 1. Define distinct behaviors using Interfaces
type PricingPolicy interface {
	CalculateTotal(basePrice float64) float64
}

type ShippingPolicy interface {
	GetLabel() string
}

// 2. Implement independent calculation structs
type FlatDiscount struct {
	Amount float64
}

func (d FlatDiscount) CalculateTotal(base float64) float64 {
	return base - d.Amount
}

type InternationalTax struct {
	TaxRate float64
}

func (t InternationalTax) CalculateTotal(base float64) float64 {
	return base + (base * t.TaxRate)
}

// 3. Compose the Order struct
type Order struct {
	BasePrice      float64
	// Interfaces allow us to swap policies at runtime without inheritance!
	PricingPolicy  PricingPolicy 
	ShippingPolicy ShippingPolicy
}

func (o Order) GetFinalTotal() float64 {
	if o.PricingPolicy != nil {
		return o.PricingPolicy.CalculateTotal(o.BasePrice)
	}
	return o.BasePrice
}

func main() {
	// We construct our order by COMPOSING policies, not instantiating a subclass.
	discount := FlatDiscount{Amount: 10.0}
	
	order := Order{
		BasePrice:     100.0,
		PricingPolicy: discount,
	}

	fmt.Printf("Total: $%.2f\n", order.GetFinalTotal()) // 90.00
}
```

**Run Command:** `go run main.go`

---

## 3. Rust Implementation: Traits and Newtypes

Rust takes composition further. We will use the Newtype pattern to enforce type safety (preventing mixing up taxes and discounts) and Traits for behaviors.

**Project Setup (`Cargo.toml`)**
```toml
[package]
name = "rust_ecommerce"
version = "0.1.0"
edition = "2021"
```

**Code (`src/main.rs`)**
```rust
// 1. Newtypes for absolute Type Safety (Zero-cost abstraction)
// In C#, decimal is passed around everywhere. 
// Here, a USD is distinct from a TaxRate.
#[derive(Debug, Clone, Copy)]
struct Usd(f64);

#[derive(Debug, Clone, Copy)]
struct TaxRate(f64);

// 2. Traits define behavior
trait PricingPolicy {
    fn apply(&self, base_price: Usd) -> Usd;
}

// 3. Independent Implementations
struct FlatDiscount {
    amount: Usd,
}

impl PricingPolicy for FlatDiscount {
    fn apply(&self, base_price: Usd) -> Usd {
        // We unpack the newtype to do math, then repack it.
        Usd(base_price.0 - self.amount.0)
    }
}

struct InternationalTax {
    rate: TaxRate,
}

impl PricingPolicy for InternationalTax {
    fn apply(&self, base_price: Usd) -> Usd {
        Usd(base_price.0 + (base_price.0 * self.rate.0))
    }
}

// 4. Combined Policy using Composition (No inheritance)
// We can compose behaviors dynamically.
struct CombinedPricing {
    policies: Vec<Box<dyn PricingPolicy>>,
}

impl PricingPolicy for CombinedPricing {
    fn apply(&self, base_price: Usd) -> Usd {
        let mut total = base_price;
        for policy in &self.policies {
            total = policy.apply(total);
        }
        total
    }
}

// 5. The Order contains state and a reference to behavior
struct Order {
    base_price: Usd,
    // Box<dyn Trait> is Rust's version of an interface reference (Dynamic Dispatch via Vtable)
    pricing_policy: Box<dyn PricingPolicy>,
}

impl Order {
    fn get_total(&self) -> Usd {
        self.pricing_policy.apply(self.base_price)
    }
}

fn main() {
    let base = Usd(100.0);
    
    let discount = Box::new(FlatDiscount { amount: Usd(10.0) });
    let tax = Box::new(InternationalTax { rate: TaxRate(0.20) });
    
    let combo = Box::new(CombinedPricing {
        policies: vec![discount, tax],
    });

    let order = Order {
        base_price: base,
        pricing_policy: combo,
    };

    println!("Total: ${:.2}", order.get_total().0); // (100 - 10) * 1.20 = 108.0
}
```

**Run Command:** `cargo run`

### Critical Observations for C# Developers

1.  **Combinatorial Explosion is Eliminated:** In C#, supporting (Discount + Tax) required a new class `InternationalDiscountedOrder`. In Go and Rust, we simply injected an array/slice of policies. 
2.  **No `base` Keyword:** Notice that in Go and Rust, there is no call to `base.CalculateTotal()`. The implementations do not rely on hidden state inherited from a parent. They only rely on explicit arguments passed to them.
3.  **Rust Newtypes:** `Usd` and `TaxRate` prevent a developer from accidentally passing a percentage where a currency amount was expected. C# could do this with `readonly struct`, but it carries minor serialization/boxing friction that Rust eliminates at compile-time.
4.  **`Box<dyn Trait>` vs Go Interfaces:** In Go, any struct matching the signature implicitly satisfies the interface. In Rust, we explicitly `impl PricingPolicy for FlatDiscount`. Furthermore, because `Order` doesn't know the exact size of the policy struct at compile time, we must wrap it in a `Box` to place it on the heap and store a pointer to its vtable (`dyn Trait`).
