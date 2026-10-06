# Why This Week Matters for Your Career Transition

By the end of this week, you will profoundly understand how modern backend frameworks process HTTP requests and compose middleware. As a senior .NET developer, you are intimately familiar with ASP.NET Core: the `IApplicationBuilder` pipeline, `RequestDelegate`, minimal APIs vs Controllers, and filter pipelines. It's a massive, feature-rich framework. When you move to Go and Rust, you will encounter a shift towards minimalism and composability based on fundamental language traits. Go uses interfaces (`http.Handler`) and high-order functions to build middleware chains organically without a heavy framework. Rust relies on the powerful `tower::Service` trait and Axum's type-safe extractors to enforce request validation at compile time. Understanding these paradigms will teach you how to write highly performant, type-safe APIs without relying on reflection or heavy dependency injection containers.

## The C# Baseline: ASP.NET Core Middleware and Minimal APIs

### The Pipeline Architecture
ASP.NET Core processes HTTP requests through a pipeline of middleware. Each middleware component is a function that receives an `HttpContext` and a `RequestDelegate` representing the `next` middleware in the chain.

```csharp
app.Use(async (context, next) =>
{
    // Do work before the next middleware
    await next.Invoke();
    // Do work after the next middleware
});
```
This is the classic Russian Doll model. If a middleware doesn't call `next.Invoke()`, the chain short-circuits.

### Minimal APIs vs Controllers
Historically, C# relied on heavily reflection-based Controllers. Modern .NET pushes Minimal APIs, which use delegate parameters bound directly from the request:

```csharp
app.MapGet("/users/{id}", async (int id, IUserService service) => { ... });
```
Behind the scenes, the framework still uses reflection (and source generators) to inspect the delegate signature, resolve `IUserService` from the DI container, and parse the `id` route parameter.

## The Go Approach: `net/http` and High-Order Functions

Go's standard library `net/http` is so robust that many production systems use it without any third-party framework, though routers like `chi` or `gorilla/mux` are common for path parameters.

### The `http.Handler` Interface
Everything in Go HTTP revolves around a single, simple interface:

```go
type Handler interface {
    ServeHTTP(ResponseWriter, *Request)
}
```

Because interfaces in Go are satisfied implicitly, any type with this method is an HTTP handler.

### Middleware as Function Wrappers
Go does not have an `IApplicationBuilder`. Middleware is simply a function that takes an `http.Handler` and returns a new `http.Handler` (usually using the `http.HandlerFunc` adapter to turn a standard function into a `Handler`).

```go
func LoggingMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        log.Println("Before request")
        next.ServeHTTP(w, r)
        log.Println("After request")
    })
}
```
You compose them by wrapping them: `LoggingMiddleware(AuthMiddleware(MyHandler))`.

### Server Configuration is Manual
In C#, Kestrel hides many connection details. In Go, failing to configure `http.Server` timeouts is a classic rookie mistake that leads to resource exhaustion.

```go
server := &http.Server{
    ReadTimeout:  5 * time.Second,  // Time to read the entire request
    WriteTimeout: 10 * time.Second, // Time to write the response
    IdleTimeout:  120 * time.Second, // Keep-alive timeout
}
```

## The Rust Reality: `tower::Service` and Axum

Rust approaches HTTP routing with extreme type safety. Axum (built by the Tokio team) is the industry standard. It sits on top of Hyper (the HTTP implementation) and Tower (the middleware abstraction).

### The `tower::Service` Trait
Tower defines a `Service` trait that represents any asynchronous request/response operation. It is conceptually identical to C#'s `RequestDelegate`, but fully type-safe.

```rust
pub trait Service<Request> {
    type Response;
    type Error;
    type Future: Future<Output = Result<Self::Response, Self::Error>>;
    
    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>>;
    fn call(&mut self, req: Request) -> Self::Future;
}
```

### Axum Extractors: Dependency Injection via Type Signatures
In ASP.NET Minimal APIs, you inject services via parameters. Axum does exactly this, but entirely at compile-time using the `FromRequest` trait. These are called **Extractors**.

```rust
async fn create_user(
    State(db): State<PgPool>, // Shared application state
    Path(id): Path<u32>,      // URL parameter
    Json(payload): Json<CreateUser>, // JSON body
) -> impl IntoResponse { ... }
```
If the JSON payload is malformed, Axum intercepts the request before it even reaches your handler and returns a 400 Bad Request. There is no reflection; the compiler generates the code to extract these types.

### Type-Safe Error Handling
In C#, unhandled exceptions result in a 500. In Axum, handlers must return types that implement `IntoResponse`. You often return `Result<T, AppError>`, where `AppError` is a custom enum implementing `IntoResponse`. This forces you to explicitly map every possible failure state to an HTTP status code at compile time.

## Common Misconceptions to Unlearn

*   **Misconception 1: You need a massive framework like ASP.NET to build a REST API in Go/Rust.**
    *   *Reality:* Go's `net/http` handles production traffic flawlessly. Rust's Axum provides routing and extraction without the bloat of a full MVC framework.
*   **Misconception 2: Dependency Injection requires an IoC Container.**
    *   *Reality:* C# relies heavily on `IServiceCollection`. In Go, dependencies are explicitly passed via struct fields (constructor injection). In Rust, Axum uses the `State<T>` extractor to pass a shared context (usually wrapped in an `Arc`) to handlers, entirely avoiding reflection-based containers.
*   **Misconception 3: Middleware modifies a shared Request context object.**
    *   *Reality:* In C#, you mutate `HttpContext.Items`. In Go, you create a *new* Context via `context.WithValue` and pass a clone of the `http.Request`. In Rust, you attach type-safe Extensions to the request.

## Summary Comparison

| Feature | C# (ASP.NET Core) | Go (net/http + chi) | Rust (Axum) |
| :--- | :--- | :--- | :--- |
| **Pipeline Abstraction** | `RequestDelegate` / `IApplicationBuilder` | Nested `http.Handler` | `tower::Service` / `ServiceBuilder` |
| **Request Data Binding** | Reflection & Source Generators | Manual parsing / JSON Unmarshal | Compile-time Extractors (`FromRequest`) |
| **Dependency Injection** | Built-in IoC Container (`IServiceProvider`) | Manual Struct Composition | Shared `State<T>` Extractor |
| **Error Handling** | Exception Middleware / Problem Details | Manual Error Responses | `IntoResponse` Trait |
| **Middleware Context** | `HttpContext.Items` (Dictionary) | `context.WithValue` (Any) | Request Extensions (Type Map) |
