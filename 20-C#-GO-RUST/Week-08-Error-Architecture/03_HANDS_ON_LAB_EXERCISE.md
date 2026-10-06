# Week 08 Hands-On Lab: Chained Financial Transaction Pipeline

## Scenario

You are building the core processing engine for a financial payment gateway. The pipeline executes a strict sequence of operations to process a transaction:
1. `FetchAccount`: Retrieves account details from the database.
2. `ValidateBalance`: Ensures the account has sufficient funds.
3. `ReserveFunds`: Places a temporary hold on the funds.
4. `ExecuteTransfer`: Commits the transaction to the ledger.

If *any* step fails, the entire pipeline must abort. Furthermore, the final error reported to the system must include the **exact step that failed**, the **Account ID**, the **Transaction ID**, and the **Root Cause Error** (e.g., a network timeout from the database).

In C#, a developer might lazily throw exceptions at each step and let a top-level `catch` block handle it. In this lab, we will build rigid, fully-typed error chains in Go and Rust that guarantee no information is lost and no unexpected panics occur.

---

## Days 1-2: The Go Implementation (Reference)

Examine the Go implementation. Note how we define a structured error type to capture the pipeline context, and how we use `fmt.Errorf` to build the chain.

### `pipeline.go`

```go
package main

import (
	"errors"
	"fmt"
	"time"
)

// --- Domain Models ---

type Account struct {
	ID      string
	Balance float64
}

type Transaction struct {
	TxID   string
	Amount float64
}

// --- Error Architecture ---

// PipelineError captures rich context about where and why a pipeline failed.
type PipelineError struct {
	Step      string
	AccountID string
	TxID      string
	Err       error // The root cause
}

// Implement the error interface
func (pe *PipelineError) Error() string {
	return fmt.Sprintf("pipeline failed at step [%s] for Tx %s, Account %s: %v", 
		pe.Step, pe.TxID, pe.AccountID, pe.Err)
}

// Implement Unwrap so errors.Is and errors.As can walk down to the root cause
func (pe *PipelineError) Unwrap() error {
	return pe.Err
}

// Sentinel root causes
var (
	ErrAccountNotFound  = errors.New("account not found in database")
	ErrInsufficientFund = errors.New("insufficient funds")
	ErrNetworkTimeout   = errors.New("database network timeout")
)

// --- Pipeline Steps ---

func FetchAccount(accountID string) (*Account, error) {
	// Simulate a database network timeout
	if accountID == "TIMEOUT_ACC" {
		return nil, ErrNetworkTimeout
	}
	if accountID != "ACC-123" {
		return nil, ErrAccountNotFound
	}
	return &Account{ID: accountID, Balance: 100.00}, nil
}

func ValidateBalance(acc *Account, tx *Transaction) error {
	if acc.Balance < tx.Amount {
		return ErrInsufficientFund
	}
	return nil
}

func ReserveFunds(acc *Account, tx *Transaction) error {
	// Simulate success
	return nil
}

func ExecuteTransfer(acc *Account, tx *Transaction) error {
	// Simulate success
	return nil
}

// --- Core Pipeline Orchestrator ---

func ProcessTransaction(accountID string, tx *Transaction) error {
	// Helper function to wrap errors with pipeline context
	wrapErr := func(step string, err error) error {
		if err == nil {
			return nil
		}
		return &PipelineError{
			Step:      step,
			AccountID: accountID,
			TxID:      tx.TxID,
			Err:       err,
		}
	}

	acc, err := FetchAccount(accountID)
	if err != nil {
		return wrapErr("FetchAccount", err)
	}

	if err := ValidateBalance(acc, tx); err != nil {
		return wrapErr("ValidateBalance", err)
	}

	if err := ReserveFunds(acc, tx); err != nil {
		// Imagine we might need to do some rollback here eventually
		return wrapErr("ReserveFunds", err)
	}

	if err := ExecuteTransfer(acc, tx); err != nil {
		return wrapErr("ExecuteTransfer", err)
	}

	return nil
}

func main() {
	tx := &Transaction{TxID: "TX-999", Amount: 200.00}
	
	// Test failure: Insufficient Funds
	err := ProcessTransaction("ACC-123", tx)
	if err != nil {
		fmt.Printf("Error: %v\n", err)
		
		// Check for specific root cause
		if errors.Is(err, ErrInsufficientFund) {
			fmt.Println("Action: Prompt user to top up account.")
		}
	}
}
```

---

## Days 3-4: The Rust Lab (Skeleton)

Your task is to implement the same pipeline in Rust. 

**Requirements:**
1. Use `thiserror` to define the root cause enum (`RootError`).
2. Define a custom struct `PipelineError` that contains the step name, IDs, and the `RootError`. 
3. Implement `std::error::Error` for `PipelineError` manually or via `thiserror`.
4. Use the `?` operator combined with `map_err` to elegantly transform the `RootError` from each step into a `PipelineError` with the correct context.

### `src/main.rs` (Skeleton)

```rust
use std::fmt;
use thiserror::Error;

// --- Domain Models ---
#[derive(Debug)]
pub struct Account {
    pub id: String,
    pub balance: f64,
}

#[derive(Debug)]
pub struct Transaction {
    pub tx_id: String,
    pub amount: f64,
}

// --- Error Architecture (TODO) ---

// TODO 1: Define `RootError` enum using `#[derive(Error, Debug)]`
// It should have variants: AccountNotFound, InsufficientFunds, NetworkTimeout.

// TODO 2: Define `PipelineError` struct.
// Fields: step (String), account_id (String), tx_id (String), source (RootError).
// Implement Display and std::error::Error for PipelineError.

// --- Pipeline Steps ---

// TODO 3: Implement FetchAccount
// fn fetch_account(account_id: &str) -> Result<Account, RootError> { ... }

// TODO 4: Implement ValidateBalance
// fn validate_balance(acc: &Account, tx: &Transaction) -> Result<(), RootError> { ... }

// TODO 5: Implement ReserveFunds
// fn reserve_funds(acc: &Account, tx: &Transaction) -> Result<(), RootError> { ... }

// TODO 6: Implement ExecuteTransfer
// fn execute_transfer(acc: &Account, tx: &Transaction) -> Result<(), RootError> { ... }

// --- Core Pipeline Orchestrator ---

pub fn process_transaction(account_id: &str, tx: &Transaction) -> Result<(), /* TODO: Return Type */> {
    // TODO 7: Chain the operations together.
    // HINT: Use `.map_err(|e| PipelineError { ... })?` at each step to attach context.
    
    unimplemented!("Implement the pipeline orchestration");
}

fn main() {
    let tx = Transaction { tx_id: "TX-999".to_string(), amount: 200.00 };
    
    // Test the implementation
    // match process_transaction("ACC-123", &tx) { ... }
}
```

---

## Friday: Mob Review & Sign-Off

Gather with your team and review the Rust implementations. 

### Discussion Questions (Have answers prepared)

1. **The Boilerplate Question:** Compare the C# `try/catch` approach to Rust's `.map_err().?` chaining. Which requires more typing? Which provides higher confidence that an error isn't accidentally swallowed?
2. **Memory Allocation:** When `FetchAccount` fails in C#, a heap allocation occurs for the `Exception`. Where does the memory for `RootError` and `PipelineError` live in the Rust implementation? (Answer: On the stack, they are just enums/structs returned as values).
3. **Control Flow:** How does the compiler know to exit `process_transaction` early when the `?` operator is invoked? What is the assembly equivalent of `?`?
4. **Pattern Matching vs Catch:** In `main()`, you used `match` to inspect the error. How does the Rust compiler ensure you handled all variants of `RootError` if you decided to match on `e.source`? Can C# guarantee you caught all exception types?
5. **The `anyhow` crate:** If this pipeline was the absolute top-level of an CLI application and no one else was going to import it as a library, how could we have rewritten this using `anyhow::Context` instead of a custom `PipelineError` struct?
6. **Go's `Unwrap` vs Rust's `source()`:** Both languages provide a mechanism to traverse an error chain to find a root cause. Compare Go's `errors.Is(err, target)` with Rust's `std::error::Error::source()`. 
7. **Zero Cost Abstraction:** Prove to the room that the `Result` type in Rust does not incur runtime overhead on the happy path.

### Sign-off Checklist

Each team member must verbally answer "Yes" to the following:
- [ ] I can explain why returning `Result` is faster than throwing an exception.
- [ ] I can write a function that returns a `Result` and use the `?` operator.
- [ ] I understand the difference between `thiserror` (libraries/domain) and `anyhow` (applications).
- [ ] I know how to wrap an error with additional context without losing the original stack/root cause.
- [ ] I can explain why using exceptions for normal domain validation flow is an antipattern.

### Stretch Goals for Fast Learners

1. **Retry Logic:** Modify the orchestrator (in either Go or Rust) so that if the step fails specifically with `ErrNetworkTimeout` / `RootError::NetworkTimeout`, it retries exactly 3 times with exponential backoff before returning the `PipelineError`. 
2. **Boxed Errors:** In Rust, attempt to change the return type of the steps from `Result<T, RootError>` to `Result<T, Box<dyn std::error::Error>>`. Observe how this impacts your ability to pattern match the root cause in `main()`. Why is strongly typing the error preferred in core domain logic?
