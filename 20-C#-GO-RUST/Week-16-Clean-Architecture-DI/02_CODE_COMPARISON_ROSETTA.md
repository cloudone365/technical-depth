# 02_CODE_COMPARISON_ROSETTA: Clean Architecture & DI

This document demonstrates a complete order service using ports-and-adapters (hexagonal) architecture across C#, Go, and Rust. It showcases how to implement an `OrderRepository` interface/trait, an `OrderService` with business logic, an `EventPublisher`, and how to wire them together.

## 1. C# Implementation (.NET 8)

### Project Setup (`OrderService.csproj`)
```xml
<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.Extensions.DependencyInjection" Version="8.0.0" />
  </ItemGroup>
</Project>
```

### Code (`Program.cs` and Domain)
```csharp
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using System;
using System.Threading.Tasks;

// 1. Domain Entities & Interfaces (Ports)
public record Order(Guid Id, string CustomerName, decimal TotalAmount);

public interface IOrderRepository
{
    Task SaveAsync(Order order);
    Task<Order?> GetByIdAsync(Guid id);
}

public interface IEventPublisher
{
    Task PublishAsync(string topic, object @event);
}

// 2. Application Service (Use Cases)
public class OrderService
{
    private readonly IOrderRepository _repository;
    private readonly IEventPublisher _publisher;

    // WHY: Standard C# constructor injection. The IoC container will resolve these at runtime.
    public OrderService(IOrderRepository repository, IEventPublisher publisher)
    {
        _repository = repository;
        _publisher = publisher;
    }

    public async Task<Order> CreateOrderAsync(string customerName, decimal amount)
    {
        var order = new Order(Guid.NewGuid(), customerName, amount);
        await _repository.SaveAsync(order);
        
        // WHY: Business logic triggers domain event publishing.
        await _publisher.PublishAsync("order.created", new { order.Id, order.CustomerName });
        
        return order;
    }
}

// 3. Infrastructure Adapters
public class InMemoryOrderRepository : IOrderRepository
{
    private readonly System.Collections.Concurrent.ConcurrentDictionary<Guid, Order> _store = new();

    public Task SaveAsync(Order order)
    {
        _store[order.Id] = order;
        return Task.CompletedTask;
    }

    public Task<Order?> GetByIdAsync(Guid id) => 
        Task.FromResult(_store.TryGetValue(id, out var order) ? order : null);
}

public class ConsoleEventPublisher : IEventPublisher
{
    public Task PublishAsync(string topic, object @event)
    {
        Console.WriteLine($"[EventPublished] {topic}: {@event}");
        return Task.CompletedTask;
    }
}

// 4. Wiring (Composition Root)
var builder = WebApplication.CreateBuilder(args);

// WHY: Registration of services into the ServiceCollection. Magic happens here.
// Scoped is typical for repositories tying to HTTP requests, Singleton for in-memory stores.
builder.Services.AddSingleton<IOrderRepository, InMemoryOrderRepository>();
builder.Services.AddSingleton<IEventPublisher, ConsoleEventPublisher>();
builder.Services.AddScoped<OrderService>();

var app = builder.Build();

app.MapPost("/orders", async (OrderRequest req, OrderService service) => 
{
    var order = await service.CreateOrderAsync(req.CustomerName, req.Amount);
    return Results.Created($"/orders/{order.Id}", order);
});

app.Run();

public record OrderRequest(string CustomerName, decimal Amount);
```

---

## 2. Go Implementation

### Project Setup (`go.mod`)
```go
module order-service
go 1.21
```

### Code (`main.go`)
```go
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"sync"
)

// 1. Domain Entities & Interfaces (Ports)
// WHY: In Go, interfaces are satisfied implicitly. Packages define the interfaces they consume.
type Order struct {
	ID           string  `json:"id"`
	CustomerName string  `json:"customer_name"`
	TotalAmount  float64 `json:"total_amount"`
}

type OrderRepository interface {
	Save(order *Order) error
	GetByID(id string) (*Order, error)
}

type EventPublisher interface {
	Publish(topic string, event interface{}) error
}

// 2. Application Service
type OrderService struct {
	repo      OrderRepository
	publisher EventPublisher
}

// WHY: Explicit constructor function. No DI framework. Returns a pointer to the struct.
func NewOrderService(repo OrderRepository, pub EventPublisher) *OrderService {
	return &OrderService{
		repo:      repo,
		publisher: pub,
	}
}

func (s *OrderService) CreateOrder(customerName string, amount float64) (*Order, error) {
	order := &Order{
		ID:           "uuid-1234", // Simplified for example
		CustomerName: customerName,
		TotalAmount:  amount,
	}

	if err := s.repo.Save(order); err != nil {
		return nil, err
	}

	// WHY: Event publishing explicitly invoked.
	err := s.publisher.Publish("order.created", map[string]string{"id": order.ID})
	if err != nil {
		// Log but don't fail order creation (or handle outbox pattern)
		log.Printf("failed to publish event: %v", err)
	}

	return order, nil
}

// 3. Infrastructure Adapters
type InMemoryOrderRepository struct {
	mu    sync.RWMutex
	store map[string]*Order
}

func NewInMemoryOrderRepository() *InMemoryOrderRepository {
	return &InMemoryOrderRepository{
		store: make(map[string]*Order),
	}
}

func (r *InMemoryOrderRepository) Save(order *Order) error {
	r.mu.Lock()
	defer r.mu.Unlock() // WHY: Defer ensures lock release. RAII alternative.
	r.store[order.ID] = order
	return nil
}

func (r *InMemoryOrderRepository) GetByID(id string) (*Order, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	order, ok := r.store[id]
	if !ok {
		return nil, fmt.Errorf("order not found")
	}
	return order, nil
}

type ConsoleEventPublisher struct{}

func NewConsoleEventPublisher() *ConsoleEventPublisher {
	return &ConsoleEventPublisher{}
}

func (p *ConsoleEventPublisher) Publish(topic string, event interface{}) error {
	fmt.Printf("[EventPublished] %s: %+v\n", topic, event)
	return nil
}

// 4. HTTP Handlers & Wiring
type OrderHandler struct {
	service *OrderService
}

func NewOrderHandler(svc *OrderService) *OrderHandler {
	return &OrderHandler{service: svc}
}

func (h *OrderHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req struct {
		CustomerName string  `json:"customer_name"`
		Amount       float64 `json:"amount"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	order, err := h.service.CreateOrder(req.CustomerName, req.Amount)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(order)
}

func main() {
	// WHY: Manual Dependency Wiring in main(). This is the Composition Root.
	// We clearly see exactly what implementations are used and how they are wired.
	repo := NewInMemoryOrderRepository()
	pub := NewConsoleEventPublisher()
	
	service := NewOrderService(repo, pub)
	handler := NewOrderHandler(service)

	http.Handle("/orders", handler)
	log.Println("Starting server on :8080")
	log.Fatal(http.ListenAndServe(":8080", nil))
}
```

---

## 3. Rust Implementation

### Project Setup (`Cargo.toml`)
```toml
[package]
name = "order-service"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1.0", features = ["full"] }
uuid = { version = "1.0", features = ["v4"] }
async-trait = "0.1"
```

### Code (`src/main.rs`)
```rust
use std::sync::Arc;
use tokio::sync::RwLock;
use std::collections::HashMap;
use uuid::Uuid;
use async_trait::async_trait;

// 1. Domain Entities & Interfaces (Ports)
#[derive(Clone, Debug)]
pub struct Order {
    pub id: Uuid,
    pub customer_name: String,
    pub total_amount: f64,
}

// WHY: async_trait macro is used because native async traits in Rust were stabilized only recently (Rust 1.75).
#[async_trait]
pub trait OrderRepository: Send + Sync {
    async fn save(&self, order: Order) -> Result<(), String>;
    async fn get_by_id(&self, id: Uuid) -> Result<Option<Order>, String>;
}

#[async_trait]
pub trait EventPublisher: Send + Sync {
    async fn publish(&self, topic: &str, event_payload: String) -> Result<(), String>;
}

// 2. Application Service
// WHY: We use generics bounded by traits to achieve zero-cost abstractions (static dispatch).
// Alternatively, we could use Arc<dyn OrderRepository> for dynamic dispatch.
pub struct OrderService<R, P>
where
    R: OrderRepository,
    P: EventPublisher,
{
    repo: Arc<R>,
    publisher: Arc<P>,
}

impl<R, P> OrderService<R, P>
where
    R: OrderRepository,
    P: EventPublisher,
{
    // WHY: Constructor function using the generic types.
    pub fn new(repo: Arc<R>, publisher: Arc<P>) -> Self {
        Self { repo, publisher }
    }

    pub async fn create_order(&self, customer_name: String, amount: f64) -> Result<Order, String> {
        let order = Order {
            id: Uuid::new_v4(),
            customer_name,
            total_amount: amount,
        };

        // Pass by clone or reference depending on repository implementation.
        self.repo.save(order.clone()).await?;

        let event_payload = format!("{{\"id\": \"{}\"}}", order.id);
        self.publisher.publish("order.created", event_payload).await?;

        Ok(order)
    }
}

// 3. Infrastructure Adapters
pub struct InMemoryOrderRepository {
    // WHY: RwLock wrapped in an Arc is required for thread-safe interior mutability in async context.
    store: RwLock<HashMap<Uuid, Order>>,
}

impl InMemoryOrderRepository {
    pub fn new() -> Self {
        Self {
            store: RwLock::new(HashMap::new()),
        }
    }
}

#[async_trait]
impl OrderRepository for InMemoryOrderRepository {
    async fn save(&self, order: Order) -> Result<(), String> {
        let mut store = self.store.write().await;
        store.insert(order.id, order);
        Ok(())
    }

    async fn get_by_id(&self, id: Uuid) -> Result<Option<Order>, String> {
        let store = self.store.read().await;
        Ok(store.get(&id).cloned())
    }
}

pub struct ConsoleEventPublisher;

#[async_trait]
impl EventPublisher for ConsoleEventPublisher {
    async fn publish(&self, topic: &str, event_payload: String) -> Result<(), String> {
        println!("[EventPublished] {}: {}", topic, event_payload);
        Ok(())
    }
}

// 4. Wiring (Composition Root)
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // WHY: Explicit instantiation and Arc wrapping for shared ownership.
    let repo = Arc::new(InMemoryOrderRepository::new());
    let publisher = Arc::new(ConsoleEventPublisher);

    // OrderService takes ownership of the Arcs. The compiler ensures the types match R and P.
    let service = OrderService::new(repo, publisher);

    let order = service.create_order("Alice".to_string(), 150.00).await?;
    println!("Created Order: {:?}", order);

    Ok(())
}
```

## Critical Observations for C# Developers
1. **Implicit vs Explicit Wiring**: C# heavily abstracts object creation via `IServiceCollection`. Go and Rust force you to manually wire objects (`main.go` / `main.rs`), resulting in clearer dependency graphs at the cost of slightly more boilerplate.
2. **Polymorphism**: Go achieves polymorphism implicitly (interfaces don't need `implements`), while Rust uses explicit traits. Rust developers must choose between dynamic dispatch (`Arc<dyn Trait>`) or static dispatch (generics `<R: Trait>`), whereas C# always uses dynamic dispatch via interfaces.
3. **Thread Safety and Mutability**: In C#, thread safety is often managed by the runtime or container (e.g., scoping). In Rust, thread safety is enforced at compile-time via `Send + Sync` bounds and explicitly controlled mutability via `RwLock`.
