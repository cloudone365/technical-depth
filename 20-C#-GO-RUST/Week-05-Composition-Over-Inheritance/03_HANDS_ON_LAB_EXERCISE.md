# Week 05 · Hands-On Lab Exercise: Deconstructing the 4-Level Inheritance Hierarchy
### Refactoring Legacy Enterprise Payroll into Composable Systems in Go and Rust

> **Lab Objective:** Take a legacy C# enterprise payroll system suffering from a 4-level deep inheritance tree (`Employee` -> `SalariedEmployee` -> `InternationalSalariedEmployee` -> `ExecutiveInternationalEmployee`) that violates the Single Responsibility Principle and creates combinatorial explosion.
> 
> You will systematically dismantle this hierarchy:
> 1. **Go Refactoring:** Disassemble the inheritance tree into composable domain components using struct embedding, pure value structs, and small structural interfaces.
> 2. **Rust Refactoring:** Re-architect the system using traits, the Strategy Pattern, and the Newtype Pattern with zero-cost generic dispatch.
> 3. **Friday Mob Review:** Defend why composition satisfies the Open-Closed Principle and eliminates fragile base class regressions.

---

## Lab Architecture & Timetable

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LAB WORKFLOW & TIMETABLE                        │
├────────────────────────────────────────────────────────────────────────┤
│ • Day 1-2: Analyze Legacy C# & Implement Go Composition Architecture   │
│            Decompose Employee hierarchy into decoupled value structs:  │
│            BaseCompensation, BonusPolicy, CurrencyAdjuster, and Tax.   │
│                                                                        │
│ • Day 3-4: Implement Rust Trait Strategy Pattern                       │
│            Define Payable, TaxStrategy, and CurrencyProvider traits.   │
│            Apply Newtype pattern for Money and ExchangeRate.           │
│                                                                        │
│ • Day 5:   Friday Mob Review & Extensibility Challenge                 │
│            Add a new "CryptoEquityGrant" bonus policy without altering │
│            a single line of existing employee code.                    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: The Legacy C# System (`LegacyPayroll.cs`)

Analyze this classic OOP anti-pattern. Notice how state is scattered across base classes, derived constructors require deep `base(...)` chaining, and methods depend on `base.CalculatePay()`:

```csharp
// File: labs/week05/csharp/LegacyPayroll.cs
using System;

namespace LegacyPayroll;

// Level 1: Base
public abstract class Employee
{
    public string Id { get; set; }
    public string Name { get; set; }
    public decimal BaseSalary { get; set; }

    protected Employee(string id, string name, decimal baseSalary)
    {
        Id = id;
        Name = name;
        BaseSalary = baseSalary;
    }

    public virtual decimal CalculatePay()
    {
        return BaseSalary;
    }
}

// Level 2: Salaried with Bonus
public class SalariedEmployee : Employee
{
    public decimal PerformanceBonus { get; set; }

    public SalariedEmployee(string id, string name, decimal baseSalary, decimal bonus)
        : base(id, name, baseSalary)
    {
        PerformanceBonus = bonus;
    }

    public override decimal CalculatePay()
    {
        return base.CalculatePay() + PerformanceBonus;
    }
}

// Level 3: International Employee with Currency Conversion
public class InternationalSalariedEmployee : SalariedEmployee
{
    public decimal ExchangeRate { get; set; }
    public string CurrencyCode { get; set; }

    public InternationalSalariedEmployee(
        string id, string name, decimal baseSalary, decimal bonus, decimal exchangeRate, string currency)
        : base(id, name, baseSalary, bonus)
    {
        ExchangeRate = exchangeRate;
        CurrencyCode = currency;
    }

    public override decimal CalculatePay()
    {
        // Bug risk: base.CalculatePay() already included bonus in USD!
        return base.CalculatePay() * ExchangeRate;
    }
}

// Level 4: Executive with Stock Options and Local Withholding Tax
public class ExecutiveInternationalEmployee : InternationalSalariedEmployee
{
    public decimal StockOptionBonus { get; set; }
    public decimal LocalWithholdingTaxRate { get; set; }

    public ExecutiveInternationalEmployee(
        string id, string name, decimal baseSalary, decimal bonus, decimal exchangeRate, string currency,
        decimal stockBonus, decimal taxRate)
        : base(id, name, baseSalary, bonus, exchangeRate, currency)
    {
        StockOptionBonus = stockBonus;
        LocalWithholdingTaxRate = taxRate;
    }

    public override decimal CalculatePay()
    {
        decimal gross = base.CalculatePay() + StockOptionBonus;
        return gross * (1.0m - LocalWithholdingTaxRate);
    }
}
```

#### Why this design is broken:
1. **Combinatorial Explosion:** If we need an `ExecutiveDomesticEmployee`, we must create another class or duplicate logic.
2. **Coupled Execution Order:** If `SalariedEmployee` changes how `base.CalculatePay()` calculates bonuses, `ExecutiveInternationalEmployee` silently produces corrupted financial numbers.

---

## Part 2: Days 1–2 — The Go Composition Solution (`payroll.go`)

In Go, we discard the concept of "kinds of employees." An `Employee` is simply a domain entity that holds references to decoupled, composable strategies.

Save this in `labs/week05/go/payroll.go`:

```go
package main

import "fmt"

// Decoupled Strategy Interfaces
type BonusPolicy interface {
	CalculateBonus(base float64) float64
}

type CurrencyConverter interface {
	Convert(amount float64) (float64, string)
}

type TaxStrategy interface {
	ApplyTax(gross float64) float64
}

// ------------------------------------------------------------------------
// CONCRETE STRATEGIES
// ------------------------------------------------------------------------

type FixedBonus struct {
	Amount float64
}

func (f FixedBonus) CalculateBonus(base float64) float64 {
	return f.Amount
}

type PercentageBonus struct {
	Percentage float64
}

func (p PercentageBonus) CalculateBonus(base float64) float64 {
	return base * p.Percentage
}

type NoBonus struct{}

func (NoBonus) CalculateBonus(base float64) float64 { return 0 }

type ForeignCurrency struct {
	Code         string
	ExchangeRate float64
}

func (fc ForeignCurrency) Convert(amount float64) (float64, string) {
	return amount * fc.ExchangeRate, fc.Code
}

type DomesticCurrency struct{}

func (DomesticCurrency) Convert(amount float64) (float64, string) {
	return amount, "USD"
}

type FlatTax struct {
	Rate float64
}

func (ft FlatTax) ApplyTax(gross float64) float64 {
	return gross * (1.0 - ft.Rate)
}

type ZeroTax struct{}

func (ZeroTax) ApplyTax(gross float64) float64 { return gross }

// ------------------------------------------------------------------------
// COMPOSABLE EMPLOYEE STRUCT (Zero Inheritance)
// ------------------------------------------------------------------------

type Employee struct {
	ID         string
	Name       string
	BaseSalary float64
	Bonus      BonusPolicy
	Currency   CurrencyConverter
	Tax        TaxStrategy
}

func (e *Employee) CalculateNetPay() (float64, string) {
	// 1. Calculate Gross in base currency
	bonus := e.Bonus.CalculateBonus(e.BaseSalary)
	gross := e.BaseSalary + bonus

	// 2. Convert currency
	convertedAmount, currencyCode := e.Currency.Convert(gross)

	// 3. Apply regional taxes
	net := e.Tax.ApplyTax(convertedAmount)

	return net, currencyCode
}

func main() {
	// Standard Domestic Salaried Employee
	eng := Employee{
		ID:         "EMP-101",
		Name:       "Alice Engineer",
		BaseSalary: 120_000.00,
		Bonus:      PercentageBonus{Percentage: 0.15}, // 15% bonus
		Currency:   DomesticCurrency{},
		Tax:        FlatTax{Rate: 0.25},
	}

	// Executive in Tokyo: Any combination of strategies works without subclasses!
	exec := Employee{
		ID:         "EXEC-001",
		Name:       "Kenji Executive",
		BaseSalary: 200_000.00,
		Bonus:      FixedBonus{Amount: 50_000.00},
		Currency:   ForeignCurrency{Code: "JPY", ExchangeRate: 155.0},
		Tax:        FlatTax{Rate: 0.30},
	}

	net1, curr1 := eng.CalculateNetPay()
	fmt.Printf("[Go] %s (%s) Net Pay: %.2f %s\n", eng.Name, eng.ID, net1, curr1)

	net2, curr2 := exec.CalculateNetPay()
	fmt.Printf("[Go] %s (%s) Net Pay: %.2f %s\n", exec.Name, exec.ID, net2, curr2)
}
```

---

## Part 3: Days 3–4 — The Rust Trait Strategy Solution (`src/main.rs`)

In Rust, we achieve total type safety and zero runtime overhead using generic trait parameters (monomorphization) and the Newtype pattern.

Save this in `labs/week05/rust/src/main.rs`:

```rust
use std::fmt;

// ------------------------------------------------------------------------
// NEWTYPE PATTERN: Strong typing prevents accidental currency mixing
// ------------------------------------------------------------------------
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct UsdAmount(pub f64);

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Money {
    pub amount: f64,
    pub currency: &'static str,
}

impl fmt::Display for Money {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:.2} {}", self.amount, self.currency)
    }
}

// ------------------------------------------------------------------------
// STRATEGY TRAITS
// ------------------------------------------------------------------------
pub trait BonusPolicy {
    fn calculate_bonus(&self, base: UsdAmount) -> UsdAmount;
}

pub trait CurrencyConverter {
    fn convert(&self, usd: UsdAmount) -> Money;
}

pub trait TaxStrategy {
    fn apply_tax(&self, gross: Money) -> Money;
}

// ------------------------------------------------------------------------
// CONCRETE STRATEGIES
// ------------------------------------------------------------------------
pub struct PercentageBonus(pub f64);
impl BonusPolicy for PercentageBonus {
    fn calculate_bonus(&self, base: UsdAmount) -> UsdAmount {
        UsdAmount(base.0 * self.0)
    }
}

pub struct FixedBonus(pub f64);
impl BonusPolicy for FixedBonus {
    fn calculate_bonus(&self, _base: UsdAmount) -> UsdAmount {
        UsdAmount(self.0)
    }
}

pub struct DomesticUsd;
impl CurrencyConverter for DomesticUsd {
    fn convert(&self, usd: UsdAmount) -> Money {
        Money {
            amount: usd.0,
            currency: "USD",
        }
    }
}

pub struct ForeignCurrency {
    pub code: &'static str,
    pub exchange_rate: f64,
}

impl CurrencyConverter for ForeignCurrency {
    fn convert(&self, usd: UsdAmount) -> Money {
        Money {
            amount: usd.0 * self.exchange_rate,
            currency: self.code,
        }
    }
}

pub struct FlatTax(pub f64);
impl TaxStrategy for FlatTax {
    fn apply_tax(&self, gross: Money) -> Money {
        Money {
            amount: gross.amount * (1.0 - self.0),
            currency: gross.currency,
        }
    }
}

// ------------------------------------------------------------------------
// COMPOSABLE EMPLOYEE: Fully Monomorphized (Zero Vtable Overhead!)
// ------------------------------------------------------------------------
pub struct Employee<B: BonusPolicy, C: CurrencyConverter, T: TaxStrategy> {
    pub id: &'static str,
    pub name: &'static str,
    pub base_salary: UsdAmount,
    pub bonus_policy: B,
    pub currency_converter: C,
    pub tax_strategy: T,
}

impl<B: BonusPolicy, C: CurrencyConverter, T: TaxStrategy> Employee<B, C, T> {
    pub fn calculate_net_pay(&self) -> Money {
        let bonus = self.bonus_policy.calculate_bonus(self.base_salary);
        let gross_usd = UsdAmount(self.base_salary.0 + bonus.0);
        let gross_local = self.currency_converter.convert(gross_usd);
        self.tax_strategy.apply_tax(gross_local)
    }
}

fn main() {
    println!("=== RUST COMPOSABLE PAYROLL ENGINE ===");

    let domestic_engineer = Employee {
        id: "ENG-001",
        name: "Carol Danvers",
        base_salary: UsdAmount(140_000.0),
        bonus_policy: PercentageBonus(0.20),
        currency_converter: DomesticUsd,
        tax_strategy: FlatTax(0.28),
    };

    let international_executive = Employee {
        id: "EXEC-002",
        name: "Bruce Wayne",
        base_salary: UsdAmount(300_000.0),
        bonus_policy: FixedBonus(100_000.0),
        currency_converter: ForeignCurrency {
            code: "EUR",
            exchange_rate: 0.92,
        },
        tax_strategy: FlatTax(0.40),
    };

    println!(
        "{} ({}) Net: {}",
        domestic_engineer.name,
        domestic_engineer.id,
        domestic_engineer.calculate_net_pay()
    );
    println!(
        "{} ({}) Net: {}",
        international_executive.name,
        international_executive.id,
        international_executive.calculate_net_pay()
    );
}
```

---

## Part 4: Friday Mob Review & Defense Protocol

Gather the 5-engineer team. Have one engineer perform the **Extensibility Challenge**: add a new `CryptoBonus` strategy without touching existing employee code.

### 7 Mandatory Technical Defense Questions

#### 1. How did our refactoring solve the combinatorial explosion of subclasses?
* **Expected Answer:** In the inheritance model, combining $N$ independent features (bonus types, currencies, tax jurisdictions) requires creating $2^N$ subclasses (`ExecutiveInternationalDiscountedEmployee`). By using composition, the features are decoupled into independent strategies. We have $1$ struct (`Employee`) that composes $N$ strategies dynamically.

#### 2. What is the Fragile Base Class problem, and how does composition prevent it?
* **Expected Answer:** The Fragile Base Class problem happens when changes to a base class's internal method calls silently break derived classes (such as `AddRange` calling `Add`, causing derived classes to double-count). Composition prevents this because components interact solely through explicit public interfaces without shared internal inheritance state or invisible `base` calls.

#### 3. In our Go refactoring, why did we use structural interfaces instead of struct embedding for bonuses?
* **Expected Answer:** Struct embedding is an "is-a" (or static "has-a") relationship that embeds a single concrete struct in-place. By using a `BonusPolicy` interface, we achieve **runtime polymorphism**, allowing different instances of `Employee` to carry completely different bonus calculation algorithms (`PercentageBonus`, `FixedBonus`, `NoBonus`) interchangeably.

#### 4. In our Rust implementation, why does `Employee<B, C, T>` have zero vtable overhead?
* **Expected Answer:** Rust uses **monomorphization** for generic parameters (`<B, C, T>`). At compile time, the compiler generates a dedicated, specialized copy of `Employee` and its methods for each concrete combination of types. All method calls are inlined and statically resolved (direct jumps), with **zero vtables, zero fat pointers, and zero runtime indirection**.

#### 5. What purpose does the Newtype `UsdAmount(pub f64)` serve?
* **Expected Answer:** In primitive obsession code, developers frequently pass a raw `f64` representing Euros into a function expecting USD. The Newtype pattern wraps `f64` in a distinct type at compile-time with zero runtime memory cost, making it a compiler error to pass incompatible currencies without explicit conversion.

#### 6. Why does Go's struct embedding lack a polymorphic `this` pointer?
* **Expected Answer:** In Go, methods are declared with explicit value or pointer receivers (`func (b *Base) Method()`). When an embedded method executes, its receiver is strictly bound to the embedded struct instance, not the outer struct. Go contains no vtables in struct layouts, so it is physically impossible to dispatch back to the outer struct.

#### 7. How does the Open-Closed Principle (OCP) apply to our Rust refactored payroll?
* **Expected Answer:** The system is **Open for extension** (we can add a `CryptoCurrencyConverter` or `StockOptionBonus` by simply implementing the respective trait) and **Closed for modification** (we never modify the `Employee` struct, `calculate_net_pay()`, or existing strategies to introduce new business logic).

---

## Lab Sign-off Checklist

Each engineer must verify and sign off:
* [ ] **Deconstruction Competency:** I have replaced a multi-level inheritance hierarchy with decoupled strategy components.
* [ ] **Go Interface Composition:** I understand how to inject behavior strategies into Go structs.
* [ ] **Monomorphization Literacy:** I understand how Rust generics generate zero-cost, vtable-free dispatch for strategy traits.
* [ ] **Newtype Pattern:** I can implement and explain the zero-cost type safety benefits of the Newtype pattern.
* [ ] **Fragile Base Class Proof:** I can explain why base class changes cause silent regressions in OOP hierarchies.
