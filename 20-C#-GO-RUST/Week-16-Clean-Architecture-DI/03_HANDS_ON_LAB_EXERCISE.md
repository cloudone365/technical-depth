# 03_HANDS_ON_LAB_EXERCISE: Clean Architecture Refactoring

## Overview
In this lab, you will practice decoupling tightly bound business logic from infrastructure concerns. You will experience firsthand how Dependency Inversion changes the structure of an application in C#, Go, and Rust.

## Day 1-2: Refactoring a Tightly-Coupled Go Service
We provide a Go application where HTTP handlers, business logic, and direct database queries via `sql.DB` are all mixed into a single massive function in `main.go`.

### Task: 
Refactor the Go code into a Clean Architecture structure.
1. Create an `OrderRepository` interface.
2. Move the SQL queries into a `postgresOrderRepository` struct that implements the interface.
3. Create an `OrderService` struct that takes the interface as a dependency.
4. Refactor `main.go` to inject the repository into the service, and the service into the HTTP handler.

**Reference Go Implementation (Target State):**
```go
// domain/repository.go
package domain
type OrderRepository interface {
    Save(ctx context.Context, order *Order) error
}

// infrastructure/postgres_repo.go
package infrastructure
type pgRepo struct { db *sql.DB }
func NewPgRepo(db *sql.DB) domain.OrderRepository { return &pgRepo{db: db} }
func (r *pgRepo) Save(ctx context.Context, order *domain.Order) error { /* sql execute */ }

// usecase/order_service.go
package usecase
type OrderService struct { repo domain.OrderRepository }
func NewOrderService(r domain.OrderRepository) *OrderService { return &OrderService{repo: r} }

// main.go
func main() {
    db := initDB()
    repo := infrastructure.NewPgRepo(db)
    svc := usecase.NewOrderService(repo)
    http.Handle("/orders", handler.NewOrderHandler(svc))
}
```

## Day 3-4: Refactoring Static Dependencies in C# and Rust

### Task 1: C# Static Dependency
You are given a C# class `OrderProcessor` that instantiates `new SqlTransactionManager()` directly inside its method.
- **Challenge**: Extract an interface `ITransactionManager` and inject it. Verify that the unit tests can now inject a `MockTransactionManager`.

### Task 2: Rust Refactoring
You are given a Rust starter codebase.
- **Goal**: Implement the `EventPublisher` trait for two backends: `KafkaPublisher` and `RedisPublisher`.
- **Rust Concepts You Will Fight**:
    1. **Object Safety**: You will try to put async functions in traits. You'll need `#[async_trait]` or Rust 1.75+ native async traits.
    2. **Lifetimes and Strings**: Passing `&str` through trait boundaries vs taking ownership with `String`.
    3. **Send + Sync**: The compiler will reject your code if your dependencies are passed across threads (Tokio spawn) without `Arc` and proper `Send + Sync` bounds.

## Friday: Mob Review & Benchmarking

### Benchmarking Commands to Run
Compare the binary sizes and startup times of the final executables:
- **C#**: `dotnet publish -c Release -p:PublishAot=true` (Measure startup time using benchmark script)
- **Go**: `go build -ldflags="-s -w" -o service`
- **Rust**: `cargo build --release`

### Discussion Questions
1. In Go, why did we return the interface `domain.OrderRepository` from `NewPgRepo` rather than returning a struct? (Hint: Accept interfaces, return structs).
2. In C#, how much boilerplate did `Microsoft.Extensions.DependencyInjection` save us compared to Go? Was the trade-off worth the hidden runtime complexity?
3. In Rust, what was the impact of using `Arc<dyn Repository>` vs generic bounds `<R: Repository>` on compile time and code verbosity?

### Sign-off Checklist
- [ ] Did you successfully decouple the HTTP layer from the SQL layer in Go?
- [ ] Does the C# `OrderProcessor` successfully compile with dependency injection?
- [ ] In Rust, did you satisfy the `Send + Sync` bounds for the shared dependencies?
- [ ] Were the binary sizes recorded and compared?
- [ ] Can you swap the `KafkaPublisher` for the `RedisPublisher` in Rust solely by changing the injection in `main.rs`?

### Stretch Goal
Implement compile-time DI in the Go service using `google/wire`. Create a `wire.go` file with a `ProviderSet` and run `wire`.
