# Week 19: Hands-On Lab Exercise

## The Testing & Quality Engineering Lab
Testing is not just about line coverage. This lab focuses on three advanced quality gates: Mutation Testing (proving your tests actually catch bugs), Property-Based Testing (verifying system invariants), and Fuzzing (finding panics through chaotic input).

---

### Day 1-2: Mutation Testing in C#
Your team has inherited a C# `PricingEngine` with 100% line coverage according to `coverlet`. However, there are bugs slipping into production.

**Step 1: Install Stryker.NET**
```bash
dotnet tool install -g dotnet-stryker
```

**Step 2: Run Stryker on the legacy C# API**
Navigate to `labs/PricingApi` and run:
```bash
dotnet stryker
```

**Step 3: Analyze the HTML Report**
Look at the generated Stryker HTML report. You will see "surviving mutants". Stryker actively modified your production code, ran your tests, and your tests *still passed*.
For example, Stryker changed this line:
```csharp
if (order.Total >= 100.00m) { ApplyDiscount(); }
```
To this:
```csharp
if (order.Total > 100.00m) { ApplyDiscount(); }
```
*Why did your tests pass when the logic was changed?* Because your test suite only checked an order total of `150.00m` and `50.00m`, missing the boundary condition of exactly `100.00m`.

**The Goal:** Achieve an 85% mutation score by writing tighter assertions and adding edge-case tests, not just adding lines to hit coverage metrics.

---

### Day 3-4: Property-Based Testing in Rust
Rather than testing specific examples (e.g., 2 items at $10 = $20), you will define properties that must hold true for ALL valid inputs.

**Step 1: Setup**
Create a new Rust project: `cargo new order_prop_tests`
Add `proptest` to `Cargo.toml`:
```toml
[dev-dependencies]
proptest = "1.0"
```

**Step 2: Implement the Invariant**
We need to prove that an `Order`'s total is always exactly equal to the sum of its items' `price * quantity`, regardless of what those values are.
Implement the following test in `src/lib.rs`.

```rust
use proptest::prelude::*;

// Domain structs
#[derive(Clone, Debug)]
pub struct Item { pub price: f64, pub qty: u32 }
pub struct Order { pub items: Vec<Item> }

impl Order {
    pub fn calculate_total(&self) -> f64 {
        self.items.iter().map(|i| i.price * i.qty as f64).sum()
    }
}

proptest! {
    // Proptest will run this function 256 times with random vectors of items
    #[test]
    fn test_order_total_is_sum_of_items(
        // Generate a vector of 1 to 100 items
        // Prices between 1.0 and 1000.0, Quantities between 1 and 10
        items in proptest::collection::vec(
            (1.0f64..1000.0, 1u32..10u32).prop_map(|(p, q)| Item { price: p, qty: q }),
            1..100
        )
    ) {
        let order = Order { items: items.clone() };
        let calculated_total = order.calculate_total();
        
        let mut expected_total = 0.0;
        for item in items {
            expected_total += item.price * (item.qty as f64);
        }

        // TODO: You will fight floating point precision here! 
        // assert_eq!(calculated_total, expected_total) WILL PANIC randomly.
        // Fix it using epsilon comparison or an integer Money pattern.
        
        // Example epsilon check:
        // assert!((calculated_total - expected_total).abs() < f64::EPSILON);
    }
}
```

**Rust Concepts You'll Fight:**
1. **`f64` precision:** Rust strictly enforces that floats cannot be reliably compared with `==`. Proptest will intentionally generate floats that cause precision loss during summation, breaking your naive `assert_eq!`.
2. **The Borrow Checker:** Notice the `.clone()` when passing the generated `items` into the `Order`. Proptest owns the generated data, and you must manage ownership carefully when validating expectations.

---

### Friday: Mob Review & Coverage Discussion

Gather your team and project the results of the week on the screen.

**Discussion Questions:**
1. **Mutation vs Line Coverage:** Why did our C# project have 100% line coverage but only a 45% mutation score initially? What false sense of security does line coverage provide?
2. **Property-Based Thinking:** How did writing the Rust `proptest` differ mentally from writing a C# `[Theory]` with `[InlineData]`?
3. **Floating Point Reality Check:** When Proptest found the floating point precision mismatch, how did the team resolve it? Did anyone refactor the Rust code to use `i64` for cents (the Money pattern) instead of `f64`?
4. **Go Fuzzing (Optional):** If anyone ran the Go fuzz test from the Rosetta stone, how fast did the fuzzer execute 1,000,000 iterations compared to C# unit tests?

**Sign-off Checklist (Each member must answer):**
1. [ ] Can you explain the difference between code coverage (line execution) and mutation score (logic effectiveness)?
2. [ ] Can you explain why Go prefers table-driven tests over xUnit's `[Theory]`?
3. [ ] Do you understand why reflection-based mocking (`Castle.DynamicProxy`) is impossible in Rust?
4. [ ] Have you successfully run a native Go fuzz test (`go test -fuzz`)?
5. [ ] Did your Rust property-based test uncover the floating-point precision bug?
6. [ ] Can you compose a basic `proptest` strategy using `prop_map`?
7. [ ] Do you know how to instruct a Go test to run in parallel using `t.Parallel()`?

---

### Stretch Goal
Implement an integration test in Go using `testcontainers-go`. Spin up a real PostgreSQL instance, run a `.sql` migration file to create an `orders` table, and write a test that inserts and queries an order using the raw `database/sql` driver without ANY mocks.
