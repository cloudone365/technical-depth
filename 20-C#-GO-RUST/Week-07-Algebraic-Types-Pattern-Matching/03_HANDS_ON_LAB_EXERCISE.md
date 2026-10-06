# Week 07 · Hands-On Lab Exercise: High-Throughput Protocol Parser
### Building Type-Safe State Machines and Network Parsers in Go and Rust

> **Lab Objective:** You are architecting the wire-protocol ingestion engine for a high-throughput streaming message broker (similar to Kafka or Redis).
> 
> The broker accepts raw text/binary frames and decodes them into four distinct commands:
> 1. `CONN <client_id> <version>` $\longrightarrow$ Establishes a client session.
> 2. `PUB <topic> <payload_bytes>` $\longrightarrow$ Publishes payload to a topic.
> 3. `SUB <topic>` $\longrightarrow$ Subscribes client to a topic.
> 4. `ACK <message_id>` $\longrightarrow$ Acknowledges receipt of a message.
> 
> You will implement this parser across **Go** and **Rust**:
> * **In Go:** Build an interface-based command parser with a type switch. Discover how easily developers accidentally omit variants, causing silent production bugs.
> * **In Rust:** Build an Algebraic Data Type (`enum Command`) with exhaustive pattern matching and zero-copy string borrowing. Experience how adding a new variant breaks compilation everywhere until every case is proven handled.
> * **Friday Mob Review:** Defend how sum types prevent invalid states and evaluate Niche Value Optimization memory footprints.

---

## Lab Architecture & Timetable

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LAB WORKFLOW & TIMETABLE                        │
├────────────────────────────────────────────────────────────────────────┤
│ • Day 1-2: Go Protocol Ingestion & The Silent Omission Bug             │
│            Implement Command interface and 4 command structs.          │
│            Write parser and type-switch execution pipeline.            │
│            Add `PING` command: observe silent runtime omission.        │
│                                                                        │
│ • Day 3-4: Rust ADT & Zero-Copy Pattern Matching                       │
│            Implement enum Command with payloads.                       │
│            Build zero-copy parser returning borrowed slices.           │
│            Add `PING` variant: observe compile-time exhaustiveness.    │
│                                                                        │
│ • Day 5:   Friday Mob Review & Memory Profiling                        │
│            Inspect `size_of::<Option<&Command>>()`, discuss NVO,       │
│            complete 7-question technical defense.                      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: Days 1–2 — The Go Reference Implementation (`parser.go`)

Save this into `labs/week07/go/parser.go`:

```go
package main

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
)

// ------------------------------------------------------------------------
// COMMAND INTERFACE & CONCRETE STRUCTS
// ------------------------------------------------------------------------

type Command interface {
	isCommand() // Sealed marker interface
}

type ConnectCmd struct {
	ClientID string
	Version  uint8
}
func (ConnectCmd) isCommand() {}

type PublishCmd struct {
	Topic   string
	Payload []byte
}
func (PublishCmd) isCommand() {}

type SubscribeCmd struct {
	Topic string
}
func (SubscribeCmd) isCommand() {}

type AcknowledgeCmd struct {
	MessageID uint64
}
func (AcknowledgeCmd) isCommand() {}

// Unhandled command deliberately added to simulate protocol evolution:
type PingCmd struct {
	Timestamp int64
}
func (PingCmd) isCommand() {}

// ------------------------------------------------------------------------
// THE GO PARSER
// ------------------------------------------------------------------------

func ParseGoCommand(input string) (Command, error) {
	parts := strings.SplitN(strings.TrimSpace(input), " ", 3)
	if len(parts) == 0 || parts[0] == "" {
		return nil, errors.New("empty command frame")
	}

	op := parts[0]

	switch op {
	case "CONN":
		if len(parts) < 3 {
			return nil, errors.New("CONN requires <client_id> <version>")
		}
		ver, err := strconv.ParseUint(parts[2], 10, 8)
		if err != nil {
			return nil, fmt.Errorf("invalid version: %w", err)
		}
		return ConnectCmd{ClientID: parts[1], Version: uint8(ver)}, nil

	case "PUB":
		if len(parts) < 3 {
			return nil, errors.New("PUB requires <topic> <payload>")
		}
		return PublishCmd{Topic: parts[1], Payload: []byte(parts[2])}, nil

	case "SUB":
		if len(parts) < 2 {
			return nil, errors.New("SUB requires <topic>")
		}
		return SubscribeCmd{Topic: parts[1]}, nil

	case "ACK":
		if len(parts) < 2 {
			return nil, errors.New("ACK requires <msg_id>")
		}
		id, err := strconv.ParseUint(parts[1], 10, 64)
		if err != nil {
			return nil, fmt.Errorf("invalid message ID: %w", err)
		}
		return AcknowledgeCmd{MessageID: id}, nil

	case "PING":
		return PingCmd{Timestamp: 1774900000}, nil

	default:
		return nil, fmt.Errorf("unknown opcode: %s", op)
	}
}

// ------------------------------------------------------------------------
// THE EXECUTION PIPELINE: THE SILENT OMISSION TRAP
// ------------------------------------------------------------------------

func ExecuteGoCommand(cmd Command) {
	// BUG IN GO: Notice that PingCmd is completely omitted from this type switch!
	// The Go compiler compiles this without ANY warning or error!
	switch c := cmd.(type) {
	case ConnectCmd:
		fmt.Printf("[Go Engine] Connected client '%s' with v%d\n", c.ClientID, c.Version)
	case PublishCmd:
		fmt.Printf("[Go Engine] Published %d bytes to topic '%s'\n", len(c.Payload), c.Topic)
	case SubscribeCmd:
		fmt.Printf("[Go Engine] Subscribed to topic '%s'\n", c.Topic)
	case AcknowledgeCmd:
		fmt.Printf("[Go Engine] Acknowledged message #%d\n", c.MessageID)
	default:
		// Silent fallthrough! In production, this drops commands or causes unexpected state!
		fmt.Printf("\x1b[31m[Go WARNING] Unhandled command type: %T (Silent Bug!)\x1b[0m\n", c)
	}
}

func main() {
	frames := []string{
		"CONN gateway-node-01 2",
		"PUB telemetry-stream sensor_val:42.5",
		"SUB telemetry-stream",
		"ACK 98847129",
		"PING", // Newly added command!
	}

	for _, frame := range frames {
		cmd, err := ParseGoCommand(frame)
		if err != nil {
			fmt.Printf("Parse error: %v\n", err)
			continue
		}
		ExecuteGoCommand(cmd)
	}
}
```

Run the code:
```bash
go run parser.go
```
Notice that `PING` compiled completely fine, but was silently dropped at runtime with a warning!

---

## Part 2: Days 3–4 — The Rust Zero-Copy ADT Implementation (`src/main.rs`)

In Rust, we construct a true Algebraic Data Type using `enum`. Every variant carries its specific payload, and the compiler mathematically forbids unhandled cases.

Save this in `labs/week07/rust/src/main.rs`:

```rust
use std::fmt;

// ------------------------------------------------------------------------
// ZERO-COPY ALGEBRAIC DATA TYPE (Borrowing string slices directly)
// ------------------------------------------------------------------------
#[derive(Debug, PartialEq)]
pub enum Command<'a> {
    Connect { client_id: &'a str, version: u8 },
    Publish { topic: &'a str, payload: &'a [u8] },
    Subscribe { topic: &'a str },
    Acknowledge(u64),
    Ping(i64),
}

#[derive(Debug, PartialEq)]
pub enum ParseError {
    EmptyFrame,
    UnknownOpcode(String),
    MissingArgument(&'static str),
    InvalidNumber(&'static str),
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParseError::EmptyFrame => write!(f, "empty command frame"),
            ParseError::UnknownOpcode(op) => write!(f, "unknown opcode: {}", op),
            ParseError::MissingArgument(arg) => write!(f, "missing argument: {}", arg),
            ParseError::InvalidNumber(field) => write!(f, "invalid numeric format for {}", field),
        }
    }
}

// ------------------------------------------------------------------------
// ZERO-COPY PARSER (Zero heap allocations!)
// ------------------------------------------------------------------------
pub fn parse_command<'a>(input: &'a str) -> Result<Command<'a>, ParseError> {
    let mut parts = input.split_whitespace();
    let op = parts.next().ok_or(ParseError::EmptyFrame)?;

    match op {
        "CONN" => {
            let client_id = parts.next().ok_or(ParseError::MissingArgument("client_id"))?;
            let ver_str = parts.next().ok_or(ParseError::MissingArgument("version"))?;
            let version = ver_str.parse::<u8>().map_err(|_| ParseError::InvalidNumber("version"))?;
            Ok(Command::Connect { client_id, version })
        }
        "PUB" => {
            let topic = parts.next().ok_or(ParseError::MissingArgument("topic"))?;
            // Remainder of the string is the payload
            let offset = input.find(topic).unwrap() + topic.len();
            let payload_str = input[offset..].trim_start();
            if payload_str.is_empty() {
                return Err(ParseError::MissingArgument("payload"));
            }
            Ok(Command::Publish {
                topic,
                payload: payload_str.as_bytes(),
            })
        }
        "SUB" => {
            let topic = parts.next().ok_or(ParseError::MissingArgument("topic"))?;
            Ok(Command::Subscribe { topic })
        }
        "ACK" => {
            let id_str = parts.next().ok_or(ParseError::MissingArgument("message_id"))?;
            let id = id_str.parse::<u64>().map_err(|_| ParseError::InvalidNumber("message_id"))?;
            Ok(Command::Acknowledge(id))
        }
        "PING" => Ok(Command::Ping(1774900000)),
        other => Err(ParseError::UnknownOpcode(other.to_string())),
    }
}

// ------------------------------------------------------------------------
// EXHAUSTIVE EXECUTION PIPELINE
// ------------------------------------------------------------------------
pub fn execute_command(cmd: Command) {
    // RUST COMPILER PROOF:
    // If you delete the `Command::Ping` arm below, the code WILL NOT COMPILE!
    // error[E0004]: non-exhaustive patterns: `Command::Ping(_)` not covered!
    match cmd {
        Command::Connect { client_id, version } if version < 2 => {
            println!("\x1b[33m[Rust Engine] Rejected client '{}': Protocol v{} is deprecated!\x1b[0m", client_id, version);
        }
        Command::Connect { client_id, version } => {
            println!("[Rust Engine] Connected client '{}' with v{}", client_id, version);
        }
        Command::Publish { topic, payload } => {
            println!("[Rust Engine] Published {} bytes to topic '{}'", payload.len(), topic);
        }
        Command::Subscribe { topic } => {
            println!("[Rust Engine] Subscribed to topic '{}'", topic);
        }
        Command::Acknowledge(msg_id) => {
            println!("[Rust Engine] Acknowledged message #{}", msg_id);
        }
        Command::Ping(timestamp) => {
            println!("[Rust Engine] Responded PONG to heartbeat at {}", timestamp);
        }
    }
}

fn main() {
    println!("=== RUST ZERO-COPY PROTOCOL PARSER ===");

    // Prove memory sizes and Niche Value Optimization
    println!("[Memory] size_of::<Command>:        {} bytes", std::mem::size_of::<Command>());
    println!("[Memory] size_of::<Option<&Command>>: {} bytes (NVO applied!)\n", std::mem::size_of::<Option<&Command>>());

    let frames = vec![
        "CONN gateway-node-01 2",
        "CONN legacy-client 1", // Triggers pattern match guard!
        "PUB telemetry-stream sensor_val:42.5",
        "SUB telemetry-stream",
        "ACK 98847129",
        "PING",
    ];

    for frame in frames {
        match parse_command(frame) {
            Ok(cmd) => execute_command(cmd),
            Err(e) => println!("Parse error: {}", e),
        }
    }
}
```

---

## Part 3: Friday Mob Review & Defense Protocol

Gather the 5-engineer team. Have one engineer delete the `Command::Ping` branch in `execute_command` and run `cargo check`.

### 7 Mandatory Technical Defense Questions

#### 1. What happened when we deleted the `Command::Ping` branch in Rust vs Go?
* **Expected Answer:** In Rust, `rustc` immediately failed compilation with error `E0004: non-exhaustive patterns: Command::Ping(_) not covered`. In Go, the code compiled with zero warnings, and the unhandled command silently executed the `default` fallback at runtime. Rust guarantees at compile time that every possible domain state is handled.

#### 2. What is the Niche Value Optimization (NVO), and why is `size_of::<Option<&Command>>()` exactly 8 bytes?
* **Expected Answer:** In Rust, a reference `&T` can never be null. The compiler uses the all-zero bit pattern (`0x0000_0000_0000_0000`) as the "niche" to represent `None`, and any non-zero memory address to represent `Some(&T)`. This eliminates the need for an external discriminant tag byte, making `Option<&T>` identical in size to a raw 8-byte pointer with zero memory overhead.

#### 3. How did our Rust parser achieve "Zero-Copy" parsing?
* **Expected Answer:** Instead of allocating new `String` or `Vec<u8>` heap buffers for every command, `Command<'a>` borrows string slices (`&'a str`) and byte slices (`&'a [u8]`) that point directly into the input buffer. The lifetime `'a` ensures that `Command` cannot outlive the input frame, allowing zero heap allocations during parsing.

#### 4. How does pattern matching with guards (`if version < 2`) differ from standard switch statements?
* **Expected Answer:** Pattern match guards allow testing runtime conditions directly inside the match branch arm without nesting `if/else` statements. If the guard evaluates to `false`, the pattern match falls through to subsequent arms, allowing multi-tiered business rule resolution directly in the type-checking phase.

#### 5. Why is a C# entity with nullable fields inferior to a Rust Algebraic Data Type?
* **Expected Answer:** Nullable fields represent a Product Type ($S_1 \times S_2 \times S_3$), allowing impossible combinations (such as an order marked `Delivered` that has a `null` tracking number and an `InsufficientFunds` failure reason). A Rust enum represents a Sum Type ($S_1 + S_2 + S_3$), where only one variant and its associated fields can physically exist in memory at any instant, making invalid states unrepresentable.

#### 6. What is the physical memory layout of `enum Command`?
* **Expected Answer:** It is laid out as a **Tagged Union**: 1 byte for the discriminant tag (identifying whether it is `Connect`, `Publish`, etc.), followed by alignment padding bytes, followed by a shared union block sized to fit the largest variant payload (`Publish` with `&'a str` [16B] + `&'a [u8]` [16B] = 32 bytes).

#### 7. How does C# 9+ modern pattern matching compare to Rust's `match`?
* **Expected Answer:** C# switch expressions (`input switch { ... }`) provide powerful pattern matching (type patterns, property patterns, positional patterns), but unless paired with discriminated union libraries (like `OneOf`) or closed type hierarchies, C# cannot strictly enforce compile-time exhaustiveness over open class hierarchies.

---

## Lab Sign-off Checklist

Each engineer must verify and sign off:
* [ ] **Sum Type Comprehension:** I can explain the mathematical difference between a Product Type and a Sum Type.
* [ ] **Exhaustiveness Verification:** I have seen `rustc` fail compilation when an enum variant is missing from a `match`.
* [ ] **Zero-Copy Parsing:** I can write a Rust parser that borrows slices (`&'a str`) without allocating heap strings.
* [ ] **Niche Value Optimization:** I understand why `Option<&T>` has zero memory overhead compared to a raw pointer.
* [ ] **Go Enum Limitations:** I can explain why Go's `iota` fails to enforce compile-time domain safety.
