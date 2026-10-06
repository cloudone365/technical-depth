# 01_CONCEPTUAL_DEEP_DIVE: Database Persistence, Transactions & Connection Pools

## Why This Week Matters for Your Career Transition
For a C# developer, database access is heavily abstracted by ORMs like Entity Framework Core or micro-ORMs like Dapper. You are used to `DbContext` managing the unit of work, connection pooling being handled transparently by the underlying ADO.NET driver, and LINQ translating your business logic into SQL queries. While powerful, this "magic" abstracts away the raw reality of the database connection lifecycle, transaction scopes, and query performance (the infamous N+1 query problem). This week, you will dive into the raw mechanics of persistence. By learning Go's explicit `database/sql` patterns and Rust's compile-time verified SQL macros via `SQLx`, you will understand exactly how connection pools actually operate, how transactions are meticulously scoped, and how to write highly performant, type-safe data access layers without relying on an ORM's heavy machinery.

## The C# Baseline: ADO.NET, Dapper, and EF Core
In .NET, database access sits on the ADO.NET foundation (`SqlConnection`, `SqlCommand`, `SqlDataReader`). 

### Connection Pooling
Connection pooling in C# is managed by the data provider (e.g., `Microsoft.Data.SqlClient` or `Npgsql`). When you call `connection.Open()`, it rarely opens a new physical connection; instead, it checks a managed pool. When you call `Dispose()`, the connection isn't closed but returned to the pool. The abstraction is seamless but can lead to pool exhaustion if `using` blocks are forgotten.

### EF Core and the Change Tracker
EF Core uses a `DbContext` bounded to a specific lifetime (usually Scoped per HTTP request). It tracks entity modifications through its `ChangeTracker`. When `SaveChanges()` is called, it generates a single batch of SQL `UPDATE/INSERT` statements. 
**The danger:** Lazy loading and iteration over related entities often cause the N+1 query problem, where the application issues one query to fetch the parent and N subsequent queries to fetch children.

### Transactions
Transactions in EF Core are implicitly tied to `SaveChanges()`, or explicitly managed via `BeginTransactionAsync()`. This binds the transaction to the active connection inside the `DbContext`.

## Go: The Explicit `database/sql` Contract
Go provides `database/sql`, an interface around SQL databases. It is emphatically *not* an ORM, and it is *not* a database connection.

### `sql.DB` is a Pool, not a Connection
A common mistake C# developers make is treating `sql.DB` like a single connection and opening/closing it per request. In Go, `sql.DB` represents a connection pool. You initialize it once in `main.go` and pass it down.
```go
db, err := sql.Open("postgres", "postgres://...")
db.SetMaxOpenConns(25)
db.SetMaxIdleConns(25)
```

### Rows and Resource Leaks
When you query multiple rows (`db.Query`), it returns `*sql.Rows`. You *must* iterate and explicitly close it, or the connection is locked open and never returned to the pool, rapidly starving the application.
```go
rows, err := db.QueryContext(ctx, "SELECT ...")
defer rows.Close() // CRITICAL!
```

### Context and Cancellation
Go heavily leverages the `context.Context` package. Every query (`QueryContext`, `ExecContext`) takes a context. If an HTTP request is canceled by the client, the context is canceled, and Go will physically terminate the query at the database level—something highly complex to achieve manually in .NET without deep `CancellationToken` plumbing.

### SQLC for Compile-Time Type Safety
Instead of EF Core or Dapper, modern Go leans towards `sqlc`. You write plain SQL queries in a `.sql` file, and `sqlc` generates type-safe Go structs and functions at compile time. No reflection, no runtime magic, and explicit type checking.

## Rust: SQLx and Compile-Time SQL Verification
Rust introduces an entirely novel concept with the `SQLx` crate: validating SQL queries against a live database schema at compile time.

### The `query!` Macro
In Rust, you write queries using macros:
```rust
let order = sqlx::query_as!(Order, "SELECT id, amount FROM orders WHERE id = $1", order_id)
    .fetch_one(&pool)
    .await?;
```
When you run `cargo build`, the SQLx macro connects to a database (defined by `DATABASE_URL`), executes `EXPLAIN` on the query, validates that the `orders` table exists, confirms `id` and `amount` are columns, and maps the returned types to the Rust `Order` struct. If the SQL is invalid, the code *will not compile*. This offers the type safety of EF Core but with the raw performance and explicitness of raw SQL.

### Connection Pools and Transactions
Like Go, SQLx uses a `PgPool` that is cloned (via `Arc`) and shared across the application. Transactions are explicit and act as guards.
```rust
let mut tx = pool.begin().await?;
// execute queries on tx
tx.commit().await?; // If tx is dropped before commit, it rolls back automatically (RAII)
```

## Common Misconceptions to Unlearn
1. **"Raw SQL is unmaintainable."** In Go (with sqlc) and Rust (with SQLx), raw SQL is the preferred, safe, and most maintainable approach, avoiding ORM bloat and hidden performance pitfalls.
2. **"Connections need to be manually opened per request."** In Go and Rust, the connection pool abstraction automatically handles checkout and return; you operate against the pool or transaction objects directly.
3. **"Transactions belong in the repository layer."** Because transactions require the same physical connection, they often need to span multiple repository calls. Both Go and Rust solve this by passing a transaction object (`sql.Tx` or `sqlx::Transaction`) down into the repository layer explicitly.

## Summary Comparison Table

| Feature | C# / .NET | Go | Rust |
| :--- | :--- | :--- | :--- |
| **Primary Data Access** | EF Core / Dapper / ADO.NET | `database/sql`, `pgx`, `sqlc` | SQLx / Diesel |
| **Connection Abstraction** | `SqlConnection` (implicitly pooled) | `sql.DB` (explicitly configured pool) | `PgPool` (explicitly configured pool) |
| **Transaction Management** | `DbContext` or `DbTransaction` | `sql.Tx` (passed explicitly) | `sqlx::Transaction` (passed explicitly, RAII rollback) |
| **Type Safety** | LINQ to SQL translation | `sqlc` code generation from SQL | `query!` macro compile-time verification |
| **Query Cancellation** | `CancellationToken` parameter | `context.Context` parameter | Dropping the Future |
