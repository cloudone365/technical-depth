# Asynchronous HTTP Health Checker: A Rosetta Stone

This project demonstrates how to orchestrate a massive fan-out operation: checking the health of 100 HTTP endpoints concurrently, enforcing a strict timeout per request, and gathering the results.

## C# Implementation: `HttpClient` and `Task.WhenAll`

In C#, the standard pattern for high-concurrency fan-out involves mapping a collection of inputs to a collection of `Task<T>`, and then awaiting all of them simultaneously using `Task.WhenAll`.

### Project Setup
```xml
<!-- HealthChecker.csproj -->
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
```

### Code
```csharp
// Program.cs
using System.Diagnostics;

public class HealthResult
{
    public string Url { get; set; } = string.Empty;
    public bool IsHealthy { get; set; }
    public long ElapsedMs { get; set; }
    public string Error { get; set; } = string.Empty;
}

public class Program
{
    // IMPORTANT: HttpClient must be a singleton or created via IHttpClientFactory to avoid socket exhaustion.
    private static readonly HttpClient _httpClient = new HttpClient();

    public static async Task Main()
    {
        var urls = Enumerable.Range(1, 100).Select(i => $"https://httpbin.org/delay/{i % 3}").ToList();
        
        Console.WriteLine($"Starting checks for {urls.Count} URLs...");
        var sw = Stopwatch.StartNew();

        // 1. Create a CancellationTokenSource for the global 5-second timeout.
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));

        // 2. Map URLs to Tasks. These tasks are HOT and start executing immediately.
        var checkTasks = urls.Select(url => CheckHealthAsync(url, cts.Token));

        // 3. Await all tasks concurrently.
        var results = await Task.WhenAll(checkTasks);

        sw.Stop();
        
        var healthy = results.Count(r => r.IsHealthy);
        Console.WriteLine($"Completed in {sw.ElapsedMilliseconds}ms. Healthy: {healthy}/{urls.Count}");
    }

    private static async Task<HealthResult> CheckHealthAsync(string url, CancellationToken ct)
    {
        var sw = Stopwatch.StartNew();
        var result = new HealthResult { Url = url };

        try
        {
            // 4. Pass the token all the way down. ConfigureAwait(false) is best practice in libraries, 
            // though less critical in console apps.
            var response = await _httpClient.GetAsync(url, ct).ConfigureAwait(false);
            result.IsHealthy = response.IsSuccessStatusCode;
        }
        catch (OperationCanceledException)
        {
            result.Error = "Timeout";
            result.IsHealthy = false;
        }
        catch (Exception ex)
        {
            result.Error = ex.Message;
            result.IsHealthy = false;
        }
        finally
        {
            sw.Stop();
            result.ElapsedMs = sw.ElapsedMilliseconds;
        }

        return result;
    }
}
```
*Run command:* `dotnet run -c Release`

## Go Implementation: Goroutines, WaitGroup, and Channels

Go approaches this fundamentally differently. We spawn a discrete goroutine for every URL. The goroutines communicate their results back to the main thread via a thread-safe channel, while a `sync.WaitGroup` orchestrates the completion.

### Project Setup
```bash
go mod init healthchecker
```

### Code
```go
// main.go
package main

import (
	"context"
	"fmt"
	"net/http"
	"time"
	"sync"
)

type HealthResult struct {
	URL       string
	IsHealthy bool
	ElapsedMs int64
	Error     string
}

func main() {
	var urls []string
	for i := 1; i <= 100; i++ {
		urls = append(urls, fmt.Sprintf("https://httpbin.org/delay/%d", i%3))
	}

	fmt.Printf("Starting checks for %d URLs...\n", len(urls))
	start := time.Now()

	// 1. Create a context with a 5-second deadline. This replaces CancellationToken.
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel() // Always defer cancel to prevent context leaks.

	// 2. Setup WaitGroup and Channel
	var wg sync.WaitGroup
	resultsCh := make(chan HealthResult, len(urls)) // Buffered channel prevents goroutine leaks.

	// 3. HTTP Client configured with reasonable transport defaults (crucial in Go).
	client := &http.Client{
		Transport: &http.Transport{
			MaxIdleConnsPerHost: 100, // Important for high concurrency to the same host
		},
	}

	// 4. Fan-out
	for _, url := range urls {
		wg.Add(1)
		// Spawn a lightweight goroutine for each request.
		go func(u string) {
			defer wg.Done()
			resultsCh <- checkHealth(ctx, client, u)
		}(url)
	}

	// 5. Wait for all goroutines to finish in a separate goroutine, then close the channel.
	go func() {
		wg.Wait()
		close(resultsCh)
	}()

	// 6. Fan-in (Collect results)
	healthy := 0
	for result := range resultsCh {
		if result.IsHealthy {
			healthy++
		}
	}

	fmt.Printf("Completed in %dms. Healthy: %d/%d\n", time.Since(start).Milliseconds(), healthy, len(urls))
}

func checkHealth(ctx context.Context, client *http.Client, url string) HealthResult {
	start := time.Now()
	res := HealthResult{URL: url}

	// Create request with context attached.
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		res.Error = err.Error()
		return setElapsed(res, start)
	}

	resp, err := client.Do(req)
	if err != nil {
		res.Error = err.Error()
		return setElapsed(res, start)
	}
	defer resp.Body.Close() // ALWAYS close the body to release the connection.

	res.IsHealthy = resp.StatusCode >= 200 && resp.StatusCode < 300
	return setElapsed(res, start)
}

func setElapsed(res HealthResult, start time.Time) HealthResult {
	res.ElapsedMs = time.Since(start).Milliseconds()
	return res
}
```
*Run command:* `go run main.go`

## Rust Implementation: Tokio, Reqwest, and `JoinSet`

In Rust, we use the Tokio async runtime and the `reqwest` crate. To manage a large number of concurrent tasks, we use a `JoinSet`, which acts like a collection of spawned Tokio tasks that we can await collectively.

### Project Setup
```toml
# Cargo.toml
[package]
name = "healthchecker"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1.35", features = ["full"] }
reqwest = { version = "0.11", features = ["rustls-tls"] }
```

### Code
```rust
// src/main.rs
use std::time::{Duration, Instant};
use tokio::task::JoinSet;
use reqwest::Client;

#[derive(Debug)]
struct HealthResult {
    url: String,
    is_healthy: bool,
    elapsed_ms: u128,
    error: Option<String>,
}

#[tokio::main] // This macro sets up the Tokio runtime entry point.
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let urls: Vec<String> = (1..=100)
        .map(|i| format!("https://httpbin.org/delay/{}", i % 3))
        .collect();

    println!("Starting checks for {} URLs...", urls.len());
    let start_time = Instant::now();

    // 1. Create a single HTTP client instance to share connection pools.
    let client = Client::builder()
        .timeout(Duration::from_secs(5)) // Global timeout configuration at the client level.
        .build()?;

    // 2. JoinSet manages a collection of spawned async tasks.
    let mut join_set = JoinSet::new();

    for url in urls.clone() {
        let client_clone = client.clone(); // Arc internally, cheap to clone.
        
        // 3. Spawn tasks onto the Tokio executor.
        join_set.spawn(async move {
            check_health(client_clone, url).await
        });
    }

    let mut healthy = 0;
    
    // 4. Await tasks as they complete.
    while let Some(res) = join_set.join_next().await {
        // Handle potential panics in the spawned task (JoinError).
        match res {
            Ok(health_result) => {
                if health_result.is_healthy {
                    healthy += 1;
                }
            }
            Err(e) => eprintln!("Task failed to execute: {}", e),
        }
    }

    println!(
        "Completed in {}ms. Healthy: {}/{}",
        start_time.elapsed().as_millis(),
        healthy,
        urls.len()
    );

    Ok(())
}

async fn check_health(client: Client, url: String) -> HealthResult {
    let start = Instant::now();
    let mut result = HealthResult {
        url: url.clone(),
        is_healthy: false,
        elapsed_ms: 0,
        error: None,
    };

    match client.get(&url).send().await {
        Ok(response) => {
            result.is_healthy = response.status().is_success();
        }
        Err(err) => {
            result.error = Some(err.to_string());
        }
    }

    result.elapsed_ms = start.elapsed().as_millis();
    result
}
```
*Run command:* `cargo run --release`

## Critical Observations for C# Developers

1.  **Timeout Enforcement:** In C#, you pass a `CancellationToken` through every layer of the `HttpClient` call chain. In Go, you wrap the request context using `context.WithTimeout`. In Rust, `reqwest` prefers you to configure the timeout on the `Client::builder()` or on individual requests using `.timeout()`, keeping the function signatures cleaner.
2.  **Concurrency Primitives:** C#'s `Task.WhenAll` operates on hot tasks. Go requires manual synchronization using `sync.WaitGroup` and a channel to collect results safely across goroutine boundaries. Rust's `JoinSet` operates similarly to `Task.WhenAll`, but allows you to process results as they complete (`join_next().await`) without writing manual channel orchestration.
3.  **Memory Management:** The C# approach maps 100 urls to 100 `Task<HealthResult>` objects on the heap. Go allocates small initial stacks for 100 goroutines. Rust compiles the state machines and `tokio::spawn` allocates them in memory; however, Rust's lack of GC means cleanup is immediate and deterministic the moment `join_next` drops the result.
