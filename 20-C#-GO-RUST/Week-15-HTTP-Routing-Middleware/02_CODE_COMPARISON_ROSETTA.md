# REST API Routing and Middleware: A Rosetta Stone

This project demonstrates a standard REST API with request logging, authentication middleware, rate limiting (conceptual), and structured error responses.

## C# Implementation: ASP.NET Core Minimal APIs

### Code
```csharp
// Program.cs
using Microsoft.AspNetCore.Mvc;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

// Middleware 1: Request Logging
app.Use(async (context, next) =>
{
    var requestId = Guid.NewGuid().ToString();
    context.Response.Headers.Append("X-Request-ID", requestId);
    Console.WriteLine($"[{requestId}] {context.Request.Method} {context.Request.Path} started.");
    
    await next.Invoke();
    
    Console.WriteLine($"[{requestId}] completed with {context.Response.StatusCode}.");
});

// Middleware 2: Authentication
app.Use(async (context, next) =>
{
    if (!context.Request.Headers.TryGetValue("Authorization", out var authHeader) || 
        authHeader != "Bearer secret-token")
    {
        context.Response.StatusCode = 401;
        await context.Response.WriteAsJsonAsync(new ProblemDetails 
        { 
            Title = "Unauthorized", 
            Status = 401 
        });
        return;
    }
    await next.Invoke();
});

// Routes
app.MapGet("/orders/{id}", (int id) => 
{
    return Results.Ok(new { Id = id, Status = "Pending" });
});

app.MapPost("/orders", ([FromBody] CreateOrderRequest req) => 
{
    if (string.IsNullOrEmpty(req.Item)) return Results.BadRequest("Item is required");
    return Results.Created($"/orders/123", new { Id = 123, req.Item });
});

app.Run();

public record CreateOrderRequest(string Item);
```

## Go Implementation: `net/http` and Middleware Chains

### Code
```go
// main.go
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
)

// Middleware 1: Request Logging
func RequestIDLogging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reqID := uuid.New().String()
		w.Header().Set("X-Request-ID", reqID)
		
		log.Printf("[%s] %s %s started", reqID, r.Method, r.URL.Path)
		start := time.Now()
		
		next.ServeHTTP(w, r)
		
		log.Printf("[%s] completed in %v", reqID, time.Since(start))
	})
}

// Middleware 2: Authentication
func AuthMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authHeader := r.Header.Get("Authorization")
		if !strings.HasPrefix(authHeader, "Bearer secret-token") {
			w.Header().Set("Content-Type", "application/problem+json")
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]any{"title": "Unauthorized", "status": 401})
			return
		}
		next.ServeHTTP(w, r)
	})
}

type CreateOrderRequest struct {
	Item string `json:"item"`
}

func main() {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /orders/{id}", func(w http.ResponseWriter, r *http.Request) {
		id := r.PathValue("id") // Go 1.22+ routing features
		json.NewEncoder(w).Encode(map[string]any{"id": id, "status": "Pending"})
	})

	mux.HandleFunc("POST /orders", func(w http.ResponseWriter, r *http.Request) {
		var req CreateOrderRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Item == "" {
			http.Error(w, "Bad Request", http.StatusBadRequest)
			return
		}
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(map[string]any{"id": 123, "item": req.Item})
	})

	// Chain middleware
	handler := RequestIDLogging(AuthMiddleware(mux))

	server := &http.Server{
		Addr:         ":8080",
		Handler:      handler,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 10 * time.Second,
	}
	log.Fatal(server.ListenAndServe())
}
```

## Rust Implementation: Axum and Tower

### Code
```rust
// src/main.rs
use axum::{
    extract::Path,
    http::{Request, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use tower_http::trace::TraceLayer;
use uuid::Uuid;

#[tokio::main]
async fn main() {
    // Build our application with routes and middleware
    let app = Router::new()
        .route("/orders/:id", get(get_order))
        .route("/orders", post(create_order))
        .layer(middleware::from_fn(auth_middleware))
        .layer(middleware::from_fn(request_id_middleware));
        // Note: Layers execute bottom-to-top for requests, top-to-bottom for responses.

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080").await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

// Middleware 1: Request ID (Axum allows mutating the response easily)
async fn request_id_middleware(request: Request<axum::body::Body>, next: Next) -> Response {
    let request_id = Uuid::new_v4().to_string();
    println!("[{}] {} {}", request_id, request.method(), request.uri().path());
    
    let mut response = next.run(request).await;
    
    response.headers_mut().insert(
        "X-Request-ID",
        request_id.parse().unwrap(),
    );
    response
}

// Middleware 2: Authentication
async fn auth_middleware(request: Request<axum::body::Body>, next: Next) -> Result<Response, StatusCode> {
    let auth_header = request.headers().get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok());

    match auth_header {
        Some("Bearer secret-token") => Ok(next.run(request).await),
        _ => Err(StatusCode::UNAUTHORIZED),
    }
}

// Handlers
#[derive(Serialize)]
struct OrderResponse {
    id: String,
    status: String,
}

async fn get_order(Path(id): Path<String>) -> Json<OrderResponse> {
    Json(OrderResponse {
        id,
        status: "Pending".to_string(),
    })
}

#[derive(Deserialize)]
struct CreateOrderRequest {
    item: String,
}

async fn create_order(Json(payload): Json<CreateOrderRequest>) -> impl IntoResponse {
    (StatusCode::CREATED, Json(payload))
}
```

## Critical Observations for C# Developers

1.  **Middleware Execution Order:** In C#, you register middleware sequentially. In Go, you wrap functions `A(B(C(Handler)))`. In Rust (Axum/Tower), `layer` is applied like an onion. Understanding the exact order of request parsing vs response modification is critical.
2.  **Request Body Deserialization:** C# Minimal APIs handle body deserialization implicitly via parameters. In Go, you manually create a decoder and parse the body (`json.NewDecoder(r.Body)`). In Rust, Axum uses the `Json<T>` extractor which performs the exact same function as C# but with strict compile-time guarantees—if the body is missing or malformed, the handler is never invoked.
3.  **Header Manipulation:** C#'s `HttpContext.Response.Headers.Append` is very straightforward. In Go, you mutate the `ResponseWriter` header map before writing the status code. In Rust, you await the `next.run()` future to get the `Response` struct, then mutate its headers before returning it up the chain.
