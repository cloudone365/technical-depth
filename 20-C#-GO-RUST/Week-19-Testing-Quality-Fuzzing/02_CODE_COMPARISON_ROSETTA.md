# Week 19: Code Comparison Rosetta

## The Mission: Comprehensive OrderService Testing Suite
This Rosetta stone demonstrates a full-spectrum testing suite for an `OrderService`. It goes beyond simple unit tests and includes unit tests (with mocked repos), integration tests (with a real PostgreSQL Testcontainer), API tests (in-process server testing), and advanced fuzz/benchmark testing. 

### 1. C# (.NET 8)

The C# approach relies heavily on xUnit, NSubstitute for mocking, and Testcontainers for integration tests. `WebApplicationFactory` is used for in-process API testing.

**Project Setup (`OrderApp.Tests.csproj`):**
```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="xunit" Version="2.7.0" />
    <PackageReference Include="NSubstitute" Version="5.1.0" />
    <PackageReference Include="FluentAssertions" Version="6.12.0" />
    <PackageReference Include="Testcontainers.PostgreSql" Version="3.7.0" />
    <PackageReference Include="Microsoft.AspNetCore.Mvc.Testing" Version="8.0.2" />
  </ItemGroup>
</Project>
```

**Test Implementation (`OrderServiceTests.cs`):**
```csharp
using FluentAssertions;
using NSubstitute;
using Testcontainers.PostgreSql;
using Xunit;
using Microsoft.AspNetCore.Mvc.Testing;

// 1. UNIT TEST (Mocked Repo)
public class OrderServiceUnitTests 
{
    // NSubstitute generates a dynamic proxy at runtime for the interface.
    // If IOrderRepository was a sealed class, this would fail at runtime.
    private readonly IOrderRepository _repo = Substitute.For<IOrderRepository>();
    
    [Fact]
    public async Task ProcessOrder_WithValidItems_CalculatesTotal()
    {
        // Arrange
        var service = new OrderService(_repo);
        var order = new Order { Items = [new(Price: 10, Qty: 2)] };
        
        // Act
        var result = await service.ProcessAsync(order);
        
        // Assert
        result.Total.Should().Be(20); // FluentAssertions for readability
        await _repo.Received(1).SaveAsync(Arg.Any<Order>());
    }
}

// 2. INTEGRATION TEST (Real PostgreSQL)
// IAsyncLifetime is xUnit's way of handling async setup/teardown per test class instance.
public class OrderRepositoryIntegrationTests : IAsyncLifetime
{
    private readonly PostgreSqlContainer _dbContainer = new PostgreSqlBuilder()
        .WithImage("postgres:15-alpine")
        .Build();

    public async Task InitializeAsync() => await _dbContainer.StartAsync();
    public async Task DisposeAsync() => await _dbContainer.DisposeAsync();

    [Fact]
    public async Task CanSaveAndRetrieveOrder()
    {
        var repo = new SqlOrderRepository(_dbContainer.GetConnectionString());
        var order = new Order { Id = "ORD-123", Total = 50.0m };
        
        await repo.SaveAsync(order);
        var retrieved = await repo.GetAsync("ORD-123");
        
        retrieved.Should().NotBeNull();
        retrieved.Total.Should().Be(50.0m);
    }
}

// 3. API TEST (In-Process Server)
public class OrderApiTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly HttpClient _client;

    public OrderApiTests(WebApplicationFactory<Program> factory)
    {
        // Spins up the ASP.NET Core server in-memory for testing
        _client = factory.CreateClient();
    }

    [Fact]
    public async Task GetOrder_Returns200Ok()
    {
        var response = await _client.GetAsync("/api/orders/ORD-123");
        response.EnsureSuccessStatusCode();
    }
}
```
**To Run:** `dotnet test`

### 2. Go (1.21+)

Go embraces simplicity. There are no attributes. Fakes are preferred over mocks. Table-driven tests are the standard idiom. Fuzzing is built into the standard library.

**Project Setup (`go.mod`):**
```go
module orderapp

go 1.21

require (
    github.com/testcontainers/testcontainers-go v0.29.1
    github.com/testcontainers/testcontainers-go/modules/postgres v0.29.1
)
```

**Test Implementation (`order_service_test.go`):**
```go
package order_test // Use _test package for external black-box testing

import (
    "context"
    "net/http"
    "net/http/httptest"
    "testing"
    
    "github.com/testcontainers/testcontainers-go/modules/postgres"
    "orderapp/order"
)

// 1. UNIT TEST (Handwritten Fake)
// In Go, we prefer concrete fakes over reflection-based mock frameworks.
type fakeOrderRepo struct {
    savedOrder *order.Order
}
func (f *fakeOrderRepo) Save(o *order.Order) error {
    f.savedOrder = o
    return nil
}

func TestProcessOrder(t *testing.T) {
    // We enable parallel execution of this test
    t.Parallel()
    
    // Table-driven tests encapsulate inputs and expected outputs
    tests := []struct{
        name      string
        items     []order.Item
        wantTotal float64
    }{
        {"single item", []order.Item{{Price: 10, Qty: 1}}, 10},
        {"multiple items", []order.Item{{Price: 10, Qty: 2}, {Price: 5, Qty: 1}}, 25},
    }
    
    for _, tt := range tests {
        // We re-bind the loop variable to prevent closure bugs (prior to Go 1.22)
        tt := tt
        t.Run(tt.name, func(t *testing.T) {
            t.Parallel() // Sub-tests can also run in parallel
            repo := &fakeOrderRepo{}
            service := order.NewOrderService(repo)
            
            o := &order.Order{Items: tt.items}
            service.Process(o)
            
            if o.Total != tt.wantTotal {
                t.Errorf("got %v, want %v", o.Total, tt.wantTotal)
            }
        })
    }
}

// 2. INTEGRATION TEST (Real PostgreSQL)
func TestRepositoryIntegration(t *testing.T) {
    ctx := context.Background()
    // Spin up container inline
    dbContainer, err := postgres.RunContainer(ctx, postgres.WithImage("postgres:15-alpine"))
    if err != nil {
        t.Fatal(err)
    }
    // Defer teardown ensures cleanup happens even on test failure
    t.Cleanup(func() {
        if err := dbContainer.Terminate(ctx); err != nil {
            t.Fatalf("failed to terminate container: %s", err)
        }
    })

    connStr, _ := dbContainer.ConnectionString(ctx, "sslmode=disable")
    repo := order.NewSqlRepository(connStr)
    // Assertions omitted for brevity
    _ = repo
}

// 3. API TEST (In-Process Server)
func TestOrderAPI(t *testing.T) {
    req, _ := http.NewRequest(http.MethodGet, "/api/orders/ORD-123", nil)
    // httptest.NewRecorder captures the response without binding to a port
    rr := httptest.NewRecorder()
    
    handler := order.NewAPIHandler()
    handler.ServeHTTP(rr, req)
    
    if status := rr.Code; status != http.StatusOK {
        t.Errorf("handler returned wrong status code: got %v want %v", status, http.StatusOK)
    }
}

// 4. FUZZ TEST (Native)
func FuzzParseOrderID(f *testing.F) {
    // Add seed corpus for the fuzzer to start from
    f.Add("ORD-123")
    f.Add("ORD-999")
    
    f.Fuzz(func(t *testing.T, id string) {
        // Fuzzer will pass random garbage into 'id'. 
        // Goal: Ensure the parser returns an error, but NEVER panics.
        _, err := order.ParseOrderID(id)
        if err != nil {
            // Expected on garbage data, we just skip
            t.Skip()
        }
    })
}
```
**To Run:** 
* `go test ./...`
* `go test -fuzz=FuzzParseOrderID`

### 3. Rust

Rust tests often live right inside the source file under a `#[cfg(test)]` module, allowing access to private fields for unit tests. For integration tests, a separate `tests/` directory is used. Mocking requires procedural macros at compile time.

**Project Setup (`Cargo.toml`):**
```toml
[package]
name = "orderapp"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1", features = ["full"] }
axum = "0.7"

[dev-dependencies]
mockall = "0.12.1"
testcontainers = "0.15.0"
criterion = "0.5.1"

[[bench]]
name = "order_benchmark"
harness = false
```

**Test Implementation (`src/order.rs`):**
```rust
pub struct Order {
    pub id: String,
    pub items: Vec<Item>,
    pub total: f64,
}

pub struct Item {
    pub price: f64,
    pub qty: u32,
}

// The trait we want to mock
pub trait OrderRepository {
    fn save(&self, order: &Order) -> Result<(), String>;
}

// Production code goes here...

// --- TEST MODULE ---
// The #[cfg(test)] attribute tells the compiler to ONLY compile this 
// when running 'cargo test'. It won't be in the production binary.
#[cfg(test)]
mod tests {
    use super::*; // Import parent module
    use mockall::predicate::*;
    use mockall::mock;

    // 1. UNIT TEST (Compile-time Mocking)
    // mockall uses a macro to generate a MockOrderRepo struct at compile time.
    mock! {
        pub OrderRepo {}
        impl OrderRepository for OrderRepo {
            fn save(&self, order: &Order) -> Result<(), String>;
        }
    }

    #[test]
    fn process_order_calculates_total() {
        // Arrange
        let mut mock_repo = MockOrderRepo::new();
        mock_repo.expect_save()
            .times(1) // Expect exactly one call
            .returning(|_| Ok(()));
            
        let service = OrderService::new(mock_repo);
        let mut order = Order { 
            id: "ORD-123".into(),
            items: vec![Item { price: 10.0, qty: 2 }], 
            total: 0.0 
        };
        
        // Act
        service.process(&mut order);
        
        // Assert - built-in macros
        assert_eq!(order.total, 20.0);
    }
}
```

**Integration Test (`tests/db_integration.rs`):**
```rust
use testcontainers::clients;
use testcontainers::images::postgres::Postgres;
use orderapp::SqlOrderRepository; // External crate API access only

// 2. INTEGRATION TEST
#[tokio::test]
async fn test_database_insert() {
    let docker = clients::Cli::default();
    let node = docker.run(Postgres::default());
    
    let port = node.get_host_port_ipv4(5432);
    let conn_str = format!("postgres://postgres:postgres@127.0.0.1:{}/postgres", port);
    
    let repo = SqlOrderRepository::new(&conn_str).await;
    // ... test logic ...
}
```

**Benchmark (`benches/order_benchmark.rs`):**
```rust
use criterion::{black_box, criterion_group, criterion_main, Criterion};
use orderapp::{Order, Item, OrderService};

// 5. CRITERION BENCHMARK
// Criterion does statistically significant micro-benchmarking.
fn bench_order_processing(c: &mut Criterion) {
    let mut order = Order { id: "1".into(), items: vec![Item{price:10.0, qty:2}], total: 0.0 };
    let service = OrderService::new_dummy();

    c.bench_function("process_order", |b| {
        b.iter(|| {
            // black_box prevents the compiler from optimizing away the call
            service.process(black_box(&mut order)); 
        })
    });
}

criterion_group!(benches, bench_order_processing);
criterion_main!(benches);
```
**To Run:** 
* `cargo test`
* `cargo bench`

### Critical Observations for C# Developers
1. **Mocking Mechanics:** C# does it at runtime via reflection proxies. Go does it manually via fakes. Rust does it at compile-time via macros (`mock!`). Rust's approach catches mock signature drift during compilation, whereas C# runtime mocks throw exceptions at test execution.
2. **Setup/Teardown:** C# uses class lifecycles (`IAsyncLifetime`). Go uses explicit inline setup and `defer`/`t.Cleanup`. Rust relies on standard RAII (Drop trait) for container teardown.
3. **Table-Driven Tests:** Go's use of slices of structs with `t.Run` eliminates the need for xUnit's `[Theory]` attributes, giving you standard language control structures (like loops) over your test execution.
4. **Fuzzing and Benchmarking:** Go has native fuzzing `go test -fuzz`. Rust has a standardized third-party ecosystem (`criterion` for benchmarking, `cargo fuzz` for fuzzing) heavily embedded in Cargo. In C#, these are usually niche tools (BenchmarkDotNet notwithstanding).
