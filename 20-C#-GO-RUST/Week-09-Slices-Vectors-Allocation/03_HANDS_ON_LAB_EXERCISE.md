# Week 09: Hands-on Lab - Slice Aliasing and Ring Buffers

## Day 1-2: The Slice Aliasing Bug Hunt
Go's slice architecture (shared backing arrays) causes notorious bugs when slicing and appending overlap. 

Examine the following code. It contains a critical data corruption bug.

```go
package main
import "fmt"

func processBatch(data []int) []int {
    // We want to filter out negative numbers and double the rest.
    // To be "efficient", we reuse the backing array of the input slice!
    // result has len=0, but cap=len(data), pointing to the SAME array.
    result := data[:0] 
    
    for _, v := range data {
        if v > 0 { 
            // BUG HERE: This append modifies the shared backing array!
            result = append(result, v*2) 
        }
    }
    return result
}

func main() {
    original := []int{1, -1, 2, 3}
    
    // First call works seemingly fine
    res1 := processBatch(original) 
    fmt.Println("Res1:", res1)
    
    // Second call on the same original data... what happens?
    res2 := processBatch(original)
    fmt.Println("Res2:", res2) 
}
```

**Task for Students:** 
1. Run this code. You will see that `original` gets mutated during the first call, corrupting the input for the second call.
2. **Fix the bug.** Use Go's full slice expression `a[low:high:max]` to force reallocation, or simply allocate a new slice with `make([]int, 0, len(data))`.

## Day 3-4: Ring Buffer Implementation
A Ring Buffer (Circular Queue) is a fixed-size buffer that wraps around. It is widely used in high-performance networking and logging. Implement it in Rust.

### Go Reference Implementation
```go
type RingBuffer struct {
    data []int
    head int
    tail int
    size int
}

func NewRingBuffer(cap int) *RingBuffer {
    return &RingBuffer{ data: make([]int, cap) }
}

func (r *RingBuffer) Push(val int) {
    if r.size == len(r.data) {
        return // Full
    }
    r.data[r.head] = val
    r.head = (r.head + 1) % len(r.data)
    r.size++
}
```

### Rust Skeleton (Your Task)
In Rust, you can't just leave memory uninitialized safely without `unsafe` blocks. We will use `Vec<Option<T>>`.

```rust
// Cargo.toml
// [package] name = "ring_buffer"

pub struct RingBuffer<T> {
    buffer: Vec<Option<T>>,
    head: usize,
    tail: usize,
    capacity: usize,
    count: usize,
}

impl<T> RingBuffer<T> {
    pub fn new(capacity: usize) -> Self {
        // TODO: Initialize the Vec.
        // You cannot just use `Vec::with_capacity(capacity)`. That allocates space,
        // but length is 0. You need length to be `capacity` so you can index into it.
        // Hint: use a loop to push `None` `capacity` times, or the `vec!` macro if T implements Clone.
        // To be generic without requiring Clone, use `std::iter::repeat_with`.
        unimplemented!()
    }

    pub fn push(&mut self, item: T) -> Result<(), &'static str> {
        // TODO: Insert at head, advance head via modulo arithmetic. 
        // Return Err("Buffer full") if count == capacity.
        unimplemented!()
    }
    
    pub fn pop(&mut self) -> Option<T> {
        // TODO: Take item at tail using `Option::take()`.
        // This takes ownership out of the Option, leaving None in its place!
        // Advance tail via modulo arithmetic. decrement count.
        unimplemented!()
    }
}
```

## Friday Mob Review

Run the benchmark suites.

| Implementation | Ops-per-second | Allocations-per-op |
| :--- | :--- | :--- |
| Go Slice Circular | TBD | TBD (Target: 0) |
| Rust Vec<Option<T>> | TBD | TBD (Target: 0) |

### Discussion Questions
1. **How many heap allocations occurred during the Push/Pop loop benchmarks?** 
   *(Target: Exactly 1 during `new()`, and 0 during operations).*
2. **In Rust, why did we use `Vec<Option<T>>` instead of an uninitialized array `[T; N]`?**
   *(Answer: Safe Rust forbids reading uninitialized memory. `Option` guarantees a safe empty state (None) without triggering undefined behavior).*
3. **How does `Option::take()` help us satisfy the Borrow Checker?**
   *(Answer: It moves the owned `T` out of the mutable reference `&mut Option<T>` and replaces it with `None` in one atomic step, avoiding a "cannot move out of borrowed content" error).*
4. **Why is the Go slice aliasing bug so dangerous in web servers?**
   *(Answer: If multiple requests slice a shared memory buffer (like a TCP read buffer), appending to one request's slice might corrupt another concurrent request's payload data).*

## Sign-off Checklist
- [ ] Did you successfully fix the Go slice aliasing bug by isolating capacity?
- [ ] Does your Rust `RingBuffer` allocate exactly once during `new()`?
- [ ] Did you use `Option::take()` in Rust to safely move values?
- [ ] Do your benchmarks confirm zero allocations in the hot path?
