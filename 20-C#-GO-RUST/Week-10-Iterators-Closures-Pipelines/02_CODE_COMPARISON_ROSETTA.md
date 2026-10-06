# Week 10: Rosetta Stone - Data Processing Pipelines

This week, we will build a data processing pipeline that reads financial transactions from a CSV, filters them by date and amount, groups them by merchant, and calculates basic statistics. 

## 1. C#: LINQ Pipeline

In C#, we rely on declarative LINQ pipelines. We use `File.ReadLines` to ensure we don't load the whole file into memory at once.

```csharp
using System;
using System.IO;
using System.Linq;
using System.Globalization;

public class Transaction {
    public string Merchant { get; set; }
    public decimal Amount { get; set; }
    public DateTime Date { get; set; }
}

public class Program {
    public static void Main() {
        var results = File.ReadLines("transactions.csv")
            .Skip(1) // Skip header
            .Select(line => {
                var parts = line.Split(',');
                return new Transaction {
                    Date = DateTime.Parse(parts[0], CultureInfo.InvariantCulture),
                    Merchant = parts[1],
                    Amount = decimal.Parse(parts[2], CultureInfo.InvariantCulture)
                };
            })
            // WHY: LINQ is lazy, this filter is applied as we iterate.
            .Where(t => t.Amount > 10.00m && t.Date.Year == 2023)
            // WHY: GroupBy is an eager operation! It must consume all previous elements 
            // before it can yield the first group.
            .GroupBy(t => t.Merchant)
            .Select(g => new {
                Merchant = g.Key,
                Total = g.Sum(t => t.Amount),
                Avg = g.Average(t => t.Amount),
                Count = g.Count()
            })
            .OrderByDescending(r => r.Total)
            .ToList(); // Consumes the pipeline

        foreach(var r in results) {
            Console.WriteLine($"{r.Merchant}: {r.Total} ({r.Count} txns)");
        }
    }
}
```

## 2. Go: Eager Imperative Loops

Go prefers explicit iteration. We manually open the file, read lines, parse, and aggregate into a map.

```go
package main

import (
	"encoding/csv"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

type Stats struct {
	Total float64
	Count int
}

func main() {
	file, err := os.Open("transactions.csv")
	if err != nil {
		panic(err)
	}
	defer file.Close()

	reader := csv.NewReader(file)
	records, err := reader.ReadAll() // Eagerly loads all into memory
	if err != nil {
		panic(err)
	}

    // WHY: Go doesn't have a built-in GroupBy. We manually manage the map.
	aggregated := make(map[string]*Stats)

	for i, record := range records {
		if i == 0 {
			continue // Skip header
		}
		
		date, _ := time.Parse("2006-01-02", record[0])
		amount, _ := strconv.ParseFloat(record[2], 64)

        // WHY: Explicit filter block instead of a .Where() closure
		if amount > 10.00 && date.Year() == 2023 {
			merchant := record[1]
			if _, exists := aggregated[merchant]; !exists {
				aggregated[merchant] = &Stats{}
			}
			aggregated[merchant].Total += amount
			aggregated[merchant].Count++
		}
	}

    // WHY: Maps in Go are un-ordered. We must extract keys to a slice and sort.
	type Result struct {
		Merchant string
		Stats    *Stats
	}
	var results []Result
	for k, v := range aggregated {
		results = append(results, Result{Merchant: k, Stats: v})
	}

	sort.Slice(results, func(i, j int) bool {
		return results[i].Stats.Total > results[j].Stats.Total
	})

	for _, r := range results {
		fmt.Printf("%s: %.2f (%d txns)\n", r.Merchant, r.Stats.Total, r.Stats.Count)
	}
}
```

## 3. Rust: Zero-Cost Iterator Chains

Rust combines the declarative elegance of C# with the explicit memory and performance characteristics of C.

```rust
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::collections::HashMap;

#[derive(Debug)]
struct Stats {
    total: f64,
    count: usize,
}

fn main() {
    let file = File::open("transactions.csv").expect("Failed to open file");
    let reader = BufReader::new(file);

    // WHY: iterators are lazy. Nothing happens until fold() consumes them.
    let mut aggregated = reader
        .lines()
        .skip(1) // skip header
        .filter_map(|line| line.ok()) // filter out I/O errors safely
        .filter_map(|line| {
            let parts: Vec<&str> = line.split(',').collect();
            if parts.len() != 3 { return None; }
            
            let amount: f64 = parts[2].parse().ok()?;
            // Simple string prefix check for year to avoid full date parsing overhead
            if amount > 10.00 && parts[0].starts_with("2023") {
                Some((parts[1].to_string(), amount))
            } else {
                None
            }
        })
        // WHY: fold is a consuming adapter. It takes an initial state (HashMap) 
        // and a closure to mutate that state.
        .fold(HashMap::new(), |mut acc, (merchant, amount)| {
            let entry = acc.entry(merchant).or_insert(Stats { total: 0.0, count: 0 });
            entry.total += amount;
            entry.count += 1;
            acc
        });

    // WHY: into_iter() consumes the HashMap, yielding owned key-value pairs.
    let mut results: Vec<_> = aggregated.into_iter().collect();
    
    // WHY: Sort by total descending. We use partial_cmp because f64 does not implement Ord.
    results.sort_by(|a, b| b.1.total.partial_cmp(&a.1.total).unwrap());

    for (merchant, stats) in results {
        println!("{}: {:.2} ({} txns)", merchant, stats.total, stats.count);
    }
}
```

### Critical Observations for C# Developers
1. **The `GroupBy` Trap**: In C#, `.GroupBy()` looks like a lazy operation, but it fundamentally requires caching all data in memory to form the groups before yielding them. Rust forces you to make this state explicit via `.fold()` and a `HashMap`.
2. **Result vs Exception Parsing**: C# relies on `decimal.Parse()` which throws exceptions. Rust uses `str::parse().ok()?` inside `filter_map` which silently discards invalid rows—a much safer approach for dirty CSV pipelines.
3. **Inlining and Devirtualization**: The C# pipeline allocates delegate objects for every lambda and performs virtual method calls for the iterator. The Rust compiler collapses the entire `.lines().skip().filter_map().fold()` chain into a single machine-code loop with no heap allocation for the iterator itself.
