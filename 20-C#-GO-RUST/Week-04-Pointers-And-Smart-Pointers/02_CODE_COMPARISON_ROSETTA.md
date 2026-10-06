# Week 04 Code Comparison: Directed Acyclic Graphs (DAGs) and Cycles

In this exercise, we will build a simple Graph structure that allows nodes to have children and parents. We will intentionally introduce a cycle and observe how each language handles the memory. 

This scenario perfectly illustrates the difference between Garbage Collection (C#, Go) and strictly enforced ownership semantics (Rust).

---

## 1. C# Implementation: Garbage Collected References

In C#, building a graph is trivial. We just use object references. The .NET Garbage Collector uses a mark-and-sweep algorithm, meaning it starts at root references and traces the graph. It handles cycles effortlessly—if a cycle of objects has no root reference, the entire cycle is collected.

**Project Setup (`DAG.csproj`)**
```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
```

**Code (`Program.cs`)**
```csharp
using System;
using System.Collections.Generic;

// In C#, class references are implicitly garbage-collected pointers.
public class Node
{
    public string Name { get; }
    
    // List of references to other heap objects.
    public List<Node> Children { get; } = new List<Node>();
    public Node? Parent { get; set; }

    public Node(string name)
    {
        Name = name;
        Console.WriteLine($"Node {Name} created.");
    }

    public void AddChild(Node child)
    {
        Children.Add(child);
        child.Parent = this;
    }

    ~Node()
    {
        // Finalizer to observe garbage collection (Note: non-deterministic in C#)
        Console.WriteLine($"Node {Name} is being garbage collected!");
    }
}

public class Program
{
    public static void Main()
    {
        CreateGraph();
        
        // Force a GC collection to demonstrate cycle resolution.
        // In real applications, NEVER do this!
        GC.Collect();
        GC.WaitForPendingFinalizers();
        
        Console.WriteLine("Application exiting.");
    }

    static void CreateGraph()
    {
        // Memory Layout: A, B, C are allocated on the heap.
        var a = new Node("A");
        var b = new Node("B");
        var c = new Node("C");

        a.AddChild(b);
        b.AddChild(c);
        
        // INTRODUCE A CYCLE: C points back to A
        c.AddChild(a); 

        Console.WriteLine("Graph created with cycle A -> B -> C -> A.");
        
        // When this method exits, 'a', 'b', and 'c' local variables are popped off the stack.
        // The heap objects are orphaned, even though they point to each other.
        // The GC will successfully collect them.
    }
}
```

**Run Command:** `dotnet run`

---

## 2. Go Implementation: Garbage Collected Pointers

Go’s approach is syntactically different (explicit pointers `*Node` instead of implicit references), but structurally identical. Go also uses a concurrent mark-and-sweep GC, which detects and cleans up cycles automatically.

**Project Setup:**
```bash
go mod init graphdemo
```

**Code (`main.go`)**
```go
package main

import (
	"fmt"
	"runtime"
)

// Node represents a vertex in our graph.
// We use pointers (*Node) to represent shared references.
type Node struct {
	Name     string
	Children []*Node
	Parent   *Node
}

func NewNode(name string) *Node {
	fmt.Printf("Node %s created.\n", name)
	return &Node{Name: name}
}

func (n *Node) AddChild(child *Node) {
	n.Children = append(n.Children, child)
	child.Parent = n
}

func createGraph() {
	// A, B, C are created. Escape analysis will allocate these on the heap 
    // because they form a cyclic structure that escapes this function's simple stack frame.
	a := NewNode("A")
	b := NewNode("B")
	c := NewNode("C")

	a.AddChild(b)
	b.AddChild(c)
	
	// INTRODUCE A CYCLE
	c.AddChild(a)

	fmt.Println("Graph created with cycle A -> B -> C -> A.")
    // No explicit destructors in Go. We rely on GC.
}

func main() {
	createGraph()
	
	// Force GC to demonstrate cleanup (Again, for demo only)
	runtime.GC()
	fmt.Println("Application exiting.")
}
```

**Run Command:** `go run main.go`

---

## 3. Rust Implementation: Strict Ownership and Smart Pointers

Here is where the paradigm shift occurs. Rust does not have a Garbage Collector. To build a graph where multiple nodes can refer to each other and be mutated, we must compose Smart Pointers.

We will use:
- `Rc<T>` to allow multiple ownership of a Node.
- `RefCell<T>` to allow mutating the Node (adding children) while it is shared via `Rc`.
- `Weak<T>` for the parent pointer to prevent a memory leak!

**Project Setup (`Cargo.toml`)**
```toml
[package]
name = "rust_graph"
version = "0.1.0"
edition = "2021"
```

**Code (`src/main.rs`)**
```rust
use std::rc::{Rc, Weak};
use std::cell::RefCell;

// Our Node requires runtime borrow checking (RefCell) wrapped in reference counting (Rc).
struct Node {
    name: String,
    // Children hold strong references. They keep the child alive.
    children: RefCell<Vec<Rc<Node>>>,
    
    // Parent holds a Weak reference. 
    // If this were an Rc<Node>, A -> B -> C -> A would cause the ref count to never hit 0!
    // Weak<T> breaks the cycle.
    parent: RefCell<Weak<Node>>, 
}

impl Node {
    fn new(name: &str) -> Rc<Node> {
        println!("Node {} created.", name);
        Rc::new(Node {
            name: name.to_string(),
            children: RefCell::new(vec![]),
            parent: RefCell::new(Weak::new()),
        })
    }

    fn add_child(parent: &Rc<Node>, child: &Rc<Node>) {
        // We use .borrow_mut() on the RefCell to mutate the vectors at runtime.
        parent.children.borrow_mut().push(Rc::clone(child));
        // Downgrade the Rc<Node> to a Weak<Node> to assign the parent.
        *child.parent.borrow_mut() = Rc::downgrade(parent);
    }
}

// Implement Drop to prove when memory is freed.
impl Drop for Node {
    fn drop(&mut self) {
        println!("Node {} is being dropped (freed)!", self.name);
    }
}

fn create_graph() {
    let a = Node::new("A");
    let b = Node::new("B");
    let c = Node::new("C");

    Node::add_child(&a, &b);
    Node::add_child(&b, &c);
    
    // INTRODUCE A CYCLE (Child to Parent via strong reference)
    // If we push 'a' as a child of 'c', we create an Rc cycle!
    // Let's see what happens if we DO NOT use a Weak pointer for this specific edge.
    // Node::add_child(&c, &a); 
    
    // IF YOU UNCOMMENT THE LINE ABOVE, THE CYCLE LEAKS! 
    // The drop log will never print for A, B, or C because their Rc count never hits 0.

    println!("Graph created.");
    println!("Rc Count for A: {}", Rc::strong_count(&a)); // 1
}

fn main() {
    create_graph();
    println!("Application exiting.");
}
```

**Run Command:** `cargo run`

### Critical Observations for C# Developers

1.  **Complexity Overhead:** Building graphs or doubly-linked lists in Rust is notoriously difficult. In C#, you just type `public Node Parent`. In Rust, you must architect the ownership tree explicitly: `RefCell<Weak<Node>>`. 
2.  **Runtime Mutability Panics:** If you call `.borrow_mut()` on a `RefCell` that is already borrowed mutably somewhere else in the same call stack, your Rust program will panic and crash. C# would just allow concurrent modification (potentially causing data races if multi-threaded).
3.  **Memory Leaks are Possible:** Rust guarantees memory *safety* (no use-after-free, no dangling pointers), but it does not guarantee the *absence of leaks*. As demonstrated, cyclic `Rc<T>` structures will leak memory precisely because there is no Mark-and-Sweep GC to save you.
4.  **Zero-Cost Abstractions:** When `create_graph()` in Rust ends, the variables fall out of scope, the ref counts hit zero, and `Drop` is called synchronously. The memory is freed instantly, without pausing the execution pipeline for a GC cycle.
