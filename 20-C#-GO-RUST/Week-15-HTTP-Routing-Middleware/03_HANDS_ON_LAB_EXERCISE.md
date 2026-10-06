# Hands-On Lab: Tracing and Timeout Middleware

## Objective
Build two essential production middlewares: a Tracing middleware that injects a Correlation ID into the request context, and a Timeout middleware that enforces a strict SLA on request duration, cancelling the context if it takes too long.

## Day 1-2: Context Values in Go

In Go, passing request-scoped data like a Request ID requires `context.WithValue`. 

### The Go Reference
```go
package main

import (
	"context"
	"fmt"
	"net/http"
	"time"
	"github.com/google/uuid"
)

type contextKey string
const reqIDKey contextKey = "requestID"

func RequestIDMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reqID := uuid.New().String()
		w.Header().Set("X-Request-ID", reqID)
		
		// Create a NEW request with a NEW context containing the ID
		ctx := context.WithValue(r.Context(), reqIDKey, reqID)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func TimeoutMiddleware(timeout time.Duration) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ctx, cancel := context.WithTimeout(r.Context(), timeout)
			defer cancel()
			
			// We cannot just pass the context; we need to race the timeout against the handler.
			// Go provides http.TimeoutHandler for this exact purpose!
			http.TimeoutHandler(next, timeout, "Request Timeout").ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		reqID := r.Context().Value(reqIDKey).(string)
		
		// Simulate slow work
		select {
		case <-time.After(2 * time.Second):
			fmt.Fprintf(w, "Hello! ID: %s", reqID)
		case <-r.Context().Done():
			// Context was cancelled by TimeoutHandler
			fmt.Println("Work aborted for ID:", reqID)
		}
	})

	handler := RequestIDMiddleware(TimeoutMiddleware(1 * time.Second)(mux))
	http.ListenAndServe(":8080", handler)
}
```

## Day 3-4: The Rust Challenge (Axum)

Implement the same middleware in Axum. Axum uses `Extension` to pass data between middleware and handlers.

### The Rust Skeleton
```rust
use axum::{
    extract::Extension,
    http::Request,
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::get,
    Router,
};
use std::time::Duration;
use tokio::time::timeout;

#[derive(Clone)]
struct RequestId(String);

// TODO 1: Implement Request ID Middleware
async fn request_id_middleware(mut request: Request<axum::body::Body>, next: Next) -> Response {
    let req_id = RequestId("abc-123".to_string()); // Generate UUID here
    
    // 1. Insert the RequestId into the request extensions so handlers can extract it
    
    // 2. Call next.run()
    
    // 3. Add the X-Request-ID header to the response
    unimplemented!()
}

// TODO 2: Implement Timeout Middleware
async fn timeout_middleware(request: Request<axum::body::Body>, next: Next) -> Result<Response, axum::http::StatusCode> {
    // 1. Use tokio::time::timeout to wrap the `next.run(request)` future.
    // 2. If it times out, return Err(StatusCode::REQUEST_TIMEOUT)
    // 3. If it succeeds, return Ok(response)
    unimplemented!()
}

async fn slow_handler(Extension(req_id): Extension<RequestId>) -> impl IntoResponse {
    tokio::time::sleep(Duration::from_secs(2)).await;
    format!("Hello! ID: {}", req_id.0)
}

#[tokio::main]
async fn main() {
    let app = Router::new()
        .route("/", get(slow_handler))
        .layer(middleware::from_fn(timeout_middleware))
        .layer(middleware::from_fn(request_id_middleware));

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080").await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
```

**Rust Concepts You Will Fight:**
1. **Request Extensions**: In Axum, request extensions are a type-map. You insert `RequestId` (your struct), and the handler extracts `Extension<RequestId>`. It matches strictly by type, which is why we created a specific `RequestId` tuple struct rather than just inserting a `String`.
2. **Future Racing**: Wrapping `next.run()` in `tokio::time::timeout` automatically cancels the inner future if the timer expires. You do not need to check cancellation tokens manually inside your handlers like you do in Go; dropping the future drops the task.

## Friday: Mob Review & Load Testing

### Load Testing Exercise
Run the API (in release mode for Rust/C#). Use `hey` or `vegeta` to send 500 requests per second.
```bash
hey -c 100 -q 5 -z 10s http://localhost:8080/
```

### Discussion Questions
*   **Q:** In the Rust implementation, we rely on dropping the future to cancel the work. What happens if the `slow_handler` was executing a blocking database call on a separate OS thread (`spawn_blocking`) when the timeout occurred?
    *   *Expected Answer:* The HTTP request would time out and return 503, but the blocking database call would *continue executing* in the background thread until completion because blocking threads cannot be preempted by dropping a future.
*   **Q:** Why did we define a custom type `type contextKey string` in Go instead of just using `"requestID"` as the key?
    *   *Expected Answer:* Go's `context.WithValue` uses any comparable type as a key. If multiple packages use a simple `string` key like `"id"`, they will overwrite each other's data. Defining an unexported custom type guarantees uniqueness.

## Sign-off Checklist
- [ ] We successfully created a RequestId type map extension in Axum.
- [ ] We successfully used `tokio::time::timeout` to wrap the middleware chain.
- [ ] We understand why Go uses unexported types for context keys.
- [ ] We ran a load test and verified the 503 (or 504) timeout responses.
