# Week 09: Slices, Vectors & Memory Allocation Dynamics

## Why This Week Matters for Your Career Transition
In C#, the Garbage Collector (GC) forgives many sins. You can allocate strings, copy arrays, and instantiate objects inside hot loops. Most of the time, the rapid Gen0 collection is fast enough to hide architectural memory flaws. 

Transitioning to Go and Rust requires an intimate understanding of memory layouts. Go has a GC, but escaping variables to the heap puts heavy pressure on it, causing latency spikes. Rust has no GC; you manage allocations explicitly via the ownership model. This week, we tear down dynamic arrays. You will learn exactly what a "slice" is at the bare-metal memory level, how capacity growth strategies work, and why minimizing heap allocations is the golden key to high-performance systems programming.

## The Baseline: C# List<T> and Span<T>
A C# `List<T>` is a managed class containing a reference to a backing array (`T[]`). It tracks `Count` (elements used) and `Capacity` (size of the backing array). When `Count == Capacity`, `List<T>` allocates a new array (usually double the size), copies the elements, and lets the GC clean up the old array.

Modern high-performance C# code heavily utilizes `Span<T>` and `ReadOnlySpan<T>`. `Span<T>` is a `ref struct` representing a contiguous region of arbitrary memory (managed or unmanaged). It allows zero-allocation slicing (e.g., parsing substrings without calling `string.Substring`, which allocates). Because it is a `ref struct`, the runtime guarantees it never escapes to the heap, keeping it completely GC-free.

## Go: The Slice Header
A Go slice is a 3-word value type (a 24-byte struct on a 64-bit OS) that points to an underlying array.

### ASCII Slice Header Diagram
```text
Go Slice Header (24 bytes)
+-----------------------+ 0 bytes
| array unsafe.Pointer  | -----> [ Backend Array (Heap or Stack) ]
+-----------------------+ 8 bytes
| len   int             |
+-----------------------+ 16 bytes
| cap   int             |
+-----------------------+ 24 bytes
```

When you pass a slice to a function in Go, you pass this 24-byte struct **by value**. However, because it contains a pointer, **the underlying array is shared**.

### The Slice Aliasing Gotcha
This shared array architecture leads to notoriously subtle bugs. 
```go
func modifySlices() {
    base := make([]int, 0, 5)     // len=0, cap=5
    base = append(base, 1, 2, 3)  // len=3, cap=5
    
    // a and b alias the same backing array!
    a := base[:2] // len=2, cap=5. Contains [1, 2]
    b := base[1:3] // len=2, cap=4. Contains [2, 3]

    // We append to 'a'. Since a.len (2) < a.cap (5), it writes to index 2 
    // of the backing array.
    a = append(a, 99) 
    
    // BUG! b now contains [2, 99]. 
    // The append on 'a' overwrote the memory 'b' was looking at!
}
```
**To prevent this:** Use the full slice expression `a := base[:2:2]`. This sets `len=2` and `cap=2`. Now, calling `append(a, 99)` forces Go to allocate a new backing array, protecting `b`.

## Rust: Vec<T> and Fat Pointers

Rust's `Vec<T>` is architecturally identical to C#'s `List<T>` and Go's slice: it contains a pointer to a heap allocation, a length, and a capacity.

### Vec<T> Memory Layout
```text
Rust Vec<T> (24 bytes)
+-----------------------+ 
| ptr: NonNull<T>       | -----> [ Heap Allocation ]
+-----------------------+ 
| len: usize            |
+-----------------------+ 
| cap: usize            |
+-----------------------+ 
```

However, Rust strictly differentiates between **owned vectors** (`Vec<T>`) and **borrowed slices** (`&[T]`).
- A slice `&[T]` in Rust is a "fat pointer"—it contains only the memory address and the length (2 words / 16 bytes). It does not contain capacity because you cannot append to a borrowed slice.

### The Deref Coercion
Rust automatically coerces `&Vec<T>` into `&[T]`. This is crucial for API design.
**Rule of thumb:** If a function only reads data, it should accept `&[T]`, never `&Vec<T>`. 
```rust
// BAD: Forces caller to allocate a Vec
fn print_items(items: &Vec<i32>) { ... }

// GOOD: Caller can pass a Vec, an Array, or a slice
fn print_items(items: &[i32]) { ... }
```

### The HashMap Entry API Pattern
When parsing data, checking a map and then inserting causes two lookups. Rust's `Entry` API prevents this.
```rust
// The Entry API does the hash and lookup EXACTLY ONCE.
map.entry(key)
   .and_modify(|count| *count += 1)
   .or_insert(1);
```

### String Types Comparison

Strings are just vectors/slices of bytes with UTF guarantees. Understanding which to use is critical for zero-allocation code.

| Type | Nature | C# Equivalent | Use Case |
| :--- | :--- | :--- | :--- |
| `String` | Owned, heap-allocated, mutable | `string` / `StringBuilder` | Building new text dynamically |
| `&str` | Borrowed slice (fat ptr) | `ReadOnlySpan<char>` | Reading text, zero-copy slicing |
| `&'static str` | Binary literal, infinite lifetime | `const string` | Hardcoded messages |
| `Cow<'a, str>` | "Clone On Write" enum | No direct equivalent | You might need to mutate, but usually don't |

## Common Misconceptions to Unlearn
- **"Go slices are like C# arrays."** False. They are views over arrays. A small slice can keep a massive backing array alive in the GC, causing severe memory leaks.
- **"I should always pass Vec<T> by reference in Rust."** False. Passing `&Vec<T>` forces an unnecessary double-indirection (pointer to a struct containing a pointer). Pass `&[T]` instead.
