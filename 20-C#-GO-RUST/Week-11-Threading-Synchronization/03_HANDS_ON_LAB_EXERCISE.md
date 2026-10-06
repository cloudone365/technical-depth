# Week 11 Hands-on Lab: Detecting and Fixing Data Races

## Day 1-2: The Racy Bank Account in Go

### The Scenario
You have a simple banking application in Go. 

```go
// main.go
package main

import (
	"fmt"
	"sync"
)

type BankAccount struct {
	Balance int
}

func (a *BankAccount) Deposit(amount int, wg *sync.WaitGroup) {
	defer wg.Done()
	// Deliberate race condition!
	temp := a.Balance
	temp += amount
	a.Balance = temp
}

func main() {
	account := &BankAccount{Balance: 0}
	var wg sync.WaitGroup

	for i := 0; i < 1000; i++ {
		wg.Add(1)
		go account.Deposit(1, &wg)
	}

	wg.Wait()
	fmt.Printf("Final Balance: %d\n", account.Balance) // Expected: 1000, Actual: ???
}
```

**Task 1:** Run `go run main.go`. Notice the balance is wrong.
**Task 2:** Run `go run -race main.go`. Analyze the race detector output. Where exactly are the reads and writes conflicting?
**Task 3:** Fix the Go code by adding a `sync.Mutex` and calling `Lock()` and `Unlock()`.

---

## Day 3-4: The Rust Compiler Refuses to Compile Races

Now try to write the exact same racy code in Rust.

### The Rust Skeleton
```rust
use std::thread;

struct BankAccount {
    balance: i32,
}

fn main() {
    // Attempt 1: Just sharing a mutable reference
    let mut account = BankAccount { balance: 0 };
    let mut handles = vec![];

    for _ in 0..1000 {
        // TODO 1: Uncomment this and read the massive compile error.
        /*
        let handle = thread::spawn(|| {
            account.balance += 1;
        });
        handles.push(handle);
        */
    }

    // TODO 2: Wrap the BankAccount in an Arc<Mutex<BankAccount>>.
    // TODO 3: Clone the Arc inside the loop before passing it to thread::spawn with `move`.
    // TODO 4: Lock the mutex and increment the balance.
}
```

**Task:** Complete the TODOs to make the Rust code compile and run safely. Discuss the difference between runtime detection (`go test -race`) and compile-time prevention (`Send/Sync`).

---

## Friday Mob Review

### Benchmark
Write a benchmark comparing the overhead of the following locks under heavy contention (10, 100, 1000 threads pounding the lock):
1.  C#: `lock (obj)` vs `ReaderWriterLockSlim`
2.  Go: `sync.Mutex` vs `sync.RWMutex`
3.  Rust: `std::sync::Mutex` vs `parking_lot::Mutex`

### Comparison Table Fill-in

| Question | C# Answer | Go Answer | Rust Answer |
| :--- | :--- | :--- | :--- |
| How do you share an object safely across threads? | Just pass the reference | Pass pointer, use channels | Wrap in `Arc` |
| What happens if you lock twice on the same thread?| Proceeds safely | Deadlock / Panic | Deadlock |
| Does the language guarantee data race freedom? | No | No (tooling only) | Yes (Compile time) |

### Sign-off Checklist (Each Team Member)
- [ ] I can explain why reentrant locks are dangerous.
- [ ] I have used the Go race detector and understand its output.
- [ ] I understand why Rust's `Arc` requires `Mutex` to mutate the underlying data.
- [ ] I can articulate the difference between `Send` and `Sync` traits.
- [ ] I understand how atomic operations bypass standard locking mechanisms.
