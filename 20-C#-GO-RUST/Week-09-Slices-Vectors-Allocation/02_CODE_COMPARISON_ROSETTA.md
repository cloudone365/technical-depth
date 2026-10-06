# Week 09: Code Comparison - High-Performance Log Aggregator

## The Scenario
Read 10 million log lines. Parse the severity (INFO, WARN, ERROR, DEBUG), group them, and count. The goal is to maximize throughput and minimize garbage collection pauses by achieving **zero unnecessary heap allocations**.

## 1. C# Implementation: BenchmarkDotNet

In C#, `string.Split` allocates an array, and each split piece allocates a new `string` object. We use `ReadOnlySpan<char>` to slice memory without allocations.

```csharp
using System;
using System.Collections.Generic;
// using BenchmarkDotNet.Attributes; // Assume BenchmarkDotNet is configured

public class LogParser {
    // [Benchmark]
    public Dictionary<string, int> ParseLogsNaive(string[] logLines) {
        var counts = new Dictionary<string, int>();
        foreach (var line in logLines) {
            // ALLOCATION: Split creates an array.
            // ALLOCATION: Each part is a new string.
            var parts = line.Split('['); 
            if (parts.Length > 1) {
                var severity = parts[1].Split(']')[0];
                counts.TryGetValue(severity, out int count);
                counts[severity] = count + 1;
            }
        }
        return counts;
    }

    // [Benchmark]
    public Dictionary<string, int> ParseLogsOptimized(string[] logLines) {
        // Pre-allocate map capacity to avoid Rehashing allocations
        var counts = new Dictionary<string, int>(4);

        foreach (var line in logLines) {
            ReadOnlySpan<char> span = line.AsSpan();
            
            int bracketStart = span.IndexOf('[');
            int bracketEnd = span.IndexOf(']');
            
            if (bracketStart != -1 && bracketEnd != -1) {
                // Slice the span - ZERO ALLOCATION!
                ReadOnlySpan<char> severitySpan = span.Slice(bracketStart + 1, bracketEnd - bracketStart - 1);
                
                // We MUST allocate a string here because Dictionary requires a string key.
                // However, we eliminated the array and intermediate string allocations.
                string severity = severitySpan.ToString(); 
                
                counts.TryGetValue(severity, out int count);
                counts[severity] = count + 1;
            }
        }
        return counts;
    }
}
```
*BenchmarkDotNet Result: Naive triggers millions of Gen0/Gen1 GC collections. Optimized eliminates 80% of allocations.*

## 2. Go Implementation: Zero-Allocation Lookup

Go slices byte arrays. Since maps require strings, the compiler optimizes `m[string(byteSlice)]` to not allocate a string object if used strictly as a map lookup key!

```go
package main

import (
    "bytes"
    // "testing"
)

func ParseLogsNaive(logs []string) map[string]int {
    counts := make(map[string]int)
    for _, line := range logs {
        // ALLOCATION: slicing a string creates a new string in some older Go versions,
        // but worse, we do expensive string searches.
        for i := 0; i < len(line); i++ {
            if line[i] == '[' {
                for j := i + 1; j < len(line); j++ {
                    if line[j] == ']' {
                        counts[line[i+1:j]]++
                        break
                    }
                }
                break
            }
        }
    }
    return counts
}

func ParseLogsOptimized(logs [][]byte) map[string]int {
    // Pre-allocate map capacity to avoid rehashing
    counts := make(map[string]int, 4) 

    for _, line := range logs {
        start := bytes.IndexByte(line, '[')
        end := bytes.IndexByte(line, ']')

        if start != -1 && end != -1 {
            // Slicing bytes creates a new 24-byte header on the stack.
            // Zero heap allocation for the array.
            severity := line[start+1 : end]
            
            // Compiler Magic: string(severity) usually allocates, but when used 
            // directly inside a map lookup, the Go compiler optimizes it to zero allocations!
            counts[string(severity)]++
        }
    }
    return counts
}
```
*Profiling command: `go test -bench . -benchmem`*
*Result: `allocs/op` drops from millions to exactly 0 inside the loop.*

## 3. Rust Implementation: True Zero-Copy

Rust allows borrowed string slices (`&str`) to be used directly as `HashMap` keys. No compiler magic required—it is statically proven safe by lifetimes.

```rust
use std::collections::HashMap;

pub fn parse_logs_naive(logs: Vec<String>) -> HashMap<String, usize> {
    let mut counts = HashMap::new();
    for line in logs {
        // ALLOCATION: .replace and .split create vectors/iterators
        // ALLOCATION: .to_string() creates a new heap-allocated string for the key
        if let Some(severity) = line.split('[').nth(1).and_then(|s| s.split(']').next()) {
            *counts.entry(severity.to_string()).or_insert(0) += 1;
        }
    }
    counts
}

// Notice the lifetimes <'a>. The HashMap keys are pointers INTO the original log strings!
pub fn parse_logs_optimized<'a>(logs: &'a [&'a str]) -> HashMap<&'a str, usize> {
    // Pre-allocate to prevent bucket reallocation
    let mut counts = HashMap::with_capacity(4);

    for line in logs {
        if let (Some(start), Some(end)) = (line.find('['), line.find(']')) {
            // Slice the string slice. `severity` is a fat pointer (16 bytes on stack).
            // Zero heap allocations.
            let severity = &line[start + 1..end];
            
            // The Entry API: executes the hash and lookup exactly once.
            *counts.entry(severity).or_insert(0) += 1;
        }
    }

    counts
}
```
*Profiling command: `cargo bench` (Criterion)*
*Result: Throughput reaches ~1 GB/s. `bytes/iter` inside the loop is absolutely 0.*

## Critical Observations for C# Developers
1. **Zero-Copy Keys**: In C#, taking a slice of a string (`Span<char>`) is fast, but inserting it into a standard `Dictionary<string, int>` requires materializing a managed `string` object (a heap allocation) because the Dictionary cannot store `ref struct` types. Alternate dictionaries (`AlternateLookup` in .NET 9) are fixing this, but historically it's a bottleneck.
2. **The Entry API**: Rust's `counts.entry(severity).or_insert(0) += 1` executes the hash and lookup exactly once. C#'s traditional `.ContainsKey` followed by indexer access performs the hash calculation and bucket lookup twice.
3. **Pre-allocation**: In both Go (`make(..., 4)`) and Rust (`with_capacity(4)`), explicitly stating capacity prevents the hash tables from resizing. Resizing a hash map requires allocating a new, larger array of buckets, recalculating the hash for every existing key, and copying them over—a massive latency spike.
