# Rosetta Stone: Pluggable Notification & Retry Pipeline

This exercise demonstrates how C#, Go, and Rust model a polymorphic notification pipeline consisting of multiple backends (Email, SMS) and middleware decorators (Retry). 

We focus on the architectural differences:
*   **C#:** Standard Dependency Injection and nominal interfaces.
*   **Go:** Structural typing for implicit middleware chaining.
*   **Rust:** Implementing both generic (static) and trait object (dynamic) dispatch to highlight the performance and syntax trade-offs.

---

## 1. C# Implementation (Nominal Interfaces)

In C#, polymorphism is handled strictly via nominal interfaces. We define `INotifier`, implement it, and use a decorator pattern for retries. The CoreCLR handles the virtual dispatch automatically.

**`Program.cs`**
```csharp
using System;
using System.Collections.Generic;

namespace NotificationPipeline
{
    // 1. The Interface
    public interface INotifier
    {
        void Send(string message);
    }

    // 2. Concrete Implementations
    public class EmailNotifier : INotifier
    {
        public void Send(string message)
        {
            // Virtual call routing ends up here
            Console.WriteLine($"[Email] Sent: {message}");
        }
    }

    public class SmsNotifier : INotifier
    {
        public void Send(string message)
        {
            Console.WriteLine($"[SMS] Sent: {message}");
        }
    }

    // 3. Decorator / Middleware Pattern
    public class RetryNotifier : INotifier
    {
        private readonly INotifier _inner;
        private readonly int _maxRetries;

        public RetryNotifier(INotifier inner, int maxRetries)
        {
            _inner = inner;
            _maxRetries = maxRetries;
        }

        public void Send(string message)
        {
            for (int i = 0; i < _maxRetries; i++)
            {
                try
                {
                    Console.WriteLine($"[Retry] Attempt {i + 1}");
                    _inner.Send(message); // Virtual dispatch inside decorator
                    return;
                }
                catch
                {
                    if (i == _maxRetries - 1) throw;
                }
            }
        }
    }

    class Program
    {
        static void Main(string[] args)
        {
            // Heterogeneous list of notifiers
            var notifiers = new List<INotifier>
            {
                new EmailNotifier(),
                new RetryNotifier(new SmsNotifier(), 3)
            };

            foreach (var notifier in notifiers)
            {
                // Dynamic dispatch via CoreCLR vtable
                notifier.Send("System Alert: High CPU");
            }
        }
    }
}
```

---

## 2. Go Implementation (Structural Interfaces)

In Go, the `Notifier` interface is satisfied implicitly. The `RetryMiddleware` doesn't need to declare that it implements `Notifier`; it just implements the `Send` method.

**`main.go`**
```go
package main

import (
	"fmt"
)

// 1. The Interface
// Any type that implements Send(string) error implicitly satisfies this.
type Notifier interface {
	Send(message string) error
}

// 2. Concrete Implementations
type EmailNotifier struct{}

func (e EmailNotifier) Send(message string) error {
	fmt.Printf("[Email] Sent: %s\n", message)
	return nil
}

type SmsNotifier struct{}

func (s SmsNotifier) Send(message string) error {
	fmt.Printf("[SMS] Sent: %s\n", message)
	return nil
}

// 3. Decorator / Middleware Pattern
// We embed the interface inside the struct
type RetryNotifier struct {
	Inner      Notifier // runtime.iface (itab + data ptr)
	MaxRetries int
}

func (r RetryNotifier) Send(message string) error {
	var err error
	for i := 0; i < r.MaxRetries; i++ {
		fmt.Printf("[Retry] Attempt %d\n", i+1)
		err = r.Inner.Send(message) // Dynamic dispatch via itab
		if err == nil {
			return nil
		}
	}
	return err
}

func main() {
	// Heterogeneous slice of Notifiers
	// Each element is a 16-byte fat pointer (runtime.iface)
	notifiers := []Notifier{
		EmailNotifier{},
		RetryNotifier{
			Inner:      SmsNotifier{},
			MaxRetries: 3,
		},
	}

	for _, n := range notifiers {
		n.Send("System Alert: High CPU")
	}
}
```

---

## 3. Rust Implementation (Static vs Dynamic Dispatch)

Rust forces you to be explicit about memory and dispatch mechanisms. We will implement BOTH:
1.  **Static Dispatch (Monomorphization):** Fastest execution, but rigid.
2.  **Dynamic Dispatch (Trait Objects):** Flexible heterogeneous collections, but incurs vtable overhead and requires heap allocation (`Box`).

**`Cargo.toml`**
```toml
[package]
name = "notification_pipeline"
version = "0.1.0"
edition = "2021"
```

**`src/main.rs`**
```rust
// 1. The Trait
pub trait Notifier {
    fn send(&self, message: &str) -> Result<(), String>;
}

// 2. Concrete Implementations
pub struct EmailNotifier;

impl Notifier for EmailNotifier {
    fn send(&self, message: &str) -> Result<(), String> {
        println!("[Email] Sent: {}", message);
        Ok(())
    }
}

pub struct SmsNotifier;

impl Notifier for SmsNotifier {
    fn send(&self, message: &str) -> Result<(), String> {
        println!("[SMS] Sent: {}", message);
        Ok(())
    }
}

// 3. Middleware: STATIC DISPATCH Version
// Takes ownership of a generic type N that implements Notifier.
// The compiler generates a unique struct/implementation for every N used!
pub struct StaticRetryNotifier<N: Notifier> {
    inner: N,
    max_retries: usize,
}

impl<N: Notifier> Notifier for StaticRetryNotifier<N> {
    fn send(&self, message: &str) -> Result<(), String> {
        for i in 0..self.max_retries {
            println!("[Static Retry] Attempt {}", i + 1);
            if self.inner.send(message).is_ok() { // Inlined by compiler! Zero overhead.
                return Ok(());
            }
        }
        Err("Retries exhausted".to_string())
    }
}

// 4. Middleware: DYNAMIC DISPATCH Version
// Uses a Trait Object (dyn Notifier) boxed on the heap.
pub struct DynamicRetryNotifier {
    // Box<dyn Trait> is a fat pointer: [data ptr, vtable ptr]
    inner: Box<dyn Notifier>,
    max_retries: usize,
}

impl Notifier for DynamicRetryNotifier {
    fn send(&self, message: &str) -> Result<(), String> {
        for i in 0..self.max_retries {
            println!("[Dynamic Retry] Attempt {}", i + 1);
            if self.inner.send(message).is_ok() { // Vtable lookup happens here
                return Ok(());
            }
        }
        Err("Retries exhausted".to_string())
    }
}

fn main() {
    println!("--- Static Dispatch Pipeline ---");
    // We construct the pipeline at compile time. 
    // The type is known exactly: StaticRetryNotifier<SmsNotifier>
    let static_pipeline = StaticRetryNotifier {
        inner: SmsNotifier,
        max_retries: 3,
    };
    let _ = static_pipeline.send("System Alert: Database Down");

    println!("\n--- Dynamic Dispatch Pipeline (Heterogeneous Collection) ---");
    // To have a Vec of mixed types, we MUST use dyn Notifier.
    // This forces heap allocations (Box).
    let notifiers: Vec<Box<dyn Notifier>> = vec![
        Box::new(EmailNotifier),
        Box::new(DynamicRetryNotifier {
            inner: Box::new(SmsNotifier),
            max_retries: 3,
        }),
    ];

    for n in notifiers {
        let _ = n.send("System Alert: High CPU");
    }
}
```

---

## Critical Observations for C# Developers

1.  **Heterogeneous Collections Cost You:** Look at the Rust `main` function. To create a list containing *both* `EmailNotifier` and `DynamicRetryNotifier`, we are forced to use `Vec<Box<dyn Notifier>>`. In C#, `List<INotifier>` obscures the fact that it's doing the exact same thing under the hood—holding a list of references to heap-allocated objects. Rust makes the heap allocation (`Box`) and the vtable usage (`dyn`) explicit.
2.  **Monomorphization is the Rust Superpower:** The `StaticRetryNotifier<N>` in Rust has NO equivalent in Go or standard C# interface usage. The Rust compiler will completely remove the function call boundaries and inline the `SmsNotifier::send` logic directly inside the retry loop. If you measure this in a tight loop, the static version will be significantly faster than the dynamic version, Go version, or C# version.
3.  **Go's Implicit Magic:** The Go `RetryNotifier` simply declares a struct field `Inner Notifier`. Because `SmsNotifier` implements `Send`, it seamlessly works. Note that Go's interface is a fat pointer (data + itab), making interface method calls slightly slower than a direct C# class virtual call, but the creation of the heterogeneous slice `[]Notifier` feels very similar syntactically to C#.
4.  **Value Types and Interfaces:** In the C# and Go examples, if `EmailNotifier` was a value type (a `struct` in C#), putting it into `List<INotifier>` or `[]Notifier` would cause a boxing allocation on the heap. In Rust's static approach, `StaticRetryNotifier` takes the `SmsNotifier` strictly on the stack. No heap allocation occurs at all.
