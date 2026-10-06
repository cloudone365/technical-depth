# 03_HANDS_ON_LAB_EXERCISE: Dead Letter Queues and Resiliency

## Overview
Failures in asynchronous systems are inevitable. A database lock timeout, a 3rd party API going down, or a malformed JSON payload will crash consumer logic. In this lab, you will implement a reliable Dead Letter Queue (DLQ) topology using RabbitMQ and build a specialized inspector tool in Go and Rust to manage failed messages.

## Day 1-2: Configuring the Topology and Forcing Failures
You are provided with a basic order processing application across C#, Go, and Rust.

### Task 1: Setup the DLQ Topology
Modify the RabbitMQ queue configuration in the startup code for all three languages.
1. Declare a primary queue: `orders_main_q`
2. Declare a DLQ exchange: `orders_dlx`
3. Declare a DLQ queue: `orders_dlq` and bind it to the `orders_dlx`.
4. Configure `orders_main_q` with the argument `x-dead-letter-exchange` pointing to `orders_dlx`.

### Task 2: Implement the "Poison Pill"
Inject a deliberate bug into the `ProcessPayment` logic:
- If the `customer_name` is exactly `"POISON"`, throw an unhandled exception / return an unrecoverable error, and explicitly `Nack(requeue=false)`.
- **C#**: MassTransit handles DLQ natively if you configure fault consumers, but for this lab, configure standard RabbitMQ DLX arguments directly.
- **Go**: Use `amqp091` to `d.Nack(false, false)`.
- **Rust**: Use `lapin` to `.nack(BasicNackOptions { multiple: false, requeue: false })`.

Send 10 valid orders and 5 poison orders via the provided HTTP publisher. Verify using the RabbitMQ Management UI (http://localhost:15672) that the 5 poison messages end up in `orders_dlq`.

## Day 3-4: The DLQ Inspector Tool
You must now recover the system. 

### Task
Write a CLI tool in **Go** or **Rust** (choose one) that:
1. Connects to RabbitMQ.
2. Consumes messages from `orders_dlq` (without acking them immediately).
3. Prints the payload to the console and prompts the operator: `[R]etry, [D]iscard, [S]kip?`
4. If `R`: Extract the payload, publish it back to the original `orders_main_q`, and `Ack` the DLQ message.
5. If `D`: `Ack` the DLQ message (permanently deleting it).
6. If `S`: `Nack` the DLQ message with requeue=true (leaving it in the DLQ for later).

**Rust Concepts You Will Fight:**
- Managing interactive CLI input via `std::io::stdin` while inside a Tokio async context.
- Handling string parsing and pattern matching (`match` statement) for operator input.

## Friday: Mob Review & Benchmarking

### Replay Scenario
1. Fix the "POISON" bug in the consumer codebase and deploy the fix.
2. Use your DLQ Inspector CLI to mark all 5 poison messages for `[R]etry`.
3. Watch the consumer successfully process them.

### Discussion Questions
1. In C#, how does MassTransit's native `_error` queue differ from RabbitMQ's built-in DLX topology? Which is more language-agnostic?
2. When building the Inspector Tool in Go, how did you handle the blocking `fmt.Scanln` for user input without blocking the entire RabbitMQ consumption channel? 
3. In Rust, what happens if the CLI tool crashes halfway through inspecting a message? Does the message disappear? (Answer: No, because un-acked messages return to the queue).

### Sign-off Checklist
- [ ] Do poison messages successfully route to the `orders_dlq` across all three languages?
- [ ] Does the Go/Rust CLI tool successfully parse DLQ messages?
- [ ] Does selecting `[R]etry` successfully move the message back to the main queue and delete it from the DLQ?
- [ ] Does selecting `[D]iscard` successfully delete the message from the DLQ without republishing?
- [ ] Was the "POISON" bug successfully fixed and the messages replayed successfully?

### Stretch Goal
Modify the DLQ inspector to implement a "Delay" feature. When a message is Retried, publish it with an `x-delay` header (using the RabbitMQ Delayed Message Plugin) so it waits 5 minutes before hitting the main queue again.
