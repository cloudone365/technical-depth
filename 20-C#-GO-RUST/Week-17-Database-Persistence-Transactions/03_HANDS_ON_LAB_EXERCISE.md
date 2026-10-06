# 03_HANDS_ON_LAB_EXERCISE: N+1 Queries and Reliable Event Publishing

## Overview
In this lab, you will identify and eliminate an N+1 query performance bottleneck across multiple environments. Then, you will implement the Transactional Outbox pattern to guarantee that domain events are safely stored alongside business data before being published asynchronously.

## Day 1-2: The N+1 Query Bug Safari
We provide an existing `get_orders_with_items` HTTP endpoint in C#, Go, and Rust. All three currently loop through orders and execute a subsequent query to fetch line items for each order.

### Task 1: Detect the Bug
1. Enable SQL statement logging in each language:
   - **C#**: Enable `EnableSensitiveDataLogging` and `LogTo` on `DbContextOptions`.
   - **Go**: Use `pgx` tracer to log executed SQL.
   - **Rust**: Enable tracing using `tracing-subscriber` and SQLx's built-in logging.
2. Hit the endpoint. Observe the logs. Count the number of queries executed for 100 orders.

### Task 2: Fix with Efficient JOINs
Refactor the implementation to use a single `JOIN` query and map the results into the hierarchical domain model.
- **C#**: Use `.Include()` in EF Core, or a Dapper multi-mapping function (`QueryAsync<Order, LineItem, Order>`).
- **Go**: Use `sqlc` to define a single `SELECT ... JOIN` and map the returned flattened rows into nested structs manually.
- **Rust**: Use SQLx `query!` with a JOIN. Group the flattened rows by `order_id` in a Rust `HashMap` or `Vec`.

## Day 3-4: The Transactional Outbox Pattern
Directly publishing to RabbitMQ or Kafka in the middle of a database transaction is an anti-pattern. If the DB commits but Kafka is down, the event is lost. If Kafka acks but the DB rolls back, you've published a ghost event.

### Task
Implement an atomic outbox.
1. Create an `outbox_events` table (id, event_type, payload, status).
2. Inside your `create_order` transaction, insert the Order and insert the Event.
3. Write a separate background worker that polls the `outbox_events` table, publishes pending events to the console, and marks them as 'processed'.

**Specific Language Hurdles You Will Fight:**
- **Go**: You'll need to manage the background worker using a Goroutine and `time.Ticker`, ensuring graceful shutdown with `context.Context`.
- **Rust**: You'll spawn a background Tokio task (`tokio::spawn`). You will fight the borrow checker if your background task needs the `PgPool` but doesn't take ownership of an `Arc<PgPool>`.

## Friday: Mob Review & Benchmarking

### Benchmarking Commands to Run
Compare the performance of the N+1 code vs the JOIN code using a load testing tool like `hey` or `k6`.
- `hey -n 1000 -c 50 http://localhost:8080/orders`

Run `EXPLAIN ANALYZE` on the PostgreSQL CLI for the JOIN query to prove it utilizes indexes correctly.

### Discussion Questions
1. In C#, EF Core's `.Include()` solves N+1 but generates a massive cartesian product (cartesian explosion). How does Go or Rust mapping a raw JOIN handle this memory-wise?
2. Why is a background worker polling the database (Outbox pattern) better than publishing to a queue synchronously? What is the alternative to DB polling? (Answer: DB Log tailing / Change Data Capture like Debezium).
3. In Rust, how did `tokio::spawn` ensure that the outbox worker did not block the main async runtime thread?

### Sign-off Checklist
- [ ] Are SQL logs explicitly showing exactly 1 query execution for the `get_orders` endpoint?
- [ ] Is the outbox table correctly populated when an order is created?
- [ ] If you simulate a DB rollback after the outbox insert, does the outbox event disappear?
- [ ] Does the Go background worker shut down gracefully when `SIGINT` is received?
- [ ] Does the Rust application compile with the `PgPool` shared between the HTTP server and the Tokio background worker?

### Stretch Goal
Implement Change Data Capture (CDC) using PostgreSQL's `LISTEN/NOTIFY` in Go or Rust to eliminate the need for the outbox worker to poll on a timer.
