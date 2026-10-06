# Week 10: Functional Pipelines & Zero-Cost Iteration

## Why This Week Matters for Your Career Transition
For a C# developer, `IEnumerable<T>` and LINQ are second nature. You are accustomed to effortlessly chaining `.Where()`, `.Select()`, and `.GroupBy()` to transform data. However, as you transition to Go and Rust, attempting to replicate LINQ will lead to either unidiomatic code (in Go) or compile-time frustration (in Rust). By the end of this week, you will understand exactly how the C# compiler generates state machines for `yield return`, why Go deliberately forces you to write explicit `for` loops (and how Go 1.22 iterators fit in), and how Rust achieves true zero-cost abstractions—compiling massive iterator chains down to a single optimized loop with no heap allocation. You will also finally master closure semantics, unlearning the classic C# loop variable capture bug and understanding move semantics.

## The Baseline: C# IEnumerable<T> and State Machines
In C#, lazy evaluation is driven by `IEnumerable<T>` and the `yield return` keyword. When you write a method returning `IEnumerable<T>` with `yield return`, the compiler rewrites your method into a hidden state machine class implementing `IEnumerator<T>`. 

### Lazy vs Eager Evaluation
LINQ relies heavily on lazy evaluation. A chain like `source.Where(x => x > 0).Select(x => x * 2)` does absolutely nothing until you iterate it with `foreach`, `.ToList()`, or `.FirstOrDefault()`.
*   **The Multiple Enumeration Bug:** Because LINQ is lazy, iterating an `IEnumerable<T>` twice executes the entire pipeline (and any side-effects, like DB calls or network requests) twice. 
*   **IQueryable<T>:** Differentiates from `IEnumerable<T>` by building expression trees rather than delegates, allowing providers (like Entity Framework) to translate code into SQL.

### Closure Capture in C#
C# closures capture variables by reference (conceptually). This leads to the infamous loop variable capture bug (prior to C# 5.0's fix for `foreach`):
```csharp
var actions = new List<Action>();
for (int i = 0; i < 5; i++) {
    actions.Add(() => Console.WriteLine(i)); // Prints 5, 5, 5, 5, 5
}
```
The closure captures the memory location of `i`, not its value at the time of creation.

## Go: Readability Over Conciseness
Go historically rejected LINQ-style chaining. Why? Because the Go creators believe that hiding complexity inside map/filter/reduce chains makes performance characteristics obscure.

### Explicit Range-Based Loops
In Go, you transform data by manually appending to slices in a `for` loop. This eager evaluation makes memory allocation explicit. You know exactly when a slice is created and when it grows.

### Go 1.22: Range Over Functions
Go 1.22 introduced "range over functions" (iterators). An iterator in Go is essentially a function that takes a yield function:
```go
func MyIterator(yield func(int) bool) {
    for i := 0; i < 5; i++ {
        if !yield(i) { break }
    }
}
```
This enables lazy evaluation, but Go still eschews massive method chains, preferring discrete function calls for adapters.

### Closure Capture Semantics in Go
Go behaves similarly to C# pre-5.0 with loop variables. Prior to Go 1.22, the `for i := range arr` loop variable was shared across iterations. Goroutines capturing this variable would all see the final value. Go 1.22 fixed this by creating a new variable per loop iteration.

## Rust: The Iterator Trait and Zero-Cost Abstractions
Rust takes a radically different approach. The `Iterator` trait is a core pillar of the language.

```rust
pub trait Iterator {
    type Item;
    fn next(&mut self) -> Option<Self::Item>;
}
```
### Associated Types and Method Chaining
The `type Item` is an associated type. When you call `.map()`, it doesn't return `IEnumerable<T>`. It returns a specific, strongly-typed struct `Map<I, F>`. 
Because Rust knows the exact types of every adapter in the chain at compile time, the LLVM backend can inline the entire chain into a single tight loop—hence "zero-cost abstraction."

### Closure Types in Rust
Rust closures capture their environment based on *how* they use the captured variables, leading to three traits:
1.  **`FnOnce`**: Takes capture by value. Can only be called once.
2.  **`FnMut`**: Takes capture by mutable reference. Can mutate captured state.
3.  **`Fn`**: Takes capture by immutable reference.

To guarantee thread safety or return a closure, you often must force it to take ownership of captured variables using the `move` keyword: `move || { ... }`.

### Iterator Adapters and Consumption
-   **Consuming:** Adapters like `.collect()`, `.fold()`, or `.sum()` drive the iterator to completion.
-   **Non-consuming:** `.map()`, `.filter()`, `.take()` return lazy structs.
-   **`into_iter()` vs `iter()`**: `into_iter()` takes ownership of the collection (consumes it). `iter()` yields immutable references.

## Common Misconceptions to Unlearn
1.  **"Iterators allocate memory on the heap."** In C# LINQ, yes, enumerators are often reference types allocating state machines. In Rust, iterators are usually stack-allocated, zero-size types, or simple pointers.
2.  **"Go needs map/filter/reduce."** Go explicitly avoids this to keep algorithmic complexity visually obvious. Writing a loop is standard idiom.

## Summary Table

| Feature | C# (.NET) | Go | Rust |
| :--- | :--- | :--- | :--- |
| **Primary Mechanism** | `IEnumerable<T>`, LINQ | `for` loops, Go 1.22 `iter` | `Iterator` trait |
| **Evaluation Model** | Lazy (state machines) | Eager (loops), Lazy (Go 1.22) | Lazy (zero-cost structs) |
| **State Machine Gen.** | Yes, via `yield return` | No | No (struct nesting, but yes for async/await) |
| **Closure Default** | By Reference (class closure) | By Reference | Auto-inferred (ref, mut ref, or value) |
| **Allocation Cost** | High (heap, interface dispatch) | Medium (slice allocation) | Zero (stack, fully inlined by LLVM) |
