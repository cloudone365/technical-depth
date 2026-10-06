# Week 21: Code Comparison Rosetta

## The Mission: A Product Catalog gRPC Service
We will define a `ProductCatalog` service using Protocol Buffers. It will feature a Unary RPC and a Server Streaming RPC. We will implement the server and client in C#, Go, and Rust, including proper mapping of domain errors to standard gRPC status codes.

### 1. The Contract: `catalog.proto`
This file is the single source of truth for all three languages.

```protobuf
syntax = "proto3";

// Defines the Go package name
option go_package = "catalog/pb";
// Defines the C# namespace
option csharp_namespace = "Catalog.Grpc";

package catalog;

service ProductCatalog {
  // 1. Unary RPC
  rpc GetProduct (GetProductRequest) returns (ProductResponse);
  
  // 2. Server Streaming RPC
  rpc SearchProducts (SearchRequest) returns (stream ProductResponse);
}

message GetProductRequest {
  string id = 1;
}

message SearchRequest {
  string category = 1;
  int32 max_results = 2;
}

message ProductResponse {
  string id = 1;
  string name = 2;
  double price = 3;
  
  // Enums are native to protobuf
  enum Availability {
    UNKNOWN = 0;
    IN_STOCK = 1;
    OUT_OF_STOCK = 2;
  }
  Availability status = 4;
}
```

### 2. C# (.NET 8)

**Project Setup (`Catalog.csproj`):**
C# uses the `Grpc.AspNetCore` package and native MSBuild integration to compile the `.proto` file on the fly.
```xml
<Project Sdk="Microsoft.NET.Sdk.Web">
  <ItemGroup>
    <PackageReference Include="Grpc.AspNetCore" Version="2.61.0" />
    <PackageReference Include="Grpc.AspNetCore.Server.Reflection" Version="2.61.0" />
  </ItemGroup>
  <ItemGroup>
    <!-- Instructs MSBuild to generate Server code -->
    <Protobuf Include="Protos\catalog.proto" GrpcServices="Server" />
  </ItemGroup>
</Project>
```

**Server Implementation (`Services/CatalogService.cs`):**
```csharp
using Grpc.Core;
using Catalog.Grpc; // Generated namespace

public class CatalogService : ProductCatalog.ProductCatalogBase
{
    // 1. Unary Implementation
    public override Task<ProductResponse> GetProduct(GetProductRequest request, ServerCallContext context)
    {
        if (string.IsNullOrEmpty(request.Id))
        {
            // Error mapping: Throw an RpcException with a gRPC StatusCode
            throw new RpcException(new Status(StatusCode.InvalidArgument, "ID is required"));
        }

        if (request.Id == "999") // Simulate not found
        {
            throw new RpcException(new Status(StatusCode.NotFound, $"Product {request.Id} not found"));
        }

        return Task.FromResult(new ProductResponse
        {
            Id = request.Id,
            Name = "Mechanical Keyboard",
            Price = 129.99,
            Status = ProductResponse.Types.Availability.InStock
        });
    }

    // 2. Server Streaming Implementation
    public override async Task SearchProducts(
        SearchRequest request, 
        IServerStreamWriter<ProductResponse> responseStream, 
        ServerCallContext context)
    {
        for (int i = 0; i < request.MaxResults; i++)
        {
            // Check if client cancelled the request
            if (context.CancellationToken.IsCancellationRequested) break;

            await responseStream.WriteAsync(new ProductResponse
            {
                Id = $"prod-{i}",
                Name = $"{request.Category} Item {i}",
                Price = 10.0 * i,
                Status = ProductResponse.Types.Availability.InStock
            });
            
            await Task.Delay(500); // Simulate DB delay
        }
    }
}
```

### 3. Go (1.21+)

**Build Script (`generate.sh`):**
Go uses the `protoc` CLI compiler with standard plugins.
```bash
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest

protoc --go_out=. --go_opt=paths=source_relative \
    --go-grpc_out=. --go-grpc_opt=paths=source_relative \
    catalog.proto
```

**Server Implementation (`main.go`):**
```go
package main

import (
	"context"
	"fmt"
	"log"
	"net"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/reflection"

	pb "catalog/pb" // Generated code
)

type server struct {
	pb.UnimplementedProductCatalogServer // Forward compatibility
}

// 1. Unary Implementation
func (s *server) GetProduct(ctx context.Context, req *pb.GetProductRequest) (*pb.ProductResponse, error) {
	if req.Id == "" {
        // Error mapping to gRPC codes
		return nil, status.Error(codes.InvalidArgument, "ID is required")
	}
	if req.Id == "999" {
		return nil, status.Errorf(codes.NotFound, "Product %s not found", req.Id)
	}

	return &pb.ProductResponse{
		Id:     req.Id,
		Name:   "Mechanical Keyboard",
		Price:  129.99,
		Status: pb.ProductResponse_IN_STOCK,
	}, nil
}

// 2. Server Streaming Implementation
func (s *server) SearchProducts(req *pb.SearchRequest, stream pb.ProductCatalog_SearchProductsServer) error {
	for i := int32(0); i < req.MaxResults; i++ {
		// Context cancellation check
		if err := stream.Context().Err(); err != nil {
			return err
		}

		err := stream.Send(&pb.ProductResponse{
			Id:     fmt.Sprintf("prod-%d", i),
			Name:   fmt.Sprintf("%s Item %d", req.Category, i),
			Price:  10.0 * float64(i),
			Status: pb.ProductResponse_IN_STOCK,
		})
		if err != nil {
			return err
		}
		time.Sleep(500 * time.Millisecond)
	}
	return nil
}

func main() {
	lis, err := net.Listen("tcp", ":50051")
	if err != nil { log.Fatalf("failed to listen: %v", err) }
    
	s := grpc.NewServer()
	pb.RegisterProductCatalogServer(s, &server{})
	reflection.Register(s) // Enable grpcurl support
    
	log.Printf("server listening at %v", lis.Addr())
	if err := s.Serve(lis); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}
```

### 4. Rust

**Project Setup (`Cargo.toml` and `build.rs`):**
Rust uses the `tonic` crate and compiles `.proto` files in `build.rs`.
```toml
[dependencies]
tonic = "0.11"
prost = "0.12"
tokio = { version = "1.0", features = ["macros", "rt-multi-thread"] }
tokio-stream = "0.1"

[build-dependencies]
tonic-build = "0.11"
```
*`build.rs`:*
```rust
fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::compile_protos("proto/catalog.proto")?;
    Ok(())
}
```

**Server Implementation (`src/main.rs`):**
```rust
use tonic::{transport::Server, Request, Response, Status};
use tokio_stream::wrappers::ReceiverStream;
use tokio::sync::mpsc;

// Include the generated code
pub mod catalog {
    tonic::include_proto!("catalog");
}

use catalog::product_catalog_server::{ProductCatalog, ProductCatalogServer};
use catalog::{GetProductRequest, ProductResponse, SearchRequest};
use catalog::product_response::Availability;

#[derive(Default)]
pub struct MyCatalog {}

#[tonic::async_trait]
impl ProductCatalog for MyCatalog {
    // 2. Server Streaming Definition requires an associated type for the stream
    type SearchProductsStream = ReceiverStream<Result<ProductResponse, Status>>;

    // 1. Unary Implementation
    async fn get_product(
        &self,
        request: Request<GetProductRequest>,
    ) -> Result<Response<ProductResponse>, Status> {
        let req = request.into_inner();

        if req.id.is_empty() {
            return Err(Status::invalid_argument("ID is required"));
        }
        if req.id == "999" {
            return Err(Status::not_found(format!("Product {} not found", req.id)));
        }

        let reply = ProductResponse {
            id: req.id,
            name: "Mechanical Keyboard".into(),
            price: 129.99,
            status: Availability::InStock as i32,
        };

        Ok(Response::new(reply))
    }

    // 2. Server Streaming Implementation
    async fn search_products(
        &self,
        request: Request<SearchRequest>,
    ) -> Result<Response<Self::SearchProductsStream>, Status> {
        let req = request.into_inner();
        let (tx, rx) = mpsc::channel(4); // Create an async channel

        tokio::spawn(async move {
            for i in 0..req.max_results {
                let res = ProductResponse {
                    id: format!("prod-{}", i),
                    name: format!("{} Item {}", req.category, i),
                    price: 10.0 * (i as f64),
                    status: Availability::InStock as i32,
                };
                
                // If client disconnects, tx.send fails and we exit the loop
                if tx.send(Ok(res)).await.is_err() {
                    break; 
                }
                tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;
            }
        });

        Ok(Response::new(ReceiverStream::new(rx)))
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let addr = "[::1]:50051".parse()?;
    let catalog = MyCatalog::default();

    println!("CatalogServer listening on {}", addr);
    Server::builder()
        .add_service(ProductCatalogServer::new(catalog))
        .serve(addr)
        .await?;

    Ok(())
}
```

### Critical Observations for C# Developers
1. **Contract as Code:** Notice how we never wrote a `class ProductResponse` by hand in C#, Go, or Rust. We wrote it once in Protobuf, and the build system generated the exact native types for us.
2. **Streaming Mechanics:** C# uses `IServerStreamWriter<T>`, Go uses a custom stream interface with `.Send()`, and Rust (via Tonic) uses asynchronous `mpsc` channels wrapped in a `ReceiverStream`. They all accomplish the exact same HTTP/2 stream multiplexing under the hood.
3. **Status Codes:** Unlike HTTP/1.1 REST where you return an `IActionResult` with a `404`, in gRPC you return (or throw) an error specifically mapped to standard gRPC codes (`codes.NotFound`, `StatusCode.NotFound`). The framework handles translating this to HTTP/2 headers/trailers.
