# Hands-On Lab: The Thundering Herd Problem

## Objective
Experience what happens when you overwhelm a system with unconstrained concurrency, and learn how to implement bounded concurrency (a semaphore) across C#, Go, and Rust.

## Day 1-2: Understanding the Problem and The Go Reference
Your team has built an aggregator service that calls a downstream pricing API. Under normal load, it's fine. During a traffic spike, your service fans out 1,000 concurrent calls. 
1. The downstream API crashes.
2. Even if it didn't crash, your service runs out of memory or socket connections.

### The Go Reference Implementation (Bounded Concurrency)
In Go, we don't need a heavy `Semaphore` class. We use a buffered channel.

```go
package main

import (
	"fmt"
	"sync"
	"time"
)

func main() {
	jobs := 1000
	concurrencyLimit := 50

	// The Semaphore: A buffered channel with capacity equal to our limit.
	sem := make(chan struct{}, concurrencyLimit)
	var wg sync.WaitGroup

	start := time.Now()

	for i := 0; i < jobs; i++ {
		wg.Add(1)
		go func(jobID int) {
			defer wg.Done()

			// Acquire semaphore: block if the channel is full.
			sem <- struct{}{}
			
			// Defer Release semaphore: read from the channel to free up a slot.
			defer func() { <-sem }()

			// Simulate work
			time.Sleep(10 * time.Millisecond)
		}(i)
	}

	wg.Wait()
	fmt.Printf("Completed %d jobs in %v\n", jobs, time.Since(start))
}
```

## Day 3-4: The Rust Challenge
Your task is to implement the same concurrency limit in Rust using Tokio. 

### The Rust Skeleton
```rust
use std::sync::Arc;
use tokio::sync::Semaphore;
use std::time::{Duration, Instant};

#[tokio::main]
async fn main() {
    let jobs = 1000;
    let concurrency_limit = 50;

    // TODO 1: Initialize a Tokio Semaphore wrapped in an Arc so it can be shared across tasks.
    
    let start = Instant::now();
    let mut handles = vec![];

    for i in 0..jobs {
        // TODO 2: Clone the Arc so this task can hold a reference to the semaphore.
        
        let handle = tokio::spawn(async move {
            // TODO 3: Acquire a permit from the semaphore. What happens if this fails?
            // HINT: Use `.acquire().await.unwrap()`
            
            // Simulate work
            tokio::time::sleep(Duration::from_millis(10)).await;
            
            // TODO 4: The permit is dropped here automatically at the end of the scope. 
            // Why is this safer than manual release?
        });
        handles.push(handle);
    }

    for handle in handles {
        handle.await.unwrap();
    }

    println!("Completed {} jobs in {:?}", jobs, start.elapsed());
}
```

**Rust Concepts You Will Fight:**
1. **Ownership and `Arc`**: You cannot simply pass a `Semaphore` into multiple spawned tasks. You must wrap it in an `Arc<Semaphore>` and clone the `Arc` pointer for each task.
2. **RAII (Resource Acquisition Is Initialization)**: You don't manually release the semaphore in Rust. The `acquire().await` call returns a `SemaphorePermit`. When that permit goes out of scope (is dropped), the semaphore is automatically released. 

## Friday: Mob Review & Benchmarking

### Benchmarking Exercise
1. Remove the limits in all three languages. Run 100,000 jobs that simply sleep for 10ms.
2. Profile the memory footprint of the executable while running.

| Language | 10k Concurrency Memory | 100k Concurrency Memory | Observation on Crash/Limit |
| :--- | :--- | :--- | :--- |
| **C#** | | | |
| **Go** | | | |
| **Rust** | | | |

### Discussion Questions
*   **Q:** Why does C# use `SemaphoreSlim` instead of `Semaphore` for asynchronous operations?
    *   *Expected Answer:* `Semaphore` is an OS-level kernel primitive (WaitHandle) that blocks the actual thread. `SemaphoreSlim` is a lightweight CLR construct that supports async waiting (`WaitAsync`), yielding the thread back to the pool while waiting for a slot.
*   **Q:** In the Go implementation, we used a buffered channel `make(chan struct{}, 50)`. Why is `struct{}` used instead of `bool` or `int`?
    *   *Expected Answer:* The empty struct `struct{}` occupies zero bytes of memory in Go. It signifies we only care about the synchronization properties of the channel, not the data payload.

## Sign-off Checklist
- [ ] We successfully crashed the C# application by attempting to spawn 1,000,000 `Task.Run` calls without limits.
- [ ] We implemented `SemaphoreSlim` in C#.
- [ ] We successfully wrote and ran the Go buffered channel semaphore.
- [ ] We successfully wrapped the Tokio `Semaphore` in an `Arc` and completed the Rust lab.
- [ ] We can clearly articulate why dropping a `SemaphorePermit` in Rust is superior to manually calling `Release()` in C# `try/finally` blocks.

## Stretch Goal
Modify the C# implementation to use `Parallel.ForEachAsync` (introduced in .NET 6) instead of manual semaphore logic. How much code does this eliminate?
