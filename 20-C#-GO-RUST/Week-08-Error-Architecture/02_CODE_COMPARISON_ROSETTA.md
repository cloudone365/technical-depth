# Week 08 Rosetta Stone: Production Configuration Loader

This Rosetta Stone demonstrates how to implement a robust, multi-step validation process across C#, Go, and Rust. The scenario is a `ConfigurationLoader` that must:
1. Read a JSON/YAML config file.
2. Parse the contents into a strongly typed struct.
3. Validate domain rules (e.g., ports must be > 1024, connection string must be valid).
4. Verify environmental constraints (e.g., TLS certificate file must exist and have correct permissions).

Crucially, we must preserve the exact root cause of the error while adding domain context at each layer, allowing the top-level caller to log a rich, actionable error message.

## 1. C# Implementation: Exceptions and ExceptionDispatchInfo

In C#, we define custom exception classes and use the `InnerException` property to chain context. We must be careful not to swallow stack traces if we rethrow.

### `ConfigLoader.cs`

```csharp
using System;
using System.IO;
using System.Text.Json;
using System.Runtime.ExceptionServices;
using System.Security;

namespace ConfigSystem
{
    // 1. Define structured exception hierarchy
    public class ConfigurationException : Exception
    {
        public ConfigurationException(string message, Exception innerException) 
            : base(message, innerException) { }
    }

    public class ValidationException : Exception
    {
        public ValidationException(string message) : base(message) { }
    }

    public class AppConfig
    {
        public int Port { get; set; }
        public string DbConnectionString { get; set; }
        public string TlsCertPath { get; set; }
    }

    public class ConfigurationLoader
    {
        public AppConfig Load(string path)
        {
            AppConfig config;
            try
            {
                // Attempt to read the file
                string content = File.ReadAllText(path);
                
                // Attempt to parse JSON
                config = JsonSerializer.Deserialize<AppConfig>(content);
            }
            catch (IOException ex)
            {
                // Wrap infrastructure errors with domain context
                throw new ConfigurationException($"Failed to read configuration file at '{path}'", ex);
            }
            catch (JsonException ex)
            {
                throw new ConfigurationException($"Configuration file '{path}' is not valid JSON", ex);
            }

            // Perform domain validation
            ValidateConfig(config);

            return config;
        }

        private void ValidateConfig(AppConfig config)
        {
            if (config.Port <= 1024)
            {
                // Throwing validation exceptions (Antipattern for high-throughput, but standard for startup)
                throw new ValidationException($"Port must be greater than 1024, got {config.Port}");
            }

            if (string.IsNullOrWhiteSpace(config.DbConnectionString))
            {
                throw new ValidationException("Database connection string cannot be empty");
            }

            // Environmental check
            if (!File.Exists(config.TlsCertPath))
            {
                throw new ValidationException($"TLS certificate not found at '{config.TlsCertPath}'");
            }
        }
    }

    class Program
    {
        static void Main(string[] args)
        {
            var loader = new ConfigurationLoader();
            try
            {
                var config = loader.Load("config.json");
                Console.WriteLine($"Started on port {config.Port}");
            }
            catch (ConfigurationException ex)
            {
                // Top level error reporting
                Console.Error.WriteLine($"FATAL: Application failed to start.");
                Console.Error.WriteLine(ex.Message);
                
                // Inspecting the inner exception for the root cause
                if (ex.InnerException != null)
                {
                    Console.Error.WriteLine($"Root cause: {ex.InnerException.Message}");
                }
                Environment.Exit(1);
            }
            catch (ValidationException ex)
            {
                Console.Error.WriteLine($"FATAL: Invalid configuration. {ex.Message}");
                Environment.Exit(1);
            }
        }
    }
}
```

## 2. Go Implementation: Wrapped Errors and Typed Sentinels

In Go, we use `fmt.Errorf` with the `%w` verb to wrap errors, maintaining a chain that can be inspected with `errors.Is` or `errors.As`. We define custom error structs for validation failures to allow the caller to extract specific fields.

### `main.go`

```go
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
)

// 1. Custom Error Types for Domain Specifics
type ValidationError struct {
	Field   string
	Message string
}

// Implement the error interface
func (v *ValidationError) Error() string {
	return fmt.Sprintf("validation failed on field '%s': %s", v.Field, v.Message)
}

// 2. Sentinel Errors
var ErrConfigNotFound = errors.New("configuration file not found")

type AppConfig struct {
	Port               int    `json:"port"`
	DbConnectionString string `json:"db_connection_string"`
	TlsCertPath        string `json:"tls_cert_path"`
}

func LoadConfig(path string) (*AppConfig, error) {
	// Attempt to read the file
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			// Wrap our sentinel error so callers can check for it
			return nil, fmt.Errorf("LoadConfig: %w: %s", ErrConfigNotFound, path)
		}
		// Wrap standard I/O errors
		return nil, fmt.Errorf("LoadConfig failed to read file: %w", err)
	}

	var config AppConfig
	if err := json.Unmarshal(data, &config); err != nil {
		return nil, fmt.Errorf("LoadConfig failed to parse JSON: %w", err)
	}

	// Validate
	if err := validateConfig(&config); err != nil {
		return nil, fmt.Errorf("LoadConfig validation failed: %w", err)
	}

	return &config, nil
}

func validateConfig(config *AppConfig) error {
	if config.Port <= 1024 {
		// Return custom error struct
		return &ValidationError{
			Field:   "Port",
			Message: fmt.Sprintf("must be > 1024, got %d", config.Port),
		}
	}

	if config.DbConnectionString == "" {
		return &ValidationError{
			Field:   "DbConnectionString",
			Message: "cannot be empty",
		}
	}

	if _, err := os.Stat(config.TlsCertPath); err != nil {
		if os.IsNotExist(err) {
			return &ValidationError{
				Field:   "TlsCertPath",
				Message: fmt.Sprintf("file not found at '%s'", config.TlsCertPath),
			}
		}
		return fmt.Errorf("failed to access TLS cert: %w", err)
	}

	return nil
}

func main() {
	config, err := LoadConfig("config.json")
	if err != nil {
		fmt.Fprintf(os.Stderr, "FATAL: Application failed to start.\n")
		
		// 3. Inspecting the error chain
		
		// Check for specific sentinel error
		if errors.Is(err, ErrConfigNotFound) {
			fmt.Fprintf(os.Stderr, "Hint: Ensure the config file is in the working directory.\n")
		}

		// Extract custom error data
		var valErr *ValidationError
		if errors.As(err, &valErr) {
			fmt.Fprintf(os.Stderr, "Configuration Fix Required -> Field: %s, Issue: %s\n", valErr.Field, valErr.Message)
		} else {
			// Print full error chain
			fmt.Fprintf(os.Stderr, "Details: %v\n", err)
		}
		os.Exit(1)
	}

	fmt.Printf("Started on port %d\n", config.Port)
}
```

## 3. Rust Implementation: `thiserror` for Domains, `anyhow` for Apps

In Rust, we cleanly separate library/domain errors (which should be rigidly typed enums using `thiserror`) from application orchestration errors (where we just want easily chainable context using `anyhow`).

### `Cargo.toml`
```toml
[package]
name = "config_loader"
version = "0.1.0"
edition = "2021"

[dependencies]
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
thiserror = "1.0"
anyhow = "1.0"
```

### `src/main.rs`

```rust
use anyhow::{Context, Result as AnyhowResult};
use serde::Deserialize;
use std::fs;
use std::path::Path;
use thiserror::Error;

// 1. Define strongly typed domain errors using `thiserror`
// This generates the boilerplate for std::error::Error and Display
#[derive(Error, Debug)]
pub enum ConfigValidationError {
    #[error("Port must be > 1024, got {0}")]
    InvalidPort(u16),
    #[error("Database connection string cannot be empty")]
    EmptyConnectionString,
    #[error("TLS certificate not found at '{0}'")]
    TlsCertNotFound(String),
}

#[derive(Deserialize, Debug)]
pub struct AppConfig {
    pub port: u16,
    pub db_connection_string: String,
    pub tls_cert_path: String,
}

impl AppConfig {
    // Domain validation returns our specific thiserror enum
    pub fn validate(&self) -> Result<(), ConfigValidationError> {
        if self.port <= 1024 {
            return Err(ConfigValidationError::InvalidPort(self.port));
        }

        if self.db_connection_string.trim().is_empty() {
            return Err(ConfigValidationError::EmptyConnectionString);
        }

        if !Path::new(&self.tls_cert_path).exists() {
            return Err(ConfigValidationError::TlsCertNotFound(
                self.tls_cert_path.clone(),
            ));
        }

        Ok(())
    }
}

// The outer loader returns anyhow::Result to easily capture heterogeneous errors
// (I/O errors, JSON errors, Validation errors) with attached context.
pub fn load_config<P: AsRef<Path>>(path: P) -> AnyhowResult<AppConfig> {
    let path_ref = path.as_ref();
    
    // Read file, wrap std::io::Error with anyhow context
    let content = fs::read_to_string(path_ref)
        .with_context(|| format!("Failed to read configuration file at {:?}", path_ref))?;

    // Parse JSON, wrap serde_json::Error with context
    let config: AppConfig = serde_json::from_str(&content)
        .with_context(|| format!("File {:?} contains invalid JSON", path_ref))?;

    // Validate domain rules. The ? operator automatically converts 
    // ConfigValidationError into anyhow::Error because anyhow supports anything 
    // implementing std::error::Error. We add context to show *what* we were doing.
    config
        .validate()
        .context("Domain validation failed after successfully parsing config")?;

    Ok(config)
}

fn main() {
    match load_config("config.json") {
        Ok(config) => println!("Started on port {}", config.port),
        Err(e) => {
            // anyhow::Error's debug format (`{:?}`) prints the entire chain of errors!
            // Output looks like:
            // Error: Domain validation failed after successfully parsing config
            // 
            // Caused by:
            //     Port must be > 1024, got 80
            eprintln!("FATAL: Application failed to start.");
            eprintln!("{:?}", e);
            
            // You can also downcast to inspect specific errors if needed
            if let Some(validation_err) = e.downcast_ref::<ConfigValidationError>() {
                eprintln!("Hint: Please fix the domain rule violation.");
            }
            
            std::process::exit(1);
        }
    }
}
```

## Critical Observations for C# Developers

1. **Where did `try/catch` go?** Notice in both Go and Rust, there is no separate block of code for handling exceptions vs running logic. The error handling is woven directly into the linear flow of the function. 
2. **Invisible vs Visible Return:** In the C# example, `string content = File.ReadAllText(path);` hides the fact that it can fail. You must consult documentation to know it throws `IOException`. In Rust, `fs::read_to_string` returns a `Result`, forcing you to write `?` to handle the failure case. The compiler guarantees you cannot forget.
3. **The Power of `anyhow::Context`:** Rust's `.with_context(|| ...)` is functionally identical to Go's `fmt.Errorf("... %w", err)` and C#'s `InnerException`. However, Rust's `anyhow` automatically formats the entire chain beautifully when printed with `{:?}`, eliminating the need to manually write loop logic to unwrap and print inner exceptions as is often required in C#.
4. **Validation Logic Signatures:** In C#, `ValidateConfig(AppConfig)` returns `void`. It signals failure strictly through throwing. In Go it returns `error`, and in Rust it returns `Result<(), ConfigValidationError>`. In the systems languages, the signature itself documents exactly what can go wrong.
