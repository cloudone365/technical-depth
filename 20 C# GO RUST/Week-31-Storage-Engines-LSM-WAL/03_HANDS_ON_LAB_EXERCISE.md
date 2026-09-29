# Week 31 Hands-On Lab: Crash Recovery & SSTable Compaction

In this lab, your systems engineering team will construct the two most critical operational subsystems of an enterprise storage engine:
1. **Crash Recovery via Write-Ahead Log (WAL)**: Guaranteeing zero data loss and exact state reconstruction after an abrupt kernel termination (`kill -9` / power failure) through binary CRC32 frame validation.
2. **SSTable Compaction via Multi-Way Merge**: Designing a streaming K-way merge engine that merges overlapping SSTables, purges superseded record versions, enforces tombstone lifecycles, and reclaims disk space.

---

## Lab Architecture & Team Schedule

```
+---------------------------------------------------------------------------------------------------+
|                                 5-DAY ENGINEERING LAB SCHEDULE                                    |
+------------------------------------+--------------------------------------------------------------+
| Timeline                           | Engineering Deliverables & Milestones                        |
+------------------------------------+--------------------------------------------------------------+
| Day 1–2: Durability & Recovery     | Implement binary WAL parser with IEEE 802.3 CRC32 framing.    |
| (Go Reference Engine)              | Ingest 100,000 keys, simulate SIGKILL crash, prove 100%      |
|                                    | recovery of committed data, and discard torn writes at EOF.  |
+------------------------------------+--------------------------------------------------------------+
| Day 3–4: Compaction & Merging      | Implement streaming multi-way SSTable merge worker in Rust.  |
| (Rust Systems Implementation)      | Deduplicate keys, respect sequence numbers, manage tombstone |
|                                    | lifecycles, and generate consolidated Level 1 SSTables.      |
+------------------------------------+--------------------------------------------------------------+
| Friday: Mob Review & Defense       | Run crash stress benchmarks, analyze space amplification,   |
| (Team Architecture Defense)        | complete 7-question defense review, and execute sign-off.    |
+------------------------------------+--------------------------------------------------------------+
```

---

## Day 1–2: Write-Ahead Log Crash Recovery (Go Reference Implementation)

### The Failure Scenario
Your key-value storage engine is ingesting 100,000 transactions at peak throughput. At transaction 64,218, the host experiences an abrupt power loss or receives an uncatchable `SIGKILL`. 
- Volatile RAM (the MemTable) is instantly wiped.
- The OS Page Cache writeback daemon was interrupted mid-flight.
- The physical WAL file on disk contains 64,217 fully synchronized transactions, followed by a **torn write** (a partially written frame) at the end of the file.

### Complete Reference Implementation (`recovery_engine.go`)

```go
package main
import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"hash/crc32"
	"io"
	"os"
	"path/filepath"
	"time"
)
const (
	OpPut    byte   = 0x01
	OpDelete byte   = 0x02
	WalMagic uint32 = 0x57414C31 // 'WAL1'
)
// WalFrame represents a decoded write-ahead log record
type WalFrame struct {
	Crc32       uint32
	SequenceNum uint64
	OpType      byte
	Key         []byte
	Value       []byte
}
type WalStorageEngine struct {
	walFile     *os.File
	walPath     string
	currentSeq  uint64
	activeState map[string][]byte
}
func OpenWalEngine(dataDir string) (*WalStorageEngine, error) {
	if err := os.MkdirAll(dataDir, 0755); err != nil {
		return nil, err
	}
	walPath := filepath.Join(dataDir, "commit.wal")
	file, err := os.OpenFile(walPath, os.O_CREATE|os.O_RDWR|os.O_APPEND, 0644)
	if err != nil {
		return nil, err
	}
	engine := &WalStorageEngine{
		walFile:     file,
		walPath:     walPath,
		activeState: make(map[string][]byte),
	}
	// Execute recovery upon initialization before accepting new traffic
	recoveredCount, err := engine.RecoverFromCrash()
	if err != nil {
		_ = file.Close()
		return nil, fmt.Errorf("crash recovery failed: %w", err)
	}
	fmt.Printf("[WAL Recovery] Successfully recovered %d committed transactions\n", recoveredCount)
	return engine, nil
}
// AppendWrite commits a mutation with CRC32 framing and hardware fdatasync
func (e *WalStorageEngine) AppendWrite(op byte, key, value []byte) (uint64, error) {
	e.currentSeq++
	// Binary Framing: [CRC: 4B][Seq: 8B][Op: 1B][KeyLen: 4B][ValLen: 4B][Key][Val]
	payload := new(bytes.Buffer)
	_ = binary.Write(payload, binary.LittleEndian, e.currentSeq)
	_ = payload.WriteByte(op)
	_ = binary.Write(payload, binary.LittleEndian, uint32(len(key)))
	_ = binary.Write(payload, binary.LittleEndian, uint32(len(value)))
	payload.Write(key)
	payload.Write(value)
	checksum := crc32.ChecksumIEEE(payload.Bytes())
	frame := new(bytes.Buffer)
	_ = binary.Write(frame, binary.LittleEndian, checksum)
	frame.Write(payload.Bytes())
	if _, err := e.walFile.Write(frame.Bytes()); err != nil {
		return 0, err
	}
	// Ensure physical persistence on non-volatile media before acknowledging write
	if err := e.walFile.Sync(); err != nil {
		return 0, err
	}
	if op == OpPut {
		e.activeState[string(key)] = value
	} else if op == OpDelete {
		delete(e.activeState, string(key))
	}
	return e.currentSeq, nil
}
// RecoverFromCrash scans the WAL sequentially, validates CRC32 checksums, and truncates torn writes
func (e *WalStorageEngine) RecoverFromCrash() (int, error) {
	stat, err := e.walFile.Stat()
	if err != nil {
		return 0, err
	}
	if stat.Size() == 0 {
		return 0, nil
	}
	if _, err := e.walFile.Seek(0, io.SeekStart); err != nil {
		return 0, err
	}
	recovered := 0
	var lastValidOffset int64 = 0
	for {
		currentOffset, err := e.walFile.Seek(0, io.SeekCurrent)
		if err != nil {
			return recovered, err
		}
		// Read 21-byte frame header: [CRC: 4B][Seq: 8B][Op: 1B][KeyLen: 4B][ValLen: 4B]
		headerBuf := make([]byte, 21)
		_, err = io.ReadFull(e.walFile, headerBuf)
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			// Normal EOF reached or partial frame header at EOF (torn write)
			_ = e.truncateTornWrite(lastValidOffset)
			break
		}
		if err != nil {
			return recovered, err
		}
		expectedCrc := binary.LittleEndian.Uint32(headerBuf[0:4])
		seqNum := binary.LittleEndian.Uint64(headerBuf[4:12])
		opType := headerBuf[12]
		keyLen := binary.LittleEndian.Uint32(headerBuf[13:17])
		valLen := binary.LittleEndian.Uint32(headerBuf[17:21])
		// Read Key and Value payloads
		payloadBytes := make([]byte, keyLen+valLen)
		_, err = io.ReadFull(e.walFile, payloadBytes)
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			// Power cut occurred halfway through writing payload bytes!
			fmt.Printf("[WAL Warning] Torn write detected at offset %d. Truncating corrupt tail.\n", currentOffset)
			_ = e.truncateTornWrite(lastValidOffset)
			break
		}
		if err != nil {
			return recovered, err
		}
		// Recalculate CRC32 over the payload slice
		verifyBuf := new(bytes.Buffer)
		verifyBuf.Write(headerBuf[4:21]) // SeqNum + OpType + KeyLen + ValLen
		verifyBuf.Write(payloadBytes)    // KeyBytes + ValBytes
		if crc32.ChecksumIEEE(verifyBuf.Bytes()) != expectedCrc {
			fmt.Printf("[WAL Warning] CRC32 mismatch at offset %d. Data corruption detected. Discarding.\n", currentOffset)
			_ = e.truncateTornWrite(lastValidOffset)
			break
		}
		// Record is valid: apply mutation to in-memory state
		key := payloadBytes[:keyLen]
		val := payloadBytes[keyLen:]
		if opType == OpPut {
			e.activeState[string(key)] = val
		} else if opType == OpDelete {
			delete(e.activeState, string(key))
		}
		if seqNum > e.currentSeq {
			e.currentSeq = seqNum
		}
		recovered++
		lastValidOffset = currentOffset + 21 + int64(keyLen+valLen)
	}
	// Seek to the end of the clean file boundary for subsequent append operations
	_, err = e.walFile.Seek(0, io.SeekEnd)
	return recovered, err
}
func (e *WalStorageEngine) truncateTornWrite(validSize int64) error {
	if err := e.walFile.Truncate(validSize); err != nil {
		return err
	}
	return e.walFile.Sync()
}
func (e *WalStorageEngine) Close() error {
	return e.walFile.Close()
}
func main() {
	dbDir := filepath.Join(".", "crash_lab_data")
	_ = os.RemoveAll(dbDir)
	fmt.Println("=== STEP 1: Ingesting 100,000 Keys with Durability ===")
	engine, err := OpenWalEngine(dbDir)
	if err != nil {
		panic(err)
	}
	start := time.Now()
	totalKeys := 100000
	for i := 1; i <= totalKeys; i++ {
		k := []byte(fmt.Sprintf("account:user_%06d", i))
		v := []byte(fmt.Sprintf("{\"balance\": %d, \"status\": \"active\"}", i*10))
		if _, err := engine.AppendWrite(OpPut, k, v); err != nil {
			panic(err)
		}
	}
	fmt.Printf("Ingested %d durable records in %v\n", totalKeys, time.Since(start))
	// Simulate torn write: append 12 raw bytes (corrupt partial frame) without syncing
	fmt.Println("=== STEP 2: Simulating Sudden Crash & Torn Write ===")
	corruptBytes := []byte{0xDE, 0xAD, 0xBE, 0xEF, 0x01, 0x00, 0x00, 0x00, 0x05, 0x00, 0x00, 0x00}
	_, _ = engine.walFile.Write(corruptBytes)
	_ = engine.Close() // Simulate sudden termination
	fmt.Println("=== STEP 3: Restarting Storage Engine and Recovering ===")
	recoveryEngine, err := OpenWalEngine(dbDir)
	if err != nil {
		panic(err)
	}
	defer recoveryEngine.Close()
	// Assert 100% recovery
	if len(recoveryEngine.activeState) != totalKeys {
		panic(fmt.Sprintf("Recovery mismatch! Expected %d keys, got %d", totalKeys, len(recoveryEngine.activeState)))
	}
	fmt.Printf("Verification PASSED: Exactly %d keys intact. Torn write discarded safely.\n", len(recoveryEngine.activeState))
}
```

---

## Day 3–4: SSTable Compaction & Multi-Way Merge (Rust Implementation)

### The Architectural Problem
As MemTables flush to disk, multiple Level 0 (L0) SSTables accumulate. A single key (e.g., `account:user_000100`) may exist across multiple SSTables if the account was updated multiple times. Furthermore, deleted keys persist on disk as tombstones.

Without compaction:
1. **Read Latency Degrades**: A point lookup must search every single L0 SSTable sequentially.
2. **Space Leaks**: Overwritten versions and tombstones consume storage indefinitely.

### Rust Concepts You Will Fight in This Lab

#### 1. Streaming Lifetimes & Cursor Ownership
In C#, you would write `Directory.GetFiles()` and open `BinaryReader` instances inside a `List<BinaryReader>`. In Rust, you cannot easily hold mutable references to file readers while advancing an iterator without hitting borrow checker restrictions.
- You must structure each file reader as an independent, self-contained cursor struct (`SstStreamCursor`) that owns its file handle and internally buffers the *current* active key-value pair.

#### 2. Min-Heap Priority Queue & Custom `Ord` Invariants
To merge $K$ sorted SSTables in $O(N \log K)$ time without loading all SSTables into RAM, you must use `std::collections::BinaryHeap`.
- However, Rust's `BinaryHeap` is a **Max-Heap** by default.
- To convert it to a **Min-Heap**, you must implement `Ord` and `PartialOrd` on your cursor struct to reverse comparison (`other.cmp(self)`).
- When two SSTables contain the **identical key**, the tie must be broken by the SSTable's **Sequence Number / Timestamp**. The record with the higher sequence number represents the newer state and must win!

#### 3. The Tombstone Resurrection Trap
When compacting SSTables at higher levels ($L_0$ to $L_1$), you **cannot simply drop a tombstone** just because you merged it with an older record in $L_0$. If an even older version of that key exists down in Level 2 ($L_2$), dropping the tombstone in $L_1$ will cause the obsolete $L_2$ version to suddenly "resurrect" during future queries!
- In this lab, tombstones can only be permanently dropped if we are compacting the bottom-most level of the engine.

### Rust Compaction Starter Skeleton (`src/compactor.rs`)

```rust
use std::cmp::Ordering;
use std::collections::BinaryHeap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};
const MAGIC_NUMBER: u32 = 0x4C534D31;
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct KeyValueRecord {
    pub key: Vec<u8>,
    pub value: Vec<u8>,
    pub is_tombstone: bool,
    pub seq_num: u64,
}
// Cursor managing a streaming iterator over an on-disk SSTable file
pub struct SstStreamCursor {
    file: File,
    file_id: u32,
    current_record: Option<KeyValueRecord>,
    records_remaining: u32,
}
impl SstStreamCursor {
    pub fn open(path: &Path, file_id: u32) -> io::Result<Self> {
        let mut file = File::open(path)?;
        let file_len = file.metadata()?.len();
        if file_len < 16 {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "SSTable too small"));
        }
        // Read 16-byte footer: [IndexOffset: u64][EntryCount: u32][MagicNumber: u32]
        file.seek(SeekFrom::End(-16))?;
        let mut footer = [0u8; 16];
        file.read_exact(&mut footer)?;
        let entry_count = u32::from_le_bytes(footer[8..12].try_into().unwrap());
        let magic = u32::from_le_bytes(footer[12..16].try_into().unwrap());
        if magic != MAGIC_NUMBER {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "Invalid SSTable magic"));
        }
        // Seek back to start of data blocks
        file.seek(SeekFrom::Start(0))?;
        let mut cursor = Self {
            file,
            file_id,
            current_record: None,
            records_remaining: entry_count,
        };
        cursor.advance()?;
        Ok(cursor)
    }
    // Advances to the next sequential record in the SSTable
    pub fn advance(&mut self) -> io::Result<()> {
        if self.records_remaining == 0 {
            self.current_record = None;
            return Ok(());
        }
        let mut klen_buf = [0u8; 4];
        self.file.read_exact(&mut klen_buf)?;
        let klen = u32::from_le_bytes(klen_buf) as usize;
        let mut key = vec![0u8; klen];
        self.file.read_exact(&mut key)?;
        let mut vlen_buf = [0u8; 4];
        self.file.read_exact(&mut vlen_buf)?;
        let vlen = u32::from_le_bytes(vlen_buf) as usize;
        let mut value = vec![0u8; vlen];
        self.file.read_exact(&mut value)?;
        let mut tomb_buf = [0u8; 1];
        self.file.read_exact(&mut tomb_buf)?;
        self.current_record = Some(KeyValueRecord {
            key,
            value,
            is_tombstone: tomb_buf[0] == 1,
            seq_num: self.file_id as u64, // Using file_id as sequence priority
        });
        self.records_remaining -= 1;
        Ok(())
    }
}
// Implement Ord to transform BinaryHeap into a Min-Heap based on Key ordering
impl Ord for SstStreamCursor {
    fn cmp(&self, other: &Self) -> Ordering {
        match (&self.current_record, &other.current_record) {
            (Some(r1), Some(r2)) => {
                // Primary sort: Ascending lexicographical key order (reverse for min-heap)
                match r2.key.cmp(&r1.key) {
                    Ordering::Equal => {
                        // Secondary sort: Higher file_id (newer timestamp) takes precedence!
                        r1.file_id.cmp(&other.file_id)
                    }
                    other_ord => other_ord,
                }
            }
            (Some(_), None) => Ordering::Less,
            (None, Some(_)) => Ordering::Greater,
            (None, None) => Ordering::Equal,
        }
    }
}
impl PartialOrd for SstStreamCursor {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl PartialEq for SstStreamCursor {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}
impl Eq for SstStreamCursor {}
pub struct SstCompactor {
    output_dir: PathBuf,
}
impl SstCompactor {
    pub fn new(output_dir: PathBuf) -> Self {
        Self { output_dir }
    }
    /// Merges multiple SSTables into a single consolidated SSTable file.
    /// Deduplicates keys by retaining only the highest sequence number.
    pub fn compact(&self, input_paths: &[PathBuf]) -> io::Result<PathBuf> {
        let mut min_heap = BinaryHeap::new();
        // 1. Initialize streaming cursors for each input SSTable
        for (idx, path) in input_paths.iter().enumerate() {
            let cursor = SstStreamCursor::open(path, idx as u32)?;
            if cursor.current_record.is_some() {
                min_heap.push(cursor);
            }
        }
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis();
        let compacted_path = self.output_dir.join(format!("sst_compacted_{}.db", timestamp));
        let mut out_file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&compacted_path)?;
        let mut index_entries: Vec<(Vec<u8>, u64, u32)> = Vec::new();
        let mut current_offset: u64 = 0;
        let mut last_emitted_key: Option<Vec<u8>> = None;
        // 2. Multi-Way Merge Loop using Min-Heap
        while let Some(mut cursor) = min_heap.pop() {
            let record = cursor.current_record.clone().unwrap();
            // Check if key is identical to the last emitted key (superseded version!)
            let is_duplicate = match &last_emitted_key {
                Some(prev_key) => prev_key == &record.key,
                None => false,
            };
            if !is_duplicate {
                // If not duplicate and NOT a dead tombstone at bottom level, write to new SSTable
                if !record.is_tombstone {
                    let start = current_offset;
                    out_file.write_all(&(record.key.len() as u32).to_le_bytes())?;
                    out_file.write_all(&record.key)?;
                    out_file.write_all(&(record.value.len() as u32).to_le_bytes())?;
                    out_file.write_all(&record.value)?;
                    out_file.write_all(&[0u8])?; // Not a tombstone
                    let rec_len = (out_file.stream_position()? - start) as u32;
                    index_entries.push((record.key.clone(), start, rec_len));
                    current_offset += rec_len as u64;
                }
                last_emitted_key = Some(record.key);
            }
            // Advance cursor and push back into heap if more records exist
            cursor.advance()?;
            if cursor.current_record.is_some() {
                min_heap.push(cursor);
            }
        }
        // 3. Write Index Block and 16-byte Footer
        let index_offset = current_offset;
        for (k, off, len) in &index_entries {
            out_file.write_all(&(k.len() as u32).to_le_bytes())?;
            out_file.write_all(k)?;
            out_file.write_all(&off.to_le_bytes())?;
            out_file.write_all(&len.to_le_bytes())?;
        }
        out_file.write_all(&index_offset.to_le_bytes())?;
        out_file.write_all(&(index_entries.len() as u32).to_le_bytes())?;
        out_file.write_all(&MAGIC_NUMBER.to_le_bytes())?;
        out_file.sync_data()?;
        // 4. Safely unlink obsolete input SSTables
        for path in input_paths {
            let _ = fs::remove_file(path);
        }
        Ok(compacted_path)
    }
}
```

---

## Friday Mob Review & Engineering Defense

### Benchmark Suite Instructions

Run the crash stress suite and compaction benchmarks:
```bash
# 1. Run WAL Crash Stress Benchmark
go test -v -run TestCrashRecovery -bench=.
# 2. Run Rust Multi-Way Compaction Benchmark
cargo test --release -- --nocapture
```

### Team Metrics Matrix

Fill out this empirical comparison table during the Friday mob session:

| Architectural Metric | Unbuffered I/O (`fsync`) | Group Commit (64KB Buffer) | Direct I/O (`O_DIRECT`) |
| :--- | :--- | :--- | :--- |
| **Write Throughput (ops/sec)** | ~18,500 ops/sec | ~340,000 ops/sec | ~580,000 ops/sec |
| **p99 Ingestion Latency** | 48.2 ms | 3.1 ms | 1.8 ms |
| **Recovery Duration (100k keys)**| 142 ms | 148 ms | 155 ms |
| **Pre-Compaction Footprint** | 128 MB (10 files) | 128 MB (10 files) | 128 MB (10 files) |
| **Post-Compaction Footprint** | 42 MB (1 file) | 42 MB (1 file) | 42 MB (1 file) |
| **Compaction Write Amp (WAF)** | 1.0 (Flushed) | 1.0 (Flushed) | 2.1 (Merged L1) |

---

### Technical Defense Questions & Authoritative Answers

#### 1. Why does `fdatasync` deliver significantly lower latency than `fsync` on journaling filesystems (ext4/XFS)?
*Answer*: `fsync(2)` flushes both modified file data blocks and the filesystem inode metadata (such as modification timestamps, access times, and file attribute flags). On a journaling filesystem like ext4 or XFS, flushing inode metadata forces a synchronous write barrier to the filesystem journal log, triggering two distinct physical disk I/O head movements or NVMe flush cycles. `fdatasync(2)` flushes only dirty data pages, skipping the metadata journal transaction unless the file length changed in a way that prevents data retrieval.

#### 2. What occurs if power fails halfway through writing an 8KB entry, and how does the recovery parser prevent data corruption?
*Answer*: Power failure mid-write produces a torn write. The operating system writes partial sectors to disk. During recovery, our parser attempts to read the length-prefixed payload. If it encounters EOF prematurely, or if the computed IEEE 802.3 CRC32 checksum over the payload fails to match the header's expected CRC, the recovery loop immediately halts. The engine issues `ftruncate()` at the byte offset of the last known intact record, ensuring uncommitted and corrupted transactions are severed deterministically without crashing.

#### 3. Explain the "Tombstone Resurrection" bug and why a compactor cannot simply discard a tombstone at Level 0.
*Answer*: An LSM tree is multi-tiered. If an application updates key $K$ at Level 2, and subsequently issues a `Delete(K)` at Level 0, a tombstone is written to an $L_0$ SSTable. If compaction merges two $L_0$ SSTables and naively discards the tombstone because neither file contained $K$'s prior value, $K$'s historical version in $L_2$ will suddenly become visible again! The tombstone must persist through compaction until it is merged into the bottom-most level ($L_{max}$) containing all historical keys.

#### 4. How does Pipelined Group Commit amortize the physical latency of NVMe storage syncs?
*Answer*: A single NVMe drive requires ~30–50 microseconds to execute a physical flash sync, capping single-threaded throughput at ~20,000 IOPS. Under Group Commit, concurrent client threads register their write payloads into an atomic queue. The first arrival becomes the Leader; subsequent arrivals park their threads. The Leader collects all pending payloads into a single contiguous buffer, executes a single `writev()` followed by one single `fdatasync()`, and notifies all waiting followers. Sync latency is amortized across hundreds of transactions, scaling throughput beyond 500,000 ops/sec.

#### 5. Why do production engines prefer Concurrent SkipLists over Red-Black Trees for MemTables?
*Answer*: In single-threaded benchmarks, balanced binary trees (Red-Black Trees) often match or exceed SkipLists. However, under high concurrency, modifying a Red-Black Tree requires node rotations that lock large subtrees, causing severe thread contention. A SkipList is a probabilistic linked hierarchy that requires zero tree rotations. Inserting a node only modifies forward pointers at adjacent levels, enabling lock-free concurrent insertions via atomic Compare-And-Swap (CAS) instructions.

#### 6. How does an LSM engine prevent Linux File Descriptor exhaustion (`EMFILE`) when thousands of SSTables exist?
*Answer*: As write volume grows, thousands of SSTable files are created on disk. Opening a dedicated file descriptor for every file quickly exceeds the process `ulimit -n` limit (default 1024 on Linux), resulting in `EMFILE` errors. Production engines solve this via an **SSTable File Cache** (an LRU cache of open file descriptors) and by using **Leveled Compaction**, which bounds the total number of files in higher levels by consolidating them into fixed-size 64MB or 128MB non-overlapping runs.

#### 7. Provide the mathematical formula for Write Amplification Factor (WAF) in Leveled Compaction.
*Answer*: In Leveled Compaction (LCS), each level $L_{i+1}$ is $T$ times larger than level $L_i$ (typically $T = 10$). When a file from $L_i$ is compacted down to $L_{i+1}$, its key range overlaps with up to $T$ files in $L_{i+1}$. Therefore, every byte written to $L_i$ is read and rewritten approximately $T$ times at level $L_{i+1}$. For an LSM tree with $L$ levels, the total theoretical write amplification factor is:

$$WAF = 1_{\text{WAL}} + 1_{\text{MemTable Flush}} + \sum_{i=1}^{L} T_i \approx 1 + 1 + 10 \times L$$

For a 4-level database, each byte is written physically to storage approximately 30 to 40 times over its lifetime.

---

## Sign-off Checklist

Every engineering team member must individually verify and sign off on these criteria:

- [ ] **WAL Durability Verification**: `fdatasync()` / `Flush(flushToDisk: true)` is executed on every batch before acknowledging writes to clients.
- [ ] **CRC32 Integrity Validation**: The recovery engine recalculates IEEE 802.3 CRC32 checksums and successfully discards torn writes without unhandled panic or memory fault.
- [ ] **Monotonic LSN Causality**: Sequence numbers monotonically increase, and the compactor preserves the newest sequence number when duplicate keys collide.
- [ ] **Zero-Allocation Slicing**: Binary decoding utilizes contiguous buffer slices without creating unnecessary managed heap allocations in hot loops.
- [ ] **Tombstone Propagation Contract**: Tombstones are preserved during intermediate level merges and only eradicated when reaching the lowest level.
- [ ] **Min-Heap Memory Safety**: Rust compaction uses streaming iterators backed by `BinaryHeap`, ensuring memory usage remains bounded by the number of open SSTables ($O(K)$), rather than the total size of on-disk data.

---

## Stretch Goals for Systems Engineers

1. **Direct I/O & Posix Alignment**: Open the WAL with `O_DIRECT` on Linux, allocate memory aligned to 4096-byte boundaries via `posix_memalign`, and benchmark throughput against kernel page cache writes.
2. **SIMD-Accelerated Bloom Filters**: Implement a block-based Bloom filter in Rust using `std::arch::x86_64` AVX2 vector intrinsics to test 8 hash buckets simultaneously in 1 CPU cycle.
3. **Lock-Free MemTable Swapping**: Implement atomic generation pointer swapping for the active MemTable using `crossbeam-skiplist` and compare p99 latency against standard mutexes under 64-thread write storms.
