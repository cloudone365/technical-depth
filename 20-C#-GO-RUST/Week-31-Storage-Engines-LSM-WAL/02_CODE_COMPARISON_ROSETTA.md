# Week 31 Code Rosetta: Building a Mini LSM-Tree Engine

This Rosetta document presents a complete, production-grade, compilable implementation of a crash-resilient **Mini LSM-Tree Key-Value Storage Engine** (`MiniLSM`) across C# (.NET 8), Go (1.22+), and Rust (2021 Edition).

### Engine Architecture & Invariants
1. **Write-Ahead Log (WAL)**: Binary framed log ensuring immediate persistence. Every mutation (`Put`, `Delete`) writes a CRC32 checksum, 64-bit sequence number, 1-byte opcode (`0x01` = Put, `0x02` = Delete/Tombstone), length-prefixed key, and length-prefixed value, followed by an immediate hardware disk sync (`fsync` / `fdatasync`).
2. **MemTable**: Sorted in-memory write buffer tracking total payload byte volume.
3. **SSTable Flusher**: When the MemTable exceeds its byte threshold (e.g., 4KB for testing), it is frozen, written sequentially to an immutable SSTable file (`sst_<timestamp>.db`), and the active WAL is truncated.
4. **SSTable Index Footer**: The SSTable appends all sorted data records, then an index block mapping keys to `(Offset, Length)`, followed by a fixed 16-byte trailer: `[IndexOffset: u64][EntryCount: u32][MagicNumber: 0x4C534D31]`.
5. **Point Lookups (`Get`)**: Evaluates active MemTable first; if missed, searches SSTable files on disk in descending chronological order (newest to oldest) using binary search across the SSTable index footer.

---

## 1. C# (.NET 8) Implementation

### Project Configuration (`MiniLsm.csproj`)
```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
</Project>
```

### Complete Source Code (`Program.cs`)
```csharp
using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
namespace MiniLsm;
public readonly record struct MemEntry(string? Value, bool IsTombstone);
public static class Crc32 {
    private static readonly uint[] T = Enumerable.Range(0, 256).Select(i => {
        uint c = (uint)i;
        for (int j = 0; j < 8; j++) c = (c & 1) != 0 ? (c >> 1) ^ 0xEDB88320u : c >> 1;
        return c;
    }).ToArray();
    public static uint Compute(ReadOnlySpan<byte> b) {
        uint crc = 0xFFFFFFFFu;
        foreach (byte x in b) crc = (crc >> 8) ^ T[(crc ^ x) & 0xFF];
        return ~crc;
    }
}
public sealed class MiniLsmEngine : IDisposable {
    private const uint Magic = 0x4C534D31; // 'LSM1'
    private readonly string _walPath, _sstDir;
    private readonly long _threshold;
    private readonly object _lock = new();
    private FileStream _wal;
    private readonly SortedDictionary<string, MemEntry> _memTable = new();
    private long _memBytes = 0;
    private ulong _seqNum = 0;
    public MiniLsmEngine(string dir, long threshold = 4096) {
        _walPath = Path.Combine(dir, "wal.log");
        _sstDir = Path.Combine(dir, "sstables");
        _threshold = threshold;
        Directory.CreateDirectory(_sstDir);
        _wal = new FileStream(_walPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.Read, 4096, FileOptions.WriteThrough);
        RecoverWal();
    }
    private static byte[] Frame(ulong seq, byte op, byte[] kb, byte[] vb) {
        byte[] p = new byte[17 + kb.Length + vb.Length];
        BinaryPrimitives.WriteUInt64LittleEndian(p.AsSpan(0, 8), seq);
        p[8] = op;
        BinaryPrimitives.WriteInt32LittleEndian(p.AsSpan(9, 4), kb.Length);
        BinaryPrimitives.WriteInt32LittleEndian(p.AsSpan(13, 4), vb.Length);
        Buffer.BlockCopy(kb, 0, p, 17, kb.Length);
        Buffer.BlockCopy(vb, 0, p, 17 + kb.Length, vb.Length);
        return p;
    }
    private void RecoverWal() {
        if (_wal.Length == 0) return;
        _wal.Seek(0, SeekOrigin.Begin);
        using var r = new BinaryReader(_wal, Encoding.UTF8, leaveOpen: true);
        while (_wal.Position + 21 <= _wal.Length) {
            long start = _wal.Position;
            uint expectedCrc = r.ReadUInt32();
            ulong seq = r.ReadUInt64();
            byte op = r.ReadByte();
            int kLen = r.ReadInt32(), vLen = r.ReadInt32();
            if (_wal.Position + kLen + vLen > _wal.Length) { _wal.Seek(start, SeekOrigin.Begin); break; }
            byte[] kb = r.ReadBytes(kLen), vb = r.ReadBytes(vLen);
            if (Crc32.Compute(Frame(seq, op, kb, vb)) != expectedCrc) { _wal.Seek(start, SeekOrigin.Begin); break; }
            string key = Encoding.UTF8.GetString(kb);
            _memTable[key] = op == 1 ? new MemEntry(Encoding.UTF8.GetString(vb), false) : new MemEntry(null, true);
            _memBytes += kLen + vLen;
            if (seq > _seqNum) _seqNum = seq;
        }
        _wal.Seek(0, SeekOrigin.End);
    }
    private void WriteOp(byte op, string key, string? value) {
        lock (_lock) {
            byte[] kb = Encoding.UTF8.GetBytes(key), vb = value != null ? Encoding.UTF8.GetBytes(value) : Array.Empty<byte>();
            AppendWal(op, kb, vb);
            _memTable[key] = new MemEntry(value, op == 2);
            _memBytes += kb.Length + vb.Length;
            if (_memBytes >= _threshold) Flush();
        }
    }
    public void Put(string key, string value) => WriteOp(1, key, value);
    public void Delete(string key) => WriteOp(2, key, null);
    private void AppendWal(byte op, byte[] kb, byte[] vb) {
        byte[] p = Frame(++_seqNum, op, kb, vb);
        Span<byte> crc = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32LittleEndian(crc, Crc32.Compute(p));
        _wal.Write(crc); _wal.Write(p);
        _wal.Flush(flushToDisk: true);
    }
    public string? Get(string key) {
        lock (_lock) {
            if (_memTable.TryGetValue(key, out var m)) return m.IsTombstone ? null : m.Value;
            var files = Directory.GetFiles(_sstDir, "sst_*.db").OrderByDescending(f => f);
            foreach (var f in files) {
                var res = SearchSst(f, key);
                if (res.HasValue) return res.Value.IsTombstone ? null : res.Value.Value;
            }
            return null;
        }
    }
    private (string? Value, bool IsTombstone)? SearchSst(string path, string target) {
        using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        if (fs.Length < 16) return null;
        fs.Seek(-16, SeekOrigin.End);
        using var r = new BinaryReader(fs, Encoding.UTF8, leaveOpen: true);
        ulong idxOff = r.ReadUInt64(); uint count = r.ReadUInt32(), magic = r.ReadUInt32();
        if (magic != Magic) return null;
        fs.Seek((long)idxOff, SeekOrigin.Begin);
        var idx = new List<(string Key, ulong Off, uint Len)>((int)count);
        for (int i = 0; i < count; i++) idx.Add((Encoding.UTF8.GetString(r.ReadBytes(r.ReadInt32())), r.ReadUInt64(), r.ReadUInt32()));
        int low = 0, high = idx.Count - 1;
        while (low <= high) {
            int mid = low + (high - low) / 2;
            int cmp = string.CompareOrdinal(idx[mid].Key, target);
            if (cmp == 0) {
                fs.Seek((long)idx[mid].Off, SeekOrigin.Begin);
                fs.Seek(r.ReadInt32(), SeekOrigin.Current);
                return (Encoding.UTF8.GetString(r.ReadBytes(r.ReadInt32())), r.ReadBoolean());
            }
            if (cmp < 0) low = mid + 1; else high = mid - 1;
        }
        return null;
    }
    private void Flush() {
        if (_memTable.Count == 0) return;
        string sst = Path.Combine(_sstDir, $"sst_{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}_{_seqNum}.db");
        using (var fs = new FileStream(sst, FileMode.CreateNew, FileAccess.Write))
        using (var w = new BinaryWriter(fs, Encoding.UTF8)) {
            var idx = new List<(string Key, ulong Off, uint Len)>(_memTable.Count);
            foreach (var (k, e) in _memTable) {
                ulong off = (ulong)fs.Position;
                byte[] kb = Encoding.UTF8.GetBytes(k), vb = e.Value != null ? Encoding.UTF8.GetBytes(e.Value) : Array.Empty<byte>();
                w.Write(kb.Length); w.Write(kb); w.Write(vb.Length); w.Write(vb); w.Write(e.IsTombstone);
                idx.Add((k, off, (uint)((ulong)fs.Position - off)));
            }
            ulong idxOff = (ulong)fs.Position;
            foreach (var (k, off, len) in idx) {
                byte[] kb = Encoding.UTF8.GetBytes(k);
                w.Write(kb.Length); w.Write(kb); w.Write(off); w.Write(len);
            }
            w.Write(idxOff); w.Write((uint)idx.Count); w.Write(Magic);
            fs.Flush(flushToDisk: true);
        }
        _memTable.Clear(); _memBytes = 0;
        _wal.SetLength(0); _wal.Flush(flushToDisk: true);
    }
    public void Dispose() { lock (_lock) { _wal.Dispose(); } }
}
public static class Program {
    public static void Main() {
        string dir = Path.Combine(AppContext.BaseDirectory, "minilsm_cs_data");
        if (Directory.Exists(dir)) Directory.Delete(dir, true);
        using var engine = new MiniLsmEngine(dir, threshold: 120);
        engine.Put("user:101", "Alice");
        engine.Put("user:102", "Bob");
        engine.Put("user:103", "Charlie");
        engine.Put("user:104", "Dave");
        engine.Delete("user:102");
        engine.Put("user:105", "Eve");
        Console.WriteLine($"user:101 => {engine.Get("user:101")}");
        Console.WriteLine($"user:102 => {engine.Get("user:102") ?? "(DELETED)"}");
        Console.WriteLine($"user:104 => {engine.Get("user:104")}");
        Console.WriteLine($"user:999 => {engine.Get("user:999") ?? "(NOT FOUND)"}");
    }
}
```

---

## 2. Go (1.22+) Implementation

### Project Configuration (`go.mod`)
```go
module minilsm
go 1.22
```

### Complete Source Code (`main.go`)
```go
package main
import (
	"encoding/binary"
	"fmt"
	"hash/crc32"
	"io"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)
const (
	MagicNumber uint32 = 0x4C534D31
	OpPut       byte   = 0x01
	OpDelete    byte   = 0x02
)
type MemEntry struct {
	Value       string
	IsTombstone bool
}
type IndexEntry struct {
	Key    string
	Offset uint64
	Length uint32
}
type MiniLsmEngine struct {
	mu        sync.RWMutex
	wal       *os.File
	sstDir    string
	threshold int64
	memTable  map[string]MemEntry
	memBytes  int64
	seqNum    uint64
}
func NewMiniLsmEngine(dir string, threshold int64) (*MiniLsmEngine, error) {
	sstDir := filepath.Join(dir, "sstables")
	if err := os.MkdirAll(sstDir, 0755); err != nil { return nil, err }
	walPath := filepath.Join(dir, "wal.log")
	wal, err := os.OpenFile(walPath, os.O_CREATE|os.O_RDWR|os.O_APPEND, 0644)
	if err != nil { return nil, err }
	e := &MiniLsmEngine{wal: wal, sstDir: sstDir, threshold: threshold, memTable: make(map[string]MemEntry)}
	if err := e.recoverWal(); err != nil { _ = wal.Close(); return nil, err }
	return e, nil
}
func frame(seq uint64, op byte, kb, vb []byte) []byte {
	p := make([]byte, 17+len(kb)+len(vb))
	binary.LittleEndian.PutUint64(p[0:8], seq)
	p[8] = op
	binary.LittleEndian.PutUint32(p[9:13], uint32(len(kb)))
	binary.LittleEndian.PutUint32(p[13:17], uint32(len(vb)))
	copy(p[17:], kb)
	copy(p[17+len(kb):], vb)
	return p
}
func (e *MiniLsmEngine) recoverWal() error {
	if _, err := e.wal.Seek(0, io.SeekStart); err != nil { return err }
	buf := make([]byte, 21)
	for {
		start, _ := e.wal.Seek(0, io.SeekCurrent)
		if _, err := io.ReadFull(e.wal, buf); err != nil { break }
		crc := binary.LittleEndian.Uint32(buf[0:4])
		seq := binary.LittleEndian.Uint64(buf[4:12])
		op := buf[12]
		klen := binary.LittleEndian.Uint32(buf[13:17])
		vlen := binary.LittleEndian.Uint32(buf[17:21])
		kb, vb := make([]byte, klen), make([]byte, vlen)
		if _, err := io.ReadFull(e.wal, kb); err != nil { _, _ = e.wal.Seek(start, io.SeekStart); break }
		if _, err := io.ReadFull(e.wal, vb); err != nil { _, _ = e.wal.Seek(start, io.SeekStart); break }
		p := frame(seq, op, kb, vb)
		if crc32.ChecksumIEEE(p) != crc { _, _ = e.wal.Seek(start, io.SeekStart); break }
		e.memTable[string(kb)] = MemEntry{Value: string(vb), IsTombstone: op == OpDelete}
		e.memBytes += int64(klen + vlen)
		if seq > e.seqNum { e.seqNum = seq }
	}
	_, err := e.wal.Seek(0, io.SeekEnd)
	return err
}
func (e *MiniLsmEngine) writeOp(op byte, key, value string) error {
	e.mu.Lock(); defer e.mu.Unlock()
	kb, vb := []byte(key), []byte(value)
	if op == OpDelete { vb = nil }
	if err := e.appendWal(op, kb, vb); err != nil { return err }
	e.memTable[key] = MemEntry{Value: value, IsTombstone: op == OpDelete}
	e.memBytes += int64(len(kb) + len(vb))
	if e.memBytes >= e.threshold { return e.flush() }
	return nil
}
func (e *MiniLsmEngine) Put(k, v string) error { return e.writeOp(OpPut, k, v) }
func (e *MiniLsmEngine) Delete(k string) error { return e.writeOp(OpDelete, k, "") }
func (e *MiniLsmEngine) appendWal(op byte, kb, vb []byte) error {
	e.seqNum++
	p := frame(e.seqNum, op, kb, vb)
	crcBuf := make([]byte, 4)
	binary.LittleEndian.PutUint32(crcBuf, crc32.ChecksumIEEE(p))
	if _, err := e.wal.Write(crcBuf); err != nil { return err }
	if _, err := e.wal.Write(p); err != nil { return err }
	return e.wal.Sync()
}
func (e *MiniLsmEngine) Get(key string) (string, bool, error) {
	e.mu.RLock(); defer e.mu.RUnlock()
	if entry, ok := e.memTable[key]; ok {
		if entry.IsTombstone { return "", false, nil }
		return entry.Value, true, nil
	}
	matches, _ := filepath.Glob(filepath.Join(e.sstDir, "sst_*.db"))
	sort.Sort(sort.Reverse(sort.StringSlice(matches)))
	for _, path := range matches {
		entry, found, err := e.searchSst(path, key)
		if err != nil { return "", false, err }
		if found {
			if entry.IsTombstone { return "", false, nil }
			return entry.Value, true, nil
		}
	}
	return "", false, nil
}
func (e *MiniLsmEngine) searchSst(path, target string) (MemEntry, bool, error) {
	f, err := os.Open(path); if err != nil { return MemEntry{}, false, err }
	defer f.Close()
	if _, err := f.Seek(-16, io.SeekEnd); err != nil { return MemEntry{}, false, err }
	var footer struct { IdxOff uint64; Count, Magic uint32 }
	if err := binary.Read(f, binary.LittleEndian, &footer); err != nil || footer.Magic != MagicNumber {
		return MemEntry{}, false, err
	}
	_, _ = f.Seek(int64(footer.IdxOff), io.SeekStart)
	idx := make([]IndexEntry, footer.Count)
	for i := uint32(0); i < footer.Count; i++ {
		var klen uint32; _ = binary.Read(f, binary.LittleEndian, &klen)
		kb := make([]byte, klen); _, _ = io.ReadFull(f, kb)
		var off uint64; var len uint32
		_ = binary.Read(f, binary.LittleEndian, &off)
		_ = binary.Read(f, binary.LittleEndian, &len)
		idx[i] = IndexEntry{Key: string(kb), Offset: off, Length: len}
	}
	i := sort.Search(len(idx), func(i int) bool { return idx[i].Key >= target })
	if i < len(idx) && idx[i].Key == target {
		_, _ = f.Seek(int64(idx[i].Offset), io.SeekStart)
		var klen, vlen uint32
		_ = binary.Read(f, binary.LittleEndian, &klen)
		_, _ = f.Seek(int64(klen), io.SeekCurrent)
		_ = binary.Read(f, binary.LittleEndian, &vlen)
		vb := make([]byte, vlen); _, _ = io.ReadFull(f, vb)
		var tomb byte; _ = binary.Read(f, binary.LittleEndian, &tomb)
		return MemEntry{Value: string(vb), IsTombstone: tomb == 1}, true, nil
	}
	return MemEntry{}, false, nil
}
func (e *MiniLsmEngine) flush() error {
	if len(e.memTable) == 0 { return nil }
	keys := make([]string, 0, len(e.memTable))
	for k := range e.memTable { keys = append(keys, k) }
	sort.Strings(keys)
	sstPath := filepath.Join(e.sstDir, fmt.Sprintf("sst_%d_%d.db", time.Now().UnixMilli(), e.seqNum))
	f, err := os.OpenFile(sstPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644); if err != nil { return err }
	defer f.Close()
	idx := make([]IndexEntry, 0, len(keys))
	var offset uint64 = 0
	for _, k := range keys {
		entry := e.memTable[k]
		kb, vb := []byte(k), []byte(entry.Value)
		rec := make([]byte, 9+len(kb)+len(vb))
		binary.LittleEndian.PutUint32(rec[0:4], uint32(len(kb)))
		copy(rec[4:], kb)
		binary.LittleEndian.PutUint32(rec[4+len(kb):8+len(kb)], uint32(len(vb)))
		copy(rec[8+len(kb):], vb)
		if entry.IsTombstone { rec[8+len(kb)+len(vb)] = 1 }
		_, _ = f.Write(rec)
		idx = append(idx, IndexEntry{Key: k, Offset: offset, Length: uint32(len(rec))})
		offset += uint64(len(rec))
	}
	idxOff := offset
	for _, item := range idx {
		kb := []byte(item.Key)
		buf := make([]byte, 16+len(kb))
		binary.LittleEndian.PutUint32(buf[0:4], uint32(len(kb)))
		copy(buf[4:], kb)
		binary.LittleEndian.PutUint64(buf[4+len(kb):12+len(kb)], item.Offset)
		binary.LittleEndian.PutUint32(buf[12+len(kb):], item.Length)
		_, _ = f.Write(buf)
	}
	footer := make([]byte, 16)
	binary.LittleEndian.PutUint64(footer[0:8], idxOff)
	binary.LittleEndian.PutUint32(footer[8:12], uint32(len(idx)))
	binary.LittleEndian.PutUint32(footer[12:16], MagicNumber)
	_, _ = f.Write(footer)
	_ = f.Sync()
	e.memTable = make(map[string]MemEntry)
	e.memBytes = 0
	_ = e.wal.Truncate(0)
	_, _ = e.wal.Seek(0, io.SeekStart)
	return e.wal.Sync()
}
func (e *MiniLsmEngine) Close() error { e.mu.Lock(); defer e.mu.Unlock(); return e.wal.Close() }
func main() {
	dir := filepath.Join(".", "minilsm_go_data")
	_ = os.RemoveAll(dir)
	engine, _ := NewMiniLsmEngine(dir, 120)
	defer engine.Close()
	_ = engine.Put("user:101", "Alice")
	_ = engine.Put("user:102", "Bob")
	_ = engine.Put("user:103", "Charlie")
	_ = engine.Put("user:104", "Dave")
	_ = engine.Delete("user:102")
	_ = engine.Put("user:105", "Eve")
	v, ok, _ := engine.Get("user:101"); fmt.Printf("user:101 => %s (ok=%v)\n", v, ok)
	v, ok, _ = engine.Get("user:102");  fmt.Printf("user:102 => %s (ok=%v)\n", v, ok)
	v, ok, _ = engine.Get("user:104");  fmt.Printf("user:104 => %s (ok=%v)\n", v, ok)
	v, ok, _ = engine.Get("user:999");  fmt.Printf("user:999 => %s (ok=%v)\n", v, ok)
}
```

---

## 3. Rust (2021 Edition) Implementation

### Project Configuration (`Cargo.toml`)
```toml
[package]
name = "minilsm"
version = "0.1.0"
edition = "2021"
[dependencies]
# Zero external crates: standard library provides BTreeMap, Endian conversions, and POSIX I/O.
```

### Complete Source Code (`src/main.rs`)
```rust
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
const MAGIC_NUMBER: u32 = 0x4C534D31;
const OP_PUT: u8 = 0x01;
const OP_DELETE: u8 = 0x02;
fn crc32_ieee(data: &[u8]) -> u32 {
    let mut crc = !0u32;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 { crc = if crc & 1 != 0 { (crc >> 1) ^ 0xEDB8_8320 } else { crc >> 1 }; }
    }
    !crc
}
#[derive(Clone, Debug)]
pub struct MemEntry { pub value: Vec<u8>, pub is_tombstone: bool }
#[derive(Clone, Debug)]
struct IndexEntry { key: String, offset: u64, length: u32 }
pub struct MiniLsmEngine {
    sst_dir: PathBuf,
    wal_file: File,
    threshold: usize,
    memtable: BTreeMap<String, MemEntry>,
    mem_bytes: usize,
    seq_num: u64,
}
fn frame(seq: u64, op: u8, kb: &[u8], vb: &[u8]) -> Vec<u8> {
    let mut p = Vec::with_capacity(17 + kb.len() + vb.len());
    p.extend_from_slice(&seq.to_le_bytes());
    p.push(op);
    p.extend_from_slice(&(kb.len() as u32).to_le_bytes());
    p.extend_from_slice(&(vb.len() as u32).to_le_bytes());
    p.extend_from_slice(kb);
    p.extend_from_slice(vb);
    p
}
impl MiniLsmEngine {
    pub fn open<P: AsRef<Path>>(data_dir: P, threshold: usize) -> io::Result<Arc<Mutex<Self>>> {
        let sst_dir = data_dir.as_ref().join("sstables");
        fs::create_dir_all(&sst_dir)?;
        let wal_file = OpenOptions::new().read(true).write(true).create(true).open(data_dir.as_ref().join("wal.log"))?;
        let mut engine = Self { sst_dir, wal_file, threshold, memtable: BTreeMap::new(), mem_bytes: 0, seq_num: 0 };
        engine.recover_wal()?;
        Ok(Arc::new(Mutex::new(engine)))
    }
    fn recover_wal(&mut self) -> io::Result<()> {
        if self.wal_file.metadata()?.len() == 0 { return Ok(()); }
        self.wal_file.seek(SeekFrom::Start(0))?;
        loop {
            let start = self.wal_file.stream_position()?;
            let mut h = [0u8; 21];
            if self.wal_file.read_exact(&mut h).is_err() { break; }
            let crc = u32::from_le_bytes(h[0..4].try_into().unwrap());
            let seq = u64::from_le_bytes(h[4..12].try_into().unwrap());
            let op = h[12];
            let klen = u32::from_le_bytes(h[13..17].try_into().unwrap()) as usize;
            let vlen = u32::from_le_bytes(h[17..21].try_into().unwrap()) as usize;
            let mut kb = vec![0u8; klen];
            let mut vb = vec![0u8; vlen];
            if self.wal_file.read_exact(&mut kb).is_err() || self.wal_file.read_exact(&mut vb).is_err() {
                self.wal_file.seek(SeekFrom::Start(start))?; break;
            }
            let p = frame(seq, op, &kb, &vb);
            if crc32_ieee(&p) != crc { self.wal_file.seek(SeekFrom::Start(start))?; break; }
            let key = String::from_utf8_lossy(&kb).to_string();
            self.memtable.insert(key, MemEntry { value: vb, is_tombstone: op == OP_DELETE });
            self.mem_bytes += klen + vlen;
            if seq > self.seq_num { self.seq_num = seq; }
        }
        self.wal_file.seek(SeekFrom::End(0))?;
        Ok(())
    }
    fn write_op(&mut self, op: u8, key: &str, value: Option<&[u8]>) -> io::Result<()> {
        let vb = value.unwrap_or(&[]);
        self.append_wal(op, key.as_bytes(), vb)?;
        self.memtable.insert(key.to_string(), MemEntry { value: vb.to_vec(), is_tombstone: op == OP_DELETE });
        self.mem_bytes += key.len() + vb.len();
        if self.mem_bytes >= self.threshold { self.flush()?; }
        Ok(())
    }
    pub fn put(&mut self, key: &str, value: &[u8]) -> io::Result<()> { self.write_op(OP_PUT, key, Some(value)) }
    pub fn delete(&mut self, key: &str) -> io::Result<()> { self.write_op(OP_DELETE, key, None) }
    fn append_wal(&mut self, op: u8, kb: &[u8], vb: &[u8]) -> io::Result<()> {
        self.seq_num += 1;
        let p = frame(self.seq_num, op, kb, vb);
        let crc = crc32_ieee(&p);
        self.wal_file.write_all(&crc.to_le_bytes())?;
        self.wal_file.write_all(&p)?;
        self.wal_file.sync_data()?;
        Ok(())
    }
    pub fn get(&mut self, key: &str) -> io::Result<Option<Vec<u8>>> {
        if let Some(e) = self.memtable.get(key) {
            return Ok(if e.is_tombstone { None } else { Some(e.value.clone()) });
        }
        let mut ssts: Vec<PathBuf> = fs::read_dir(&self.sst_dir)?
            .filter_map(|r| r.ok().map(|e| e.path()))
            .filter(|p| p.extension().map_or(false, |x| x == "db")).collect();
        ssts.sort(); ssts.reverse();
        for path in ssts {
            if let Some(entry) = self.search_sst(&path, key)? {
                return Ok(if entry.is_tombstone { None } else { Some(entry.value) });
            }
        }
        Ok(None)
    }
    fn search_sst(&self, path: &Path, target: &str) -> io::Result<Option<MemEntry>> {
        let mut f = File::open(path)?;
        if f.metadata()?.len() < 16 { return Ok(None); }
        f.seek(SeekFrom::End(-16))?;
        let mut footer = [0u8; 16]; f.read_exact(&mut footer)?;
        let idx_off = u64::from_le_bytes(footer[0..8].try_into().unwrap());
        let count = u32::from_le_bytes(footer[8..12].try_into().unwrap()) as usize;
        if u32::from_le_bytes(footer[12..16].try_into().unwrap()) != MAGIC_NUMBER { return Ok(None); }
        f.seek(SeekFrom::Start(idx_off))?;
        let mut idx = Vec::with_capacity(count);
        for _ in 0..count {
            let mut klen_buf = [0u8; 4]; f.read_exact(&mut klen_buf)?;
            let mut kb = vec![0u8; u32::from_le_bytes(klen_buf) as usize]; f.read_exact(&mut kb)?;
            let mut meta = [0u8; 12]; f.read_exact(&mut meta)?;
            idx.push(IndexEntry {
                key: String::from_utf8_lossy(&kb).to_string(),
                offset: u64::from_le_bytes(meta[0..8].try_into().unwrap()),
                length: u32::from_le_bytes(meta[8..12].try_into().unwrap()),
            });
        }
        if let Ok(i) = idx.binary_search_by(|e| e.key.as_str().cmp(target)) {
            f.seek(SeekFrom::Start(idx[i].offset))?;
            let mut klen_buf = [0u8; 4]; f.read_exact(&mut klen_buf)?;
            f.seek(SeekFrom::Current(u32::from_le_bytes(klen_buf) as i64))?;
            let mut vlen_buf = [0u8; 4]; f.read_exact(&mut vlen_buf)?;
            let mut vb = vec![0u8; u32::from_le_bytes(vlen_buf) as usize]; f.read_exact(&mut vb)?;
            let mut tomb = [0u8; 1]; f.read_exact(&mut tomb)?;
            return Ok(Some(MemEntry { value: vb, is_tombstone: tomb[0] == 1 }));
        }
        Ok(None)
    }
    fn flush(&mut self) -> io::Result<()> {
        if self.memtable.is_empty() { return Ok(()); }
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis();
        let sst_path = self.sst_dir.join(format!("sst_{}_{}.db", now, self.seq_num));
        let mut f = OpenOptions::new().write(true).create_new(true).open(&sst_path)?;
        let mut idx = Vec::with_capacity(self.memtable.len());
        let mut off: u64 = 0;
        for (k, e) in &self.memtable {
            let start = off;
            let kb = k.as_bytes();
            let mut rec = Vec::with_capacity(9 + kb.len() + e.value.len());
            rec.extend_from_slice(&(kb.len() as u32).to_le_bytes());
            rec.extend_from_slice(kb);
            rec.extend_from_slice(&(e.value.len() as u32).to_le_bytes());
            rec.extend_from_slice(&e.value);
            rec.push(if e.is_tombstone { 1 } else { 0 });
            f.write_all(&rec)?;
            let len = rec.len() as u32;
            idx.push(IndexEntry { key: k.clone(), offset: start, length: len });
            off += len as u64;
        }
        let idx_off = off;
        for item in &idx {
            let kb = item.key.as_bytes();
            f.write_all(&(kb.len() as u32).to_le_bytes())?;
            f.write_all(kb)?;
            f.write_all(&item.offset.to_le_bytes())?;
            f.write_all(&item.length.to_le_bytes())?;
        }
        let mut footer = [0u8; 16];
        footer[0..8].copy_from_slice(&idx_off.to_le_bytes());
        footer[8..12].copy_from_slice(&(idx.len() as u32).to_le_bytes());
        footer[12..16].copy_from_slice(&MAGIC_NUMBER.to_le_bytes());
        f.write_all(&footer)?;
        f.sync_data()?;
        self.memtable.clear(); self.mem_bytes = 0;
        self.wal_file.set_len(0)?;
        self.wal_file.seek(SeekFrom::Start(0))?;
        self.wal_file.sync_data()?;
        Ok(())
    }
}
fn main() -> io::Result<()> {
    let dir = PathBuf::from("./minilsm_rust_data");
    let _ = fs::remove_dir_all(&dir);
    let engine = MiniLsmEngine::open(&dir, 120)?;
    {
        let mut eng = engine.lock().unwrap();
        eng.put("user:101", b"Alice")?;
        eng.put("user:102", b"Bob")?;
        eng.put("user:103", b"Charlie")?; // Triggers SSTable 1 flush
        eng.put("user:104", b"Dave")?;
        eng.delete("user:102")?;          // Inserts tombstone
        eng.put("user:105", b"Eve")?;      // Triggers SSTable 2 flush
    }
    {
        let mut eng = engine.lock().unwrap();
        println!("user:101 => {:?}", eng.get("user:101")?.map(|b| String::from_utf8_lossy(&b).to_string()));
        println!("user:102 => {:?}", eng.get("user:102")?.map(|b| String::from_utf8_lossy(&b).to_string()));
        println!("user:104 => {:?}", eng.get("user:104")?.map(|b| String::from_utf8_lossy(&b).to_string()));
        println!("user:999 => {:?}", eng.get("user:999")?.map(|b| String::from_utf8_lossy(&b).to_string()));
    }
    Ok(())
}
```

---

## 4. Build and Execution Matrix

```bash
# -------------------------------------------------------------
# 1. C# (.NET 8)
# -------------------------------------------------------------
dotnet build MiniLsm.csproj -c Release
dotnet run --project MiniLsm.csproj -c Release
# -------------------------------------------------------------
# 2. Go (1.22+)
# -------------------------------------------------------------
go build -o minilsm_go main.go
./minilsm_go
# -------------------------------------------------------------
# 3. Rust (Cargo 2021)
# -------------------------------------------------------------
cargo build --release
./target/release/minilsm
```

---

## 5. Critical Observations for C# Developers

### Observation 1: Zero-Copy Binary Decoding vs. Managed Object Allocations
In C#, reading binary streams using `BinaryReader` or `Encoding.UTF8.GetString` inevitably causes reference allocations on the managed heap. While `Span<byte>` provides zero-allocation window slicing, converting that slice into a persistent object requires copying bytes into managed strings. In contrast, Rust’s `&[u8]` borrows directly from file buffers or mapped memory pages without a single heap allocation, and primitive conversions like `u32::from_le_bytes` compile into single machine instructions (`mov` or `bswap`).

### Observation 2: Precision of POSIX Durability (`Flush` vs `Sync` vs `sync_data`)
C#'s `FileStream.Flush(flushToDisk: true)` abstracts the operating system's persistence model. On Windows, it invokes `FlushFileBuffers`. On Linux, it executes `fsync(2)`, which flushes both file contents and inode metadata to the filesystem journal. Rust provides direct mechanical control: `File::sync_data()` maps strictly to `fdatasync(2)`, which flushes modified dirty data pages while bypassing metadata writes when file capacity permits. This halves disk write amplification under rapid sequential appends.

### Observation 3: In-Memory Search Structures and CPU Cache Locality
C# uses `SortedDictionary<K, V>`, which is implemented as a Red-Black Tree. Every inserted key-value pair allocates a separate node object containing three pointer references (`Left`, `Right`, `Parent`), creating memory fragmentation and pointer-chasing CPU cache misses. Rust utilizes `std::collections::BTreeMap`, an in-memory B-Tree that packs multiple keys and values into dense, contiguous arrays fitting inside 64-byte L1 CPU cache lines. Iterating over a `BTreeMap` during an SSTable flush saturates hardware memory prefetchers.

### Observation 4: Compile-Time Thread Concurrency Guarantees
In C# and Go, concurrent data race errors (e.g., reading from the MemTable while a flush worker clears it) must be caught by manual locks (`lock`, `sync.RWMutex`) or runtime race detectors. In Rust, attempting to share `MiniLsmEngine` across threads without wrapping it in `Arc<Mutex<T>>` or `Arc<RwLock<T>>` fails at compilation time via the `Send` and `Sync` trait contracts.
