# 02_CODE_COMPARISON_ROSETTA: Asynchronous Messaging & Event-Driven Integration

This document implements a reliable RabbitMQ consumer that processes `OrderCreated` events and implements idempotency. It compares MassTransit in C#, `amqp091-go` in Go, and `lapin` in Rust.

## 1. C# Implementation (MassTransit)

### Project Setup
```xml
<PackageReference Include="MassTransit.RabbitMQ" Version="8.1.0" />
```

### Code
```csharp
using System.Threading.Tasks;
using MassTransit;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.DependencyInjection;

public record OrderCreated(Guid OrderId, string CustomerName);

// WHY: Implementing IConsumer automatically binds this class to the MassTransit pipeline.
public class OrderCreatedConsumer : IConsumer<OrderCreated>
{
    public async Task Consume(ConsumeContext<OrderCreated> context)
    {
        var message = context.Message;
        
        // WHY: Idempotency check. In production, this would query a DB table.
        if (await IsAlreadyProcessed(message.OrderId))
        {
            return; // Already processed, gracefully exit (MassTransit will implicitly Ack)
        }

        Console.WriteLine($"Processing Payment for Order: {message.OrderId}");
        
        // If an exception is thrown here, MassTransit catches it, 
        // applies retry policies, and eventually moves it to an _error queue.
        await ProcessPaymentLogic();
    }

    private Task<bool> IsAlreadyProcessed(Guid orderId) => Task.FromResult(false);
    private Task ProcessPaymentLogic() => Task.CompletedTask;
}

public class Program
{
    public static void Main(string[] args)
    {
        Host.CreateDefaultBuilder(args)
            .ConfigureServices((hostContext, services) =>
            {
                services.AddMassTransit(x =>
                {
                    x.AddConsumer<OrderCreatedConsumer>();
                    x.UsingRabbitMq((context, cfg) =>
                    {
                        cfg.Host("rabbitmq://localhost");
                        
                        // WHY: Automatic topology configuration. Creates exchanges and queues matching the consumer type.
                        cfg.ReceiveEndpoint("order_events_queue", e =>
                        {
                            e.UseMessageRetry(r => r.Interval(3, TimeSpan.FromSeconds(5)));
                            e.ConfigureConsumer<OrderCreatedConsumer>(context);
                        });
                    });
                });
            })
            .Build()
            .Run();
    }
}
```

---

## 2. Go Implementation (`amqp091-go`)

### Code
```go
package main

import (
	"encoding/json"
	"log"
	"github.com/rabbitmq/amqp091-go"
)

type OrderCreated struct {
	OrderID      string `json:"order_id"`
	CustomerName string `json:"customer_name"`
}

func main() {
	// WHY: Explicit connection management.
	conn, err := amqp091.Dial("amqp://guest:guest@localhost:5672/")
	if err != nil {
		log.Fatalf("Failed to connect to RabbitMQ: %s", err)
	}
	defer conn.Close()

	ch, err := conn.Channel()
	if err != nil {
		log.Fatalf("Failed to open a channel: %s", err)
	}
	defer ch.Close()

	// WHY: Explicit Qos guarantees back-pressure. Go will not pull more than 10 un-acked messages into memory.
	err = ch.Qos(10, 0, false)

	// WHY: Manual queue declaration and binding.
	q, _ := ch.QueueDeclare("order_events_queue", true, false, false, false, nil)

	// autoAck = false ensures reliable delivery.
	msgs, err := ch.Consume(q.Name, "", false, false, false, false, nil)

	log.Printf("Waiting for messages...")

	// WHY: A channel blocks the main thread. We iterate over incoming messages continuously.
	forever := make(chan struct{})

	go func() {
		for d := range msgs {
			// WHY: Concurrently process messages in goroutines, isolated from the consume loop.
			go processMessage(d)
		}
	}()

	<-forever
}

func processMessage(d amqp091.Delivery) {
	var event OrderCreated
	if err := json.Unmarshal(d.Body, &event); err != nil {
		log.Printf("Error decoding JSON: %s", err)
		// WHY: Unparseable message. Nack with requeue=false sends it to DLQ (if configured on the broker).
		d.Nack(false, false)
		return
	}

	if isAlreadyProcessed(event.OrderID) {
		log.Printf("Ignoring duplicate order: %s", event.OrderID)
		d.Ack(false)
		return
	}

	log.Printf("Processing Payment for Order: %s", event.OrderID)

	// Simulate work
	err := processPaymentLogic()
	if err != nil {
		// Transient error, reject and requeue.
		log.Printf("Processing failed, requeuing: %s", err)
		d.Nack(false, true)
		return
	}

	// WHY: Explicit Ack only upon successful completion of business logic and DB commits.
	d.Ack(false)
}

func isAlreadyProcessed(id string) bool { return false }
func processPaymentLogic() error { return nil }
```

---

## 3. Rust Implementation (`lapin` + `tokio`)

### Project Setup
```toml
[dependencies]
tokio = { version = "1", features = ["full"] }
lapin = "2.1"
serde = { version = "1.0", serde_derive = "1.0" }
serde_json = "1.0"
futures-lite = "1.12"
```

### Code
```rust
use lapin::{
    options::*, types::FieldTable, Connection, ConnectionProperties,
};
use serde::Deserialize;
use futures_lite::stream::StreamExt;
use std::sync::Arc;
use tokio::sync::Mutex;

#[derive(Deserialize, Debug)]
struct OrderCreated {
    order_id: String,
    customer_name: String,
}

#[tokio::main]
async fn main() -> Result<(), lapin::Error> {
    let addr = std::env::var("AMQP_ADDR").unwrap_or_else(|_| "amqp://127.0.0.1:5672/%2f".into());

    let conn = Connection::connect(&addr, ConnectionProperties::default()).await?;
    let channel = conn.create_channel().await?;

    // WHY: Declare queue implicitly via Rust strongly-typed enums and options.
    channel.queue_declare(
        "order_events_queue",
        QueueDeclareOptions {
            durable: true,
            ..Default::default()
        },
        FieldTable::default(),
    ).await?;

    // Set prefetch count for back-pressure
    channel.basic_qos(10, BasicQosOptions::default()).await?;

    let mut consumer = channel
        .basic_consume(
            "order_events_queue",
            "my_consumer",
            BasicConsumeOptions::default(),
            FieldTable::default(),
        )
        .await?;

    println!("Waiting for messages...");

    // WHY: Use StreamExt to iterate asynchronously over the incoming RabbitMQ messages.
    while let Some(delivery) = consumer.next().await {
        if let Ok(delivery) = delivery {
            // WHY: Tokio spawn allows concurrent processing of each message without blocking the stream loop.
            tokio::spawn(async move {
                process_message(delivery).await;
            });
        }
    }

    Ok(())
}

async fn process_message(delivery: lapin::message::Delivery) {
    let payload = match std::str::from_utf8(&delivery.data) {
        Ok(s) => s,
        Err(_) => {
            // Unrecoverable, Nack without requeue
            let _ = delivery.nack(BasicNackOptions { multiple: false, requeue: false }).await;
            return;
        }
    };

    let event: OrderCreated = match serde_json::from_str(payload) {
        Ok(e) => e,
        Err(_) => {
            let _ = delivery.nack(BasicNackOptions { multiple: false, requeue: false }).await;
            return;
        }
    };

    if is_already_processed(&event.order_id).await {
        println!("Ignoring duplicate order: {}", event.order_id);
        let _ = delivery.ack(BasicAckOptions::default()).await;
        return;
    }

    println!("Processing Payment for Order: {}", event.order_id);

    match process_payment_logic().await {
        Ok(_) => {
            // WHY: Explicit async ack via await.
            let _ = delivery.ack(BasicAckOptions::default()).await;
        }
        Err(_) => {
            // Requeue transient errors
            let _ = delivery.nack(BasicNackOptions { multiple: false, requeue: true }).await;
        }
    }
}

async fn is_already_processed(_id: &str) -> bool { false }
async fn process_payment_logic() -> Result<(), String> { Ok(()) }
```

## Critical Observations for C# Developers
1. **Framework vs Library**: MassTransit hides the channel, the prefetch count, the loop, and the deserialization logic. Go and Rust operate closer to the protocol layer, giving you absolute control over the consumption pipeline.
2. **Explicit Acks**: In C#, a successfully completed task implies an Ack. In Go and Rust, failing to explicitly call `d.Ack(false)` will result in the message returning to the queue when the connection closes, creating zombie logic.
3. **Threading**: MassTransit uses the .NET ThreadPool automatically. Go manages concurrency natively with Goroutines. Rust requires explicit `tokio::spawn` to prevent the async while loop from blocking on a slow database query.
