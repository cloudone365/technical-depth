# 02_CODE_COMPARISON_ROSETTA: Database Persistence & Transactions

This document demonstrates an order persistence layer managing atomic transactions (saving an order, line items, and an outbox event). It contrasts Dapper in C#, `database/sql` in Go, and SQLx in Rust.

## 1. C# Implementation (Dapper & Npgsql)

### Project Setup
```xml
<PackageReference Include="Dapper" Version="2.1.24" />
<PackageReference Include="Npgsql" Version="8.0.0" />
```

### Code
```csharp
using System;
using System.Collections.Generic;
using System.Data;
using System.Text.Json;
using System.Threading.Tasks;
using Dapper;
using Npgsql;

public record Order(Guid Id, string Customer, decimal Total, int Version);
public record LineItem(Guid Id, Guid OrderId, string Product, int Quantity);
public record OutboxEvent(Guid Id, string EventType, string Payload);

public class OrderRepository
{
    private readonly string _connectionString;

    public OrderRepository(string connectionString)
    {
        _connectionString = connectionString;
    }

    public async Task CreateOrderAtomicAsync(Order order, List<LineItem> items)
    {
        // WHY: ADO.NET explicit connection management. Npgsql handles pooling under the hood.
        await using var connection = new NpgsqlConnection(_connectionString);
        await connection.OpenAsync();

        // WHY: Begin explicit transaction. Must be passed to all Dapper calls.
        await using var transaction = await connection.BeginTransactionAsync();

        try
        {
            const string insertOrder = @"
                INSERT INTO orders (id, customer, total, version) 
                VALUES (@Id, @Customer, @Total, @Version)";
            
            await connection.ExecuteAsync(insertOrder, order, transaction);

            const string insertItems = @"
                INSERT INTO line_items (id, order_id, product, quantity) 
                VALUES (@Id, @OrderId, @Product, @Quantity)";
            
            await connection.ExecuteAsync(insertItems, items, transaction);

            var outboxEvent = new OutboxEvent(
                Guid.NewGuid(), 
                "OrderCreated", 
                JsonSerializer.Serialize(order)
            );
            
            const string insertOutbox = @"
                INSERT INTO outbox (id, event_type, payload) 
                VALUES (@Id, @EventType, @Payload)";
                
            await connection.ExecuteAsync(insertOutbox, outboxEvent, transaction);

            // WHY: Commit flushes the transaction. If it fails before this, automatic rollback on dispose.
            await transaction.CommitAsync();
        }
        catch
        {
            await transaction.RollbackAsync();
            throw;
        }
    }

    public async Task<bool> UpdateOrderStatusOptimisticAsync(Guid orderId, int currentVersion)
    {
        await using var connection = new NpgsqlConnection(_connectionString);
        
        // WHY: Optimistic concurrency using the version column to prevent lost updates.
        const string sql = @"
            UPDATE orders SET status = 'Shipped', version = version + 1 
            WHERE id = @Id AND version = @Version";
            
        var rowsAffected = await connection.ExecuteAsync(sql, new { Id = orderId, Version = currentVersion });
        return rowsAffected > 0;
    }
}
```

---

## 2. Go Implementation (`database/sql` + `pgx`)

### Code
```go
package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"github.com/google/uuid"
	_ "github.com/jackc/pgx/v5/stdlib" // pgx driver
)

type Order struct {
	ID       uuid.UUID
	Customer string
	Total    float64
	Version  int
}

type LineItem struct {
	ID       uuid.UUID
	OrderID  uuid.UUID
	Product  string
	Quantity int
}

type OrderRepository struct {
	db *sql.DB // WHY: db is the thread-safe connection pool, safe for concurrent use.
}

func NewOrderRepository(db *sql.DB) *OrderRepository {
	return &OrderRepository{db: db}
}

func (r *OrderRepository) CreateOrderAtomic(ctx context.Context, order Order, items []LineItem) error {
	// WHY: BeginTx binds a connection from the pool to this transaction.
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	// WHY: defer Rollback is safe. If committed, rollback does nothing.
	defer tx.Rollback()

	queryOrder := `INSERT INTO orders (id, customer, total, version) VALUES ($1, $2, $3, $4)`
	if _, err := tx.ExecContext(ctx, queryOrder, order.ID, order.Customer, order.Total, order.Version); err != nil {
		return fmt.Errorf("failed to insert order: %w", err)
	}

	// WHY: Prepared statement for batch inserts is efficient in Go.
	stmt, err := tx.PrepareContext(ctx, `INSERT INTO line_items (id, order_id, product, quantity) VALUES ($1, $2, $3, $4)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, item := range items {
		if _, err := stmt.ExecContext(ctx, item.ID, item.OrderID, item.Product, item.Quantity); err != nil {
			return fmt.Errorf("failed to insert item: %w", err)
		}
	}

	payload, _ := json.Marshal(order)
	queryOutbox := `INSERT INTO outbox (id, event_type, payload) VALUES ($1, $2, $3)`
	if _, err := tx.ExecContext(ctx, queryOutbox, uuid.New(), "OrderCreated", payload); err != nil {
		return err
	}

	// WHY: Explicit commit.
	return tx.Commit()
}

func (r *OrderRepository) UpdateOrderStatusOptimistic(ctx context.Context, orderID uuid.UUID, currentVersion int) (bool, error) {
	query := `UPDATE orders SET status = 'Shipped', version = version + 1 WHERE id = $1 AND version = $2`
	res, err := r.db.ExecContext(ctx, query, orderID, currentVersion)
	if err != nil {
		return false, err
	}
	
	rows, err := res.RowsAffected()
	return rows > 0, err
}
```

---

## 3. Rust Implementation (SQLx)

### Project Setup
```toml
[dependencies]
sqlx = { version = "0.7", features = ["runtime-tokio-native-tls", "postgres", "uuid", "json"] }
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
uuid = { version = "1.1", features = ["v4"] }
```

### Code
```rust
use sqlx::{PgPool, postgres::PgQueryResult};
use uuid::Uuid;
use serde::Serialize;

#[derive(Serialize)]
pub struct Order {
    pub id: Uuid,
    pub customer: String,
    pub total: f64,
    pub version: i32,
}

pub struct LineItem {
    pub id: Uuid,
    pub order_id: Uuid,
    pub product: String,
    pub quantity: i32,
}

pub struct OrderRepository {
    pool: PgPool, // WHY: The pool uses Arc internally, safe to clone and share.
}

impl OrderRepository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    pub async fn create_order_atomic(&self, order: &Order, items: &[LineItem]) -> Result<(), sqlx::Error> {
        // WHY: Begin a transaction. The transaction holds a mutable reference to the connection.
        let mut tx = self.pool.begin().await?;

        // WHY: query! macro validates this SQL against the DB schema at compile time!
        sqlx::query!(
            "INSERT INTO orders (id, customer, total, version) VALUES ($1, $2, $3, $4)",
            order.id, order.customer, order.total, order.version
        )
        .execute(&mut *tx)
        .await?;

        for item in items {
            sqlx::query!(
                "INSERT INTO line_items (id, order_id, product, quantity) VALUES ($1, $2, $3, $4)",
                item.id, item.order_id, item.product, item.quantity
            )
            .execute(&mut *tx)
            .await?;
        }

        let payload = serde_json::to_value(order).unwrap();
        let outbox_id = Uuid::new_v4();
        
        sqlx::query!(
            "INSERT INTO outbox (id, event_type, payload) VALUES ($1, $2, $3)",
            outbox_id, "OrderCreated", payload
        )
        .execute(&mut *tx)
        .await?;

        // WHY: tx.commit() consumes the transaction. If dropped early, RAII auto-rolls back.
        tx.commit().await?;
        Ok(())
    }

    pub async fn update_order_status_optimistic(&self, order_id: Uuid, current_version: i32) -> Result<bool, sqlx::Error> {
        let result: PgQueryResult = sqlx::query!(
            "UPDATE orders SET status = 'Shipped', version = version + 1 WHERE id = $1 AND version = $2",
            order_id, current_version
        )
        .execute(&self.pool)
        .await?;

        Ok(result.rows_affected() > 0)
    }
}
```

## Critical Observations for C# Developers
1. **Implicit vs Explicit Pools**: C# developers rarely interact with the pool directly. In Go (`sql.DB`) and Rust (`PgPool`), the pool object is a first-class citizen passed throughout the app.
2. **Compile-Time Safety**: EF Core gives you type safety through LINQ. Rust's `SQLx` gives you type safety through compile-time macro expansion against a live DB schema, combining the control of raw SQL with the safety of an ORM.
3. **Transaction Context**: In Dapper, you pass the `transaction` object explicitly to `.ExecuteAsync`. Go mimics this via `tx.ExecContext`. Rust enforces this explicitly via mutable borrowing `&mut *tx`, preventing concurrent queries on the same transaction by the compiler.
