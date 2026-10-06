# Hands-On Lab: The Circuit Breaker Pattern

## Objective
Implement a thread-safe Circuit Breaker pattern. This pattern prevents a system from repeatedly attempting an operation that is likely to fail, giving the downstream dependency time to recover. 

## Day 1-2: The Go Reference Implementation
The Circuit Breaker has three states:
- **Closed**: Requests flow normally. If a request fails, increment the failure counter. If the counter reaches the threshold (5), transition to Open.
- **Open**: All requests immediately fail without calling the downstream dependency. Start a timer. After the timer (30s) expires, transition to Half-Open.
- **Half-Open**: Allow exactly *one* test request to pass through. If it succeeds, transition to Closed. If it fails, transition back to Open.

### Go Reference
```go
package main

import (
	"errors"
	"fmt"
	"sync"
	"time"
)

type State int

const (
	StateClosed State = iota
	StateOpen
	StateHalfOpen
)

type CircuitBreaker struct {
	mu           sync.RWMutex
	state        State
	failures     int
	threshold    int
	timeout      time.Duration
	lastStateChg time.Time
}

func NewCircuitBreaker(threshold int, timeout time.Duration) *CircuitBreaker {
	return &CircuitBreaker{
		state:     StateClosed,
		threshold: threshold,
		timeout:   timeout,
	}
}

func (cb *CircuitBreaker) Execute(work func() error) error {
	cb.mu.Lock()
	
	switch cb.state {
	case StateOpen:
		if time.Since(cb.lastStateChg) >= cb.timeout {
			cb.state = StateHalfOpen
		} else {
			cb.mu.Unlock()
			return errors.New("circuit breaker is OPEN")
		}
	case StateHalfOpen:
		// Let one request through, but leave state as HalfOpen for others to fail fast
	}
	
	cb.mu.Unlock()

	err := work()

	cb.mu.Lock()
	defer cb.mu.Unlock()

	if err != nil {
		cb.failures++
		if cb.failures >= cb.threshold || cb.state == StateHalfOpen {
			cb.state = StateOpen
			cb.lastStateChg = time.Now()
		}
	} else {
		cb.failures = 0
		cb.state = StateClosed
	}

	return err
}
```

## Day 3-4: The Rust Challenge
Implement this in Rust using Tokio's synchronization primitives. You must ensure thread safety as multiple async tasks will be calling `execute` concurrently.

### The Rust Skeleton
```rust
use std::sync::Arc;
use tokio::sync::RwLock;
use std::time::{Duration, Instant};

#[derive(Debug, PartialEq)]
enum State {
    Closed,
    Open,
    HalfOpen,
}

struct CircuitBreakerInner {
    state: State,
    failures: usize,
    last_state_change: Option<Instant>,
}

pub struct CircuitBreaker {
    // TODO 1: We use Arc<RwLock> to allow multiple concurrent readers, 
    // but exclusive access when changing state.
    inner: Arc<RwLock<CircuitBreakerInner>>,
    threshold: usize,
    timeout: Duration,
}

impl CircuitBreaker {
    pub fn new(threshold: usize, timeout: Duration) -> Self {
        Self {
            inner: Arc::new(RwLock::new(CircuitBreakerInner {
                state: State::Closed,
                failures: 0,
                last_state_change: None,
            })),
            threshold,
            timeout,
        }
    }

    // TODO 2: Implement the execute method.
    // HINT: You will need a read lock first to check the state. 
    // If you need to transition to HalfOpen, you must drop the read lock and acquire a write lock.
    pub async fn execute<F, Fut, T, E>(&self, work: F) -> Result<T, String>
    where
        F: FnOnce() -> Fut,
        Fut: std::future::Future<Output = Result<T, E>>,
    {
        // 1. Check state (Read Lock)
        
        // 2. Perform work (No Locks Held - DO NOT HOLD LOCKS ACROSS .await!)
        
        // 3. Update state based on work result (Write Lock)
        
        unimplemented!()
    }
}
```

**Rust Concepts You Will Fight:**
1. **Holding locks across `.await`**: Tokio's `RwLock` is async-aware, but holding a lock while waiting for the downstream `work()` to finish defeats concurrency. You must acquire, check, drop, execute work, then acquire again to update.
2. **Read-to-Write Lock Upgrades**: Rust's `RwLock` does not support upgrading a read lock to a write lock automatically. You must explicitly drop the read lock before requesting the write lock.

## Friday: Mob Review & Benchmarking

### Code Review
Compare the C# implementation (which you should write using `SemaphoreSlim` or `lock`) with the Go and Rust versions.

### Discussion Questions
*   **Q:** In the Rust skeleton, why must we explicitly drop the read lock before awaiting the work future?
    *   *Expected Answer:* If we hold a read lock while waiting for a slow network call, no other task can acquire a write lock to update the state if *they* fail. Worse, if we try to acquire a write lock later in the same function while holding the read lock, we deadlock.
*   **Q:** How does Go's `sync.RWMutex` differ from Tokio's `tokio::sync::RwLock`?
    *   *Expected Answer:* Go's mutex blocks the underlying OS thread (or parks the goroutine). Tokio's async RwLock yields control back to the Tokio executor, allowing other tasks to run on that thread while waiting for the lock.

## Sign-off Checklist
- [ ] We successfully implemented the Circuit Breaker in C# using `lock`.
- [ ] We understand the state transitions (Closed -> Open -> HalfOpen -> Closed).
- [ ] We successfully implemented the Rust version and did NOT hold the `RwLock` across the `work().await` call.
- [ ] We wrote a test harness that simulates 100 concurrent requests during an "Open" state and verified that only 1 request leaks through during "Half-Open".

## Stretch Goal
In the Go and Rust implementations, how would you change the design so that concurrent requests during the `Half-Open` state queue up and wait for the result of the single test request, rather than immediately failing?
