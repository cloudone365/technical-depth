# Week 12: Rosetta Stone - Parallel Document Indexer

This week, we will build a parallel document indexer using the fan-out/fan-in CSP pattern.
- **Producer**: Reads a list of file paths and pushes them to a channel.
- **Workers (Fan-out)**: Multiple goroutines/threads read file paths, process the text, and output word counts to a results channel.
- **Reducer (Fan-in)**: A single routine reads the results channel and aggregates the total word counts.

## 1. C#: `System.Threading.Channels`

```csharp
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;

public class Indexer {
    public static async Task RunAsync(List<string> files, CancellationToken ct) {
        // Unbounded channels for simplicity, but bounded is better for memory safety
        var jobs = Channel.CreateUnbounded<string>();
        var results = Channel.CreateUnbounded<Dictionary<string, int>>();

        // Producer
        var producer = Task.Run(async () => {
            foreach (var file in files) {
                await jobs.Writer.WriteAsync(file, ct);
            }
            jobs.Writer.Complete();
        });

        // Workers (Fan-out)
        var workerCount = 4;
        var workers = new Task[workerCount];
        for (int i = 0; i < workerCount; i++) {
            workers[i] = Task.Run(async () => {
                await foreach (var file in jobs.Reader.ReadAllAsync(ct)) {
                    var counts = new Dictionary<string, int>();
                    // simulate processing...
                    counts["example"] = 1; 
                    await results.Writer.WriteAsync(counts, ct);
                }
            });
        }

        // Close results when all workers finish
        _ = Task.WhenAll(workers).ContinueWith(_ => results.Writer.Complete());

        // Reducer (Fan-in)
        var finalCounts = new Dictionary<string, int>();
        await foreach (var partial in results.Reader.ReadAllAsync(ct)) {
            foreach (var kvp in partial) {
                if (!finalCounts.ContainsKey(kvp.Key)) finalCounts[kvp.Key] = 0;
                finalCounts[kvp.Key] += kvp.Value;
            }
        }

        Console.WriteLine($"Done. 'example' count: {finalCounts.GetValueOrDefault("example")}");
    }
}
```

## 2. Go: Channels and `sync.WaitGroup`

```go
package main

import (
	"context"
	"fmt"
	"sync"
)

func main() {
	files := []string{"file1.txt", "file2.txt", "file3.txt"}
	
	jobs := make(chan string, 10)
	results := make(chan map[string]int, 10)
	
	// WHY: Context allows graceful cancellation of the pipeline.
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Producer
	go func() {
		defer close(jobs) // closing jobs signals workers to stop
		for _, file := range files {
			select {
			case jobs <- file:
			case <-ctx.Done():
				return
			}
		}
	}()

	// Workers
	var wg sync.WaitGroup
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for file := range jobs {
				counts := make(map[string]int)
				counts["example"] = 1 // simulate work with file
				
				select {
				case results <- counts:
				case <-ctx.Done():
					return
				}
			}
		}()
	}

	// Wait and Close results
	go func() {
		wg.Wait()
		close(results) // signals reducer to finish
	}()

	// Reducer
	finalCounts := make(map[string]int)
	// range over channel blocks until closed and empty
	for partial := range results { 
		for k, v := range partial {
			finalCounts[k] += v
		}
	}

	fmt.Printf("Done. 'example' count: %d\n", finalCounts["example"])
}
```

## 3. Rust: `crossbeam-channel`

We use `crossbeam_channel` which performs excellently in multithreaded workflows.

```rust
use crossbeam_channel::{bounded, Receiver, Sender};
use std::collections::HashMap;
use std::thread;

fn main() {
    let files = vec!["file1.txt", "file2.txt", "file3.txt"];

    // WHY: Bounded channels apply backpressure. 
    let (job_tx, job_rx) = bounded::<&str>(10);
    let (res_tx, res_rx) = bounded::<HashMap<String, i32>>(10);

    // Producer
    thread::spawn(move || {
        for file in files {
            // Send can fail if the receiver is dropped
            if job_tx.send(file).is_err() { break; } 
        }
        // job_tx is dropped here, closing the channel
    });

    // Workers
    let mut worker_handles = vec![];
    for _ in 0..4 {
        // WHY: Receiver implements Clone, allowing multiple consumers (MPMC).
        let job_rx = job_rx.clone();
        let res_tx = res_tx.clone();
        
        let handle = thread::spawn(move || {
            // iter() blocks until the channel is closed and empty
            for _file in job_rx.iter() {
                let mut counts = HashMap::new();
                counts.insert("example".to_string(), 1); // simulate
                if res_tx.send(counts).is_err() { break; }
            }
        });
        worker_handles.push(handle);
    }
    // Drop the original res_tx so the channel actually closes when workers drop theirs.
    drop(res_tx);

    // Reducer (Main Thread)
    let mut final_counts = HashMap::new();
    for partial in res_rx.iter() {
        for (k, v) in partial {
            *final_counts.entry(k).or_insert(0) += v;
        }
    }

    for handle in worker_handles {
        handle.join().unwrap();
    }

    println!("Done. 'example' count: {:?}", final_counts.get("example"));
}
```

### Critical Observations for C# Developers
1. **Closing Channels**: In C#, you explicitly call `.Complete()`. In Go, you use the `close(ch)` builtin. In Rust, channels close automatically when all `Sender`s are dropped. Notice how we explicitly called `drop(res_tx)` in Rust before the reducer loop.
2. **Select vs Thread Blocking**: C# relies on `await` to free up thread pool threads. Go uses `select` to handle multiplexing, handled efficiently by the Go scheduler. Rust (in this sync example) uses actual OS thread blocking, but `crossbeam` makes this extremely fast. (In an async Rust app, you'd use Tokio channels and `tokio::select!`).
3. **Pipeline Ownership**: In Rust, the worker threads completely take ownership of the `HashMap` generated, moving it into the `res_tx` channel. The main thread then takes ownership from `res_rx`. No `Arc` or `Mutex` is needed.
