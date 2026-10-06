# Week 12 Hands-on Lab: Token Bucket Rate Limiter

## Day 1-2: The Token Bucket in Go

### The Scenario
You need to limit API requests to 10 requests per second. The Token Bucket algorithm is perfect for this. We will implement it using Go channels.

**The Architecture:**
- A `tokens` channel of size `capacity` (bucket size).
- A background goroutine that pushes a token into the channel every 100ms. If the channel is full, the token is dropped (non-blocking send).
- Workers that need to process a request must first read from the `tokens` channel.

### Go Reference Implementation
```go
package main

import (
	"fmt"
	"time"
)

func main() {
	rate := time.Millisecond * 100 // 10 req/s
	capacity := 5 // allow burst of 5

	tokens := make(chan struct{}, capacity)
	
	// Initial burst
	for i := 0; i < capacity; i++ {
		tokens <- struct{}{}
	}

	// Background ticker
	go func() {
		ticker := time.NewTicker(rate)
		for range ticker.C {
			select {
			case tokens <- struct{}{}:
				// Token added
			default:
				// Bucket full, drop token
			}
		}
	}()

	// Simulate 20 incoming requests
	for i := 1; i <= 20; i++ {
		<-tokens // Wait for a token
		fmt.Printf("Request %d processed at %v\n", i, time.Now().Format("15:04:05.000"))
	}
}
```
**Task:** Run this code. Observe the first 5 requests firing immediately, followed by the rest firing at a steady 100ms interval.

---

## Day 3-4: The Rust Tokio Rate Limiter

Now build the async version in Rust using `tokio::sync::mpsc`.

### The Rust Skeleton
```rust
use std::time::Duration;
use tokio::sync::mpsc;
use tokio::time;

#[tokio::main]
async fn main() {
    let capacity = 5;
    let rate = Duration::from_millis(100);

    // TODO 1: Create an mpsc channel with `capacity`.
    // let (tx, mut rx) = ...

    // TODO 2: Pre-fill the bucket with `capacity` tokens.
    // Use tx.try_send(())

    // TODO 3: Spawn a tokio background task to generate tokens.
    // Use tokio::time::interval(rate).
    // Loop and interval.tick().await, then try_send(). 
    // Ignore Full errors.

    // TODO 4: Simulate 20 requests.
    // Loop 1..=20.
    // Wait for a token using rx.recv().await.
    // println!("Request {} processed", i);
}
```

**Task:** Complete the TODOs. What happens if you try to `tx.send().await` in the background task instead of `try_send()`? (Answer: The token generator would pause when the bucket is full, which is fine for this specific algorithm, but `try_send` drops it explicitly without pausing).

---

## Friday Mob Review

### Benchmark
Load test the rate limiters. Wrap the token request inside a loop driven by a 10,000 req/sec load generator. Ensure that exactly 10 requests process per second after the initial burst.

### Comparison Table Fill-in

| Question | Go Answer | Rust (Tokio) Answer |
| :--- | :--- | :--- |
| How do you drop a message if the channel is full? | `select` with `default:` block | `try_send()` |
| Is the channel bounded? | Yes, defined at `make` | Yes, defined at `mpsc::channel(N)` |
| How are background tasks managed? | `go` keyword | `tokio::spawn` |

### Sign-off Checklist (Each Team Member)
- [ ] I can explain why channels are preferred over mutexes for rate limiting.
- [ ] I understand how unbuffered channels synchronize two routines.
- [ ] I know how to use the Go `select` statement for non-blocking sends.
- [ ] I understand why Rust's standard `mpsc` receiver is single-consumer only.
- [ ] I have successfully used Tokio's async channels.
