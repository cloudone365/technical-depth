# Week 21: Hands-On Lab Exercise

## The Polyglot Microservices Lab
In this lab, you will build a microservice architecture. A user-facing Order Service (written in Go) will communicate internally with an Inventory Service (written in Rust) using gRPC over HTTP/2.

---

### Day 1-2: Defining the Contract and the Rust Server
You are building the **Inventory Service**. It needs to check if items are in stock and deduct them.

**Step 1:** Write the `inventory.proto` file.
Define a service with a Unary RPC: `CheckStock`.
Define a service with a Bidirectional Streaming RPC: `HoldInventory` (for holding stock while the user is in checkout).

**Step 2:** Implement the Rust server using `tonic`.
1. Use `cargo new inventory_svc`
2. Add the `tonic` and `prost` dependencies.
3. Build the server using the `.proto` file.
4. **Service Discovery Pattern:** Hardcode the Rust server to listen on port `50051`. 

**Step 3:** Enable Reflection.
Add the `tonic-reflection` crate and enable it so we can test the Rust server without a client.
Test it from the terminal using `grpcurl`:
```bash
# List available services
grpcurl -plaintext localhost:50051 list

# Call the CheckStock endpoint
grpcurl -plaintext -d '{"item_id": "SKU-123"}' localhost:50051 inventory.InventoryService/CheckStock
```

---

### Day 3-4: The Go Client and TLS

Now you are building the **Order Service** in Go. It exposes a standard JSON HTTP API to the outside world, but talks gRPC internally to the Rust server.

**Step 1:** Generate the Go stubs from the exact same `inventory.proto` file.
**Step 2:** Implement the Go HTTP handler for `POST /api/orders`. Inside this handler, use the generated gRPC client to call `CheckStock` on the Rust server.
**Step 3:** Secure it with TLS.
gRPC works best over secure connections. 
1. Generate self-signed certificates using `openssl`.
   ```bash
   openssl req -x509 -newkey rsa:4096 -nodes -keyout key.pem -out cert.pem -days 365
   ```
2. Configure the Rust `tonic` server to use `ServerTlsConfig`.
3. Configure the Go `grpc.Dial` to use `credentials.NewTLS`.

---

### Friday: Mob Review & Benchmarking

Gather your team. It's time to prove why we built this using gRPC instead of HTTP/JSON.

**The Benchmark Test:**
Write a quick C# application using `HttpClient` that makes 10,000 sequential HTTP/JSON requests to an endpoint, and another loop that makes 10,000 gRPC calls using a generated C# client to the Rust server.

Fill out the benchmark table:

| Metric | HTTP/1.1 JSON | gRPC (HTTP/2 Protobuf) | Improvement Factor |
| :--- | :--- | :--- | :--- |
| **Payload Size (1 Req/Res)** | e.g., 250 bytes | e.g., 40 bytes | ? |
| **Total Time (10,000 reqs)** | e.g., 4.5 sec | e.g., 0.8 sec | ? |
| **Connection Overhead** | Opens many sockets | Single Multiplexed TCP | N/A |

**Discussion Questions:**
1. How does the Protobuf code generation step change your CI/CD pipeline compared to traditional C# Web APIs? Do you check the generated code into Git?
2. If we need to add a new field `warehouse_id` to the `CheckStock` request, what are the steps to ensure we don't break older clients?
3. How did dealing with TLS differ between the Rust `tonic` configuration and the Go `grpc.Dial` configuration?
4. How do you handle load balancing gRPC connections in Kubernetes, knowing that it uses a single persistent TCP connection? (Hint: L7 load balancing vs L4).

**Sign-off Checklist (Each member must answer):**
1. [ ] Can you explain why JSON is less efficient to parse than Protobuf varints?
2. [ ] Can you explain the concept of HTTP/2 multiplexing?
3. [ ] Have you successfully generated code from a `.proto` file in at least two languages?
4. [ ] Did you successfully configure and call a Bidirectional Streaming RPC?
5. [ ] Do you know the three golden rules of backward compatibility in Protocol Buffers?
6. [ ] Can you use `grpcurl` to inspect a running gRPC server?

---

### Stretch Goal
Implement **gRPC Interceptors** (the gRPC equivalent of ASP.NET Core Middleware). Write a Go Unary Interceptor that logs the execution time of every outgoing gRPC call, and a Rust Unary Interceptor that authenticates an incoming JWT token in the gRPC metadata headers.
