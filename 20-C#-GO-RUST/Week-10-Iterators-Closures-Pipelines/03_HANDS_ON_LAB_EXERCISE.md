# Week 10 Hands-on Lab: Iterators and Closure Mechanics

## Day 1-2: The Classic C# Closure Bug

### The Scenario
You are tasked with reviewing an old piece of C# code that schedules a series of background tasks.

```csharp
// Program.cs
using System;
using System.Collections.Generic;
using System.Threading.Tasks;

public class Program {
    public static async Task Main() {
        var tasks = new List<Task>();
        
        for (int i = 0; i < 5; i++) {
            tasks.Add(Task.Run(() => {
                Console.WriteLine($"Processing batch {i}");
            }));
        }

        await Task.WhenAll(tasks);
    }
}
```

**Task 1:** Run this code. You will likely see `Processing batch 5` printed five times (or similar non-deterministic output). 
**Task 2:** Fix the C# code by introducing a local block variable (`int localI = i;`). 
**Task 3:** Discuss *why* this happens: C# closures capture the variable by reference, not by value. The loop finishes quickly, setting `i` to 5, and the tasks read the final mutated value.

---

## Day 3-4: Rust `chunked_batches` Iterator

In Rust, you will implement a lazy batch processing iterator. You cannot simply use LINQ's `.Chunk()`.

### The Rust Skeleton
```rust
// src/main.rs

struct Chunked<I> {
    iter: I,
    chunk_size: usize,
}

impl<I> Iterator for Chunked<I>
where
    I: Iterator,
{
    // TODO 1: Define the Item type. It should be a Vec of the inner iterator's Item.
    type Item = Vec<I::Item>;

    fn next(&mut self) -> Option<Self::Item> {
        // TODO 2: Create a new Vec with capacity `chunk_size`.
        // TODO 3: Loop `chunk_size` times, calling `self.iter.next()`.
        // TODO 4: If `next()` returns None, break early.
        // TODO 5: If the Vec is empty, return None, otherwise return Some(Vec).
        unimplemented!()
    }
}

// Extension trait to easily call `.chunked(size)` on any iterator
trait IteratorExt: Iterator {
    fn chunked(self, chunk_size: usize) -> Chunked<Self>
    where
        Self: Sized,
    {
        Chunked { iter: self, chunk_size }
    }
}

impl<T: ?Sized> IteratorExt for T where T: Iterator {}

fn main() {
    let data = vec![1, 2, 3, 4, 5, 6, 7, 8];
    
    // You will fight the compiler here. Pay attention to ownership!
    let mut batches = data.into_iter().chunked(3);
    
    assert_eq!(batches.next(), Some(vec![1, 2, 3]));
    assert_eq!(batches.next(), Some(vec![4, 5, 6]));
    assert_eq!(batches.next(), Some(vec![7, 8]));
    assert_eq!(batches.next(), None);
    println!("Lab complete!");
}
```

---

## Friday Mob Review

### Benchmark
Use standard benchmarking tools (BenchmarkDotNet for C#, Criterion for Rust) to process a 50 Million element dataset, generating chunks of 1000 items.

Run: `cargo bench` vs `dotnet run -c Release`

### Comparison Table Fill-in

| Question | C# Answer | Rust Answer |
| :--- | :--- | :--- |
| Does the chunker allocate heap memory for the chunks? | Yes (Arrays/Lists) | Yes (Vec) |
| Does the chunker allocate heap memory for the iterator state? | Yes (Classes) | No (Zero-size Stack Struct) |
| How are errors inside the iterator propagated? | Exceptions | Result / Option |

### Sign-off Checklist (Each Team Member)
- [ ] I can explain why LINQ multiple enumeration bugs occur.
- [ ] I understand how C# lambda captures work and how to fix the loop index bug.
- [ ] I understand why Go forces explicit loops for complex iterations.
- [ ] I have successfully implemented the Rust `Iterator` trait.
- [ ] I understand the difference between `into_iter()`, `iter()`, and `iter_mut()` in Rust.
