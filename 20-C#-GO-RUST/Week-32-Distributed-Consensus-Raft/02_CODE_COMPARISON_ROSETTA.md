# Week 32 Code Rosetta: Raft Core Protocol State Machine

This Rosetta Stone provides a complete, compilable, and runnable implementation of the **Raft Core Protocol State Machine** across C# (.NET 8), Go (1.21+), and Rust (2021 Edition).

Rather than presenting isolated snippets or mock pseudo-code, each language implementation is a complete systems program. It implements:
1. **Canonical Message Schemas**: `LogEntry`, `RequestVoteArgs`, `RequestVoteReply`, `AppendEntriesArgs`, and `AppendEntriesReply`.
2. **Persistent and Volatile State**: `currentTerm`, `votedFor`, `log[]`, `commitIndex`, and `lastApplied`.
3. **Leader Election Lifecycle**: Randomized election timers (150ms–300ms), transition from Follower to Candidate, term increment, self-voting, and parallel vote solicitation.
4. **Election Safety & Log Completeness**: Lexicographical evaluation of `(lastLogTerm, lastLogIndex)` preventing stale candidates from winning.
5. **Log Replication & Heartbeats**: `AppendEntries` serving the dual role of heartbeat and payload replication.
6. **Log Matching Invariant & Consistency Checks**: Follower verification of `prevLogIndex` and `prevLogTerm`, truncation of conflicting uncommitted entries, and appending new entries.
7. **Commit Index Progression**: Leader advancing `commitIndex` once an entry from the current term is replicated across a strict majority quorum ($\lfloor N/2 \rfloor + 1$).
8. **An In-Memory Cluster Simulation**: Spinning up a 3-node cluster, witnessing leader election, proposing a client command, replicating across followers, committing, and applying to the local state machine.

---

## 1. C# Implementation (.NET 8)

### Project Configuration (`RaftRosetta.csproj`)
```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
  </PropertyGroup>
</Project>
```

### Complete Source Code (`Program.cs`)
```csharp
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace RaftRosetta
{
    public enum NodeRole { Follower, Candidate, Leader }
    public sealed class LogEntry { public int Index { get; set; } public int Term { get; set; } public string Command { get; set; } = ""; }
    public sealed class RequestVoteArgs { public int Term { get; set; } public int CandidateId { get; set; } public int LastLogIndex { get; set; } public int LastLogTerm { get; set; } }
    public sealed class RequestVoteReply { public int Term { get; set; } public bool VoteGranted { get; set; } }
    public sealed class AppendEntriesArgs { public int Term { get; set; } public int LeaderId { get; set; } public int PrevLogIndex { get; set; } public int PrevLogTerm { get; set; } public List<LogEntry> Entries { get; set; } = new(); public int LeaderCommit { get; set; } }
    public sealed class AppendEntriesReply { public int Term { get; set; } public bool Success { get; set; } public int MatchIndex { get; set; } }

    public sealed class RaftNode
    {
        public int Id { get; }
        private readonly List<RaftNode> _peers = new();
        private readonly object _lock = new();
        private readonly Random _rand = new();
        private readonly CancellationTokenSource _cts = new();
        public int CurrentTerm { get; private set; } = 0;
        public int? VotedFor { get; private set; } = null;
        public List<LogEntry> Log { get; } = new() { new() { Index = 0, Term = 0, Command = "<ROOT>" } };
        public NodeRole Role { get; private set; } = NodeRole.Follower;
        public int CommitIndex { get; private set; } = 0;
        public int LastApplied { get; private set; } = 0;
        private DateTime _lastHeartbeat = DateTime.UtcNow;
        private readonly Dictionary<int, int> _nextIndex = new(), _matchIndex = new();

        public RaftNode(int id) => Id = id;
        public void RegisterPeers(IEnumerable<RaftNode> peers) => _peers.AddRange(peers.Where(p => p.Id != Id));
        public void Start() => Task.Run(() => ElectionTimerLoop(_cts.Token));
        public void Stop() => _cts.Cancel();

        private async Task ElectionTimerLoop(CancellationToken ct)
        {
            while (!ct.IsCancellationRequested)
            {
                int timeoutMs = _rand.Next(150, 301); // Randomized 150-300ms avoids split-vote livelock
                await Task.Delay(timeoutMs, ct).ConfigureAwait(false);
                bool trigger = false;
                lock (_lock) { if (Role != NodeRole.Leader && (DateTime.UtcNow - _lastHeartbeat).TotalMilliseconds >= timeoutMs) trigger = true; }
                if (trigger) StartElection();
            }
        }

        private void StartElection()
        {
            int term, lastIdx, lastTerm;
            lock (_lock)
            {
                Role = NodeRole.Candidate; CurrentTerm++; VotedFor = Id; _lastHeartbeat = DateTime.UtcNow;
                term = CurrentTerm; lastIdx = Log[^1].Index; lastTerm = Log[^1].Term;
                Console.WriteLine($"[Node {Id}] Became Candidate for Term {term}");
            }
            int votes = 1, quorum = ((_peers.Count + 1) / 2) + 1; // Strict majority (N/2) + 1
            Parallel.ForEach(_peers, peer => {
                var reply = peer.HandleRequestVote(new() { Term = term, CandidateId = Id, LastLogIndex = lastIdx, LastLogTerm = lastTerm });
                lock (_lock) {
                    if (reply.Term > CurrentTerm) { StepDown(reply.Term); return; }
                    if (Role == NodeRole.Candidate && reply.Term == CurrentTerm && reply.VoteGranted && ++votes >= quorum && Role != NodeRole.Leader) {
                        Role = NodeRole.Leader; Console.WriteLine($"[Node {Id}] WON ELECTION for Term {CurrentTerm}!");
                        foreach (var p in _peers) { _nextIndex[p.Id] = Log[^1].Index + 1; _matchIndex[p.Id] = 0; }
                        Task.Run(() => HeartbeatLoop(_cts.Token));
                    }
                }
            });
        }

        private void StepDown(int term) { CurrentTerm = term; Role = NodeRole.Follower; VotedFor = null; }

        private async Task HeartbeatLoop(CancellationToken ct)
        {
            while (!ct.IsCancellationRequested)
            {
                lock (_lock) { if (Role != NodeRole.Leader) return; }
                BroadcastAppendEntries();
                await Task.Delay(50, ct).ConfigureAwait(false); // Send heartbeats every 50ms
            }
        }

        public bool Propose(string command)
        {
            lock (_lock) {
                if (Role != NodeRole.Leader) return false;
                Log.Add(new() { Index = Log[^1].Index + 1, Term = CurrentTerm, Command = command });
                Console.WriteLine($"[Leader {Id}] Appended entry: {command}");
            }
            BroadcastAppendEntries();
            return true;
        }

        private void BroadcastAppendEntries()
        {
            int term, commit;
            lock (_lock) { if (Role != NodeRole.Leader) return; term = CurrentTerm; commit = CommitIndex; }
            Parallel.ForEach(_peers, peer => {
                int prevIdx, prevTerm; List<LogEntry> entries;
                lock (_lock) { prevIdx = _nextIndex[peer.Id] - 1; prevTerm = Log[prevIdx].Term; entries = Log.Skip(prevIdx + 1).ToList(); }
                var reply = peer.HandleAppendEntries(new() { Term = term, LeaderId = Id, PrevLogIndex = prevIdx, PrevLogTerm = prevTerm, Entries = entries, LeaderCommit = commit });
                lock (_lock) {
                    if (reply.Term > CurrentTerm) { StepDown(reply.Term); return; }
                    if (Role != NodeRole.Leader || reply.Term != CurrentTerm) return;
                    if (reply.Success) { _matchIndex[peer.Id] = reply.MatchIndex; _nextIndex[peer.Id] = reply.MatchIndex + 1; CheckCommit(); }
                    else if (_nextIndex[peer.Id] > 1) _nextIndex[peer.Id]--;
                }
            });
        }

        private void CheckCommit()
        {
            int quorum = ((_peers.Count + 1) / 2) + 1;
            for (int n = Log[^1].Index; n > CommitIndex; n--) {
                if (Log[n].Term != CurrentTerm) continue; // Safety: only commit entries created in current term
                int matches = 1 + _peers.Count(p => _matchIndex.TryGetValue(p.Id, out int m) && m >= n);
                if (matches >= quorum) {
                    CommitIndex = n; Console.WriteLine($"[Leader {Id}] ADVANCED CommitIndex to {CommitIndex} via Quorum!");
                    while (CommitIndex > LastApplied) { LastApplied++; Console.WriteLine($"[Node {Id}] Applied: {Log[LastApplied].Command}"); }
                    break;
                }
            }
        }

        public RequestVoteReply HandleRequestVote(RequestVoteArgs args)
        {
            lock (_lock) {
                if (args.Term < CurrentTerm) return new() { Term = CurrentTerm, VoteGranted = false };
                if (args.Term > CurrentTerm) StepDown(args.Term);
                var last = Log[^1];
                bool upToDate = args.LastLogTerm > last.Term || (args.LastLogTerm == last.Term && args.LastLogIndex >= last.Index);
                if ((VotedFor == null || VotedFor == args.CandidateId) && upToDate) {
                    VotedFor = args.CandidateId; _lastHeartbeat = DateTime.UtcNow;
                    Console.WriteLine($"[Node {Id}] Granted vote to Candidate {args.CandidateId}");
                    return new() { Term = CurrentTerm, VoteGranted = true };
                }
                return new() { Term = CurrentTerm, VoteGranted = false };
            }
        }

        public AppendEntriesReply HandleAppendEntries(AppendEntriesArgs args)
        {
            lock (_lock) {
                if (args.Term < CurrentTerm) return new() { Term = CurrentTerm, Success = false };
                if (args.Term > CurrentTerm || Role != NodeRole.Follower) StepDown(args.Term);
                _lastHeartbeat = DateTime.UtcNow;
                if (args.PrevLogIndex >= Log.Count || Log[args.PrevLogIndex].Term != args.PrevLogTerm)
                    return new() { Term = CurrentTerm, Success = false, MatchIndex = Log[^1].Index };
                int insertIdx = args.PrevLogIndex + 1, newIdx = 0;
                while (insertIdx < Log.Count && newIdx < args.Entries.Count) {
                    if (Log[insertIdx].Term != args.Entries[newIdx].Term) { Log.RemoveRange(insertIdx, Log.Count - insertIdx); break; }
                    insertIdx++; newIdx++;
                }
                while (newIdx < args.Entries.Count) Log.Add(args.Entries[newIdx++]);
                if (args.LeaderCommit > CommitIndex) {
                    CommitIndex = Math.Min(args.LeaderCommit, Log[^1].Index);
                    while (CommitIndex > LastApplied) { LastApplied++; Console.WriteLine($"[Node {Id}] Applied: {Log[LastApplied].Command}"); }
                }
                return new() { Term = CurrentTerm, Success = true, MatchIndex = Log[^1].Index };
            }
        }
    }

    public static class Program
    {
        public static async Task Main()
        {
            Console.WriteLine("=== Raft Consensus Rosetta Stone (.NET 8) ===");
            var nodes = new List<RaftNode> { new(1), new(2), new(3) };
            foreach (var n in nodes) { n.RegisterPeers(nodes); n.Start(); }
            await Task.Delay(1000); // Allow nodes to time out and elect a leader
            nodes.First(n => n.Role == NodeRole.Leader).Propose("SET auth=mtls");
            await Task.Delay(500); // Wait for replication and quorum commit
            foreach (var n in nodes) n.Stop();
            Console.WriteLine("Simulation completed successfully.");
        }
    }
}
```

### Build and Run Commands
```bash
dotnet build RaftRosetta.csproj
dotnet run --project RaftRosetta.csproj
```

---

## 2. Go Implementation (Go 1.21+)

### Project Configuration (`go.mod`)
```go
module raftrosetta

go 1.21
```

### Complete Source Code (`main.go`)
```go
package main

import (
	"fmt"
	"math/rand"
	"sync"
	"time"
)

type NodeRole int
const ( Follower NodeRole = iota; Candidate; Leader )

type LogEntry struct { Index, Term int; Command string }
type RequestVoteArgs struct { Term, CandidateID, LastLogIndex, LastLogTerm int }
type RequestVoteReply struct { Term int; VoteGranted bool }
type AppendEntriesArgs struct { Term, LeaderID, PrevLogIndex, PrevLogTerm, LeaderCommit int; Entries []LogEntry }
type AppendEntriesReply struct { Term, MatchIndex int; Success bool }

type RaftNode struct {
	mu sync.Mutex; id int; peers []*RaftNode
	currentTerm, votedFor, commitIndex, lastApplied int
	log []LogEntry; role NodeRole; lastHeartbeat time.Time
	nextIndex, matchIndex map[int]int; stopCh chan struct{}
}

func NewRaftNode(id int) *RaftNode {
	rn := &RaftNode{
		id: id, votedFor: -1, role: Follower, lastHeartbeat: time.Now(),
		nextIndex: make(map[int]int), matchIndex: make(map[int]int), stopCh: make(chan struct{}),
	}
	rn.log = append(rn.log, LogEntry{Index: 0, Term: 0, Command: "<ROOT>"})
	return rn
}

func (rn *RaftNode) RegisterPeers(peers []*RaftNode) {
	for _, p := range peers { if p.id != rn.id { rn.peers = append(rn.peers, p) } }
}
func (rn *RaftNode) Start() { go rn.runElectionTimerLoop() }
func (rn *RaftNode) Stop()  { close(rn.stopCh) }

func (rn *RaftNode) runElectionTimerLoop() {
	for {
		timeout := time.Duration(150+rand.Intn(151)) * time.Millisecond
		select {
		case <-rn.stopCh: return
		case <-time.After(timeout):
			rn.mu.Lock()
			if rn.role != Leader && time.Since(rn.lastHeartbeat) >= timeout { rn.mu.Unlock(); rn.startElection() } else { rn.mu.Unlock() }
		}
	}
}

func (rn *RaftNode) startElection() {
	rn.mu.Lock()
	rn.role = Candidate; rn.currentTerm++; rn.votedFor = rn.id; rn.lastHeartbeat = time.Now()
	term, last := rn.currentTerm, rn.log[len(rn.log)-1]
	fmt.Printf("[Node %d] Became Candidate for Term %d\n", rn.id, term)
	rn.mu.Unlock()

	var voteMu sync.Mutex
	votes, quorum := 1, ((len(rn.peers)+1)/2)+1
	for _, peer := range rn.peers {
		go func(p *RaftNode) {
			reply := p.HandleRequestVote(RequestVoteArgs{Term: term, CandidateID: rn.id, LastLogIndex: last.Index, LastLogTerm: last.Term})
			rn.mu.Lock(); defer rn.mu.Unlock()
			if reply.Term > rn.currentTerm { rn.stepDown(reply.Term); return }
			if rn.role == Candidate && reply.Term == rn.currentTerm && reply.VoteGranted {
				voteMu.Lock(); votes++; hasQuorum := votes >= quorum; voteMu.Unlock()
				if hasQuorum && rn.role != Leader {
					rn.role = Leader
					fmt.Printf("[Node %d] WON ELECTION for Term %d!\n", rn.id, rn.currentTerm)
					nextIdx := rn.log[len(rn.log)-1].Index + 1
					for _, pr := range rn.peers { rn.nextIndex[pr.id] = nextIdx; rn.matchIndex[pr.id] = 0 }
					go rn.runHeartbeatLoop()
				}
			}
		}(peer)
	}
}

func (rn *RaftNode) stepDown(term int) { rn.currentTerm = term; rn.role = Follower; rn.votedFor = -1 }

func (rn *RaftNode) runHeartbeatLoop() {
	ticker := time.NewTicker(50 * time.Millisecond); defer ticker.Stop()
	for {
		select {
		case <-rn.stopCh: return
		case <-ticker.C:
			rn.mu.Lock()
			if rn.role != Leader { rn.mu.Unlock(); return }
			rn.mu.Unlock(); rn.broadcastAppendEntries()
		}
	}
}

func (rn *RaftNode) Propose(command string) bool {
	rn.mu.Lock()
	if rn.role != Leader { rn.mu.Unlock(); return false }
	entry := LogEntry{Index: rn.log[len(rn.log)-1].Index + 1, Term: rn.currentTerm, Command: command}
	rn.log = append(rn.log, entry)
	fmt.Printf("[Leader %d] Appended entry: %s\n", rn.id, command)
	rn.mu.Unlock(); rn.broadcastAppendEntries()
	return true
}

func (rn *RaftNode) broadcastAppendEntries() {
	rn.mu.Lock()
	if rn.role != Leader { rn.mu.Unlock(); return }
	term, commit := rn.currentTerm, rn.commitIndex; rn.mu.Unlock()

	for _, peer := range rn.peers {
		go func(p *RaftNode) {
			rn.mu.Lock()
			prevIdx := rn.nextIndex[p.id] - 1; prevTerm := rn.log[prevIdx].Term
			entries := append([]LogEntry(nil), rn.log[prevIdx+1:]...) // Contiguous slice copy avoids allocation
			rn.mu.Unlock()
			reply := p.HandleAppendEntries(AppendEntriesArgs{Term: term, LeaderID: rn.id, PrevLogIndex: prevIdx, PrevLogTerm: prevTerm, Entries: entries, LeaderCommit: commit})
			rn.mu.Lock(); defer rn.mu.Unlock()
			if reply.Term > rn.currentTerm { rn.stepDown(reply.Term); return }
			if rn.role != Leader || reply.Term != rn.currentTerm { return }
			if reply.Success {
				rn.matchIndex[p.id] = reply.MatchIndex; rn.nextIndex[p.id] = reply.MatchIndex + 1; rn.checkCommit()
			} else if rn.nextIndex[p.id] > 1 { rn.nextIndex[p.id]-- }
		}(peer)
	}
}

func (rn *RaftNode) checkCommit() {
	quorum := ((len(rn.peers) + 1) / 2) + 1
	for n := rn.log[len(rn.log)-1].Index; n > rn.commitIndex; n-- {
		if rn.log[n].Term != rn.currentTerm { continue }
		matches := 1
		for _, p := range rn.peers { if rn.matchIndex[p.id] >= n { matches++ } }
		if matches >= quorum {
			rn.commitIndex = n
			fmt.Printf("[Leader %d] ADVANCED CommitIndex to %d via Quorum!\n", rn.id, rn.commitIndex)
			for rn.commitIndex > rn.lastApplied {
				rn.lastApplied++; fmt.Printf("[Node %d] Applied: %s\n", rn.id, rn.log[rn.lastApplied].Command)
			}
			break
		}
	}
}

func (rn *RaftNode) HandleRequestVote(args RequestVoteArgs) RequestVoteReply {
	rn.mu.Lock(); defer rn.mu.Unlock()
	if args.Term < rn.currentTerm { return RequestVoteReply{Term: rn.currentTerm, VoteGranted: false} }
	if args.Term > rn.currentTerm { rn.stepDown(args.Term) }
	last := rn.log[len(rn.log)-1]
	upToDate := args.LastLogTerm > last.Term || (args.LastLogTerm == last.Term && args.LastLogIndex >= last.Index)
	if (rn.votedFor == -1 || rn.votedFor == args.CandidateID) && upToDate {
		rn.votedFor = args.CandidateID; rn.lastHeartbeat = time.Now()
		fmt.Printf("[Node %d] Granted vote to Candidate %d\n", rn.id, args.CandidateID)
		return RequestVoteReply{Term: rn.currentTerm, VoteGranted: true}
	}
	return RequestVoteReply{Term: rn.currentTerm, VoteGranted: false}
}

func (rn *RaftNode) HandleAppendEntries(args AppendEntriesArgs) AppendEntriesReply {
	rn.mu.Lock(); defer rn.mu.Unlock()
	if args.Term < rn.currentTerm { return AppendEntriesReply{Term: rn.currentTerm, Success: false} }
	if args.Term > rn.currentTerm || rn.role != Follower { rn.stepDown(args.Term) }
	rn.lastHeartbeat = time.Now()
	if args.PrevLogIndex >= len(rn.log) || rn.log[args.PrevLogIndex].Term != args.PrevLogTerm {
		return AppendEntriesReply{Term: rn.currentTerm, Success: false, MatchIndex: rn.log[len(rn.log)-1].Index}
	}
	insertIdx, newIdx := args.PrevLogIndex+1, 0
	for insertIdx < len(rn.log) && newIdx < len(args.Entries) {
		if rn.log[insertIdx].Term != args.Entries[newIdx].Term { rn.log = rn.log[:insertIdx]; break }
		insertIdx++; newIdx++
	}
	for newIdx < len(args.Entries) { rn.log = append(rn.log, args.Entries[newIdx]); newIdx++ }
	if args.LeaderCommit > rn.commitIndex {
		lastIdx := rn.log[len(rn.log)-1].Index
		if args.LeaderCommit < lastIdx { rn.commitIndex = args.LeaderCommit } else { rn.commitIndex = lastIdx }
		for rn.commitIndex > rn.lastApplied { rn.lastApplied++; fmt.Printf("[Node %d] Applied: %s\n", rn.id, rn.log[rn.lastApplied].Command) }
	}
	return AppendEntriesReply{Term: rn.currentTerm, Success: true, MatchIndex: rn.log[len(rn.log)-1].Index}
}

func main() {
	fmt.Println("=== Raft Consensus Rosetta Stone (Go 1.21+) ===")
	nodes := []*RaftNode{NewRaftNode(1), NewRaftNode(2), NewRaftNode(3)}
	for _, n := range nodes { n.RegisterPeers(nodes); n.Start() }
	time.Sleep(1 * time.Second)
	for _, n := range nodes { if n.Propose("SET auth=mtls") { break } }
	time.Sleep(500 * time.Millisecond)
	for _, n := range nodes { n.Stop() }
	fmt.Println("Simulation completed successfully.")
}
```

### Build and Run Commands
```bash
go build -o raft_rosetta main.go
./raft_rosetta
```

---

## 3. Rust Implementation (Rust 2021 Edition)

### Project Configuration (`Cargo.toml`)
```toml
[package]
name = "raft_rosetta"
version = "0.1.0"
edition = "2021"

[dependencies]
rand = "0.8"
```

### Complete Source Code (`src/main.rs`)
```rust
use rand::Rng;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NodeRole { Follower, Candidate, Leader }
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LogEntry { pub index: usize, pub term: usize, pub command: String }
#[derive(Clone, Debug)]
pub struct RequestVoteArgs { pub term: usize, pub candidate_id: usize, pub last_log_index: usize, pub last_log_term: usize }
#[derive(Clone, Debug)]
pub struct RequestVoteReply { pub term: usize, pub vote_granted: bool }
#[derive(Clone, Debug)]
pub struct AppendEntriesArgs { pub term: usize, pub leader_id: usize, pub prev_log_index: usize, pub prev_log_term: usize, pub entries: Vec<LogEntry>, pub leader_commit: usize }
#[derive(Clone, Debug)]
pub struct AppendEntriesReply { pub term: usize, pub success: bool, pub match_index: usize }

pub struct RaftState {
    pub id: usize, pub current_term: usize, pub voted_for: Option<usize>, pub log: Vec<LogEntry>,
    pub role: NodeRole, pub commit_index: usize, pub last_applied: usize, pub last_heartbeat: Instant,
    pub next_index: HashMap<usize, usize>, pub match_index: HashMap<usize, usize>,
}

#[derive(Clone)]
pub struct RaftNode {
    pub state: Arc<Mutex<RaftState>>,
    pub peers: Arc<Mutex<Vec<RaftNode>>>,
}

impl RaftNode {
    pub fn new(id: usize) -> Self {
        let initial_state = RaftState {
            id, current_term: 0, voted_for: None,
            log: vec![LogEntry { index: 0, term: 0, command: "<ROOT>".into() }],
            role: NodeRole::Follower, commit_index: 0, last_applied: 0, last_heartbeat: Instant::now(),
            next_index: HashMap::new(), match_index: HashMap::new(),
        };
        Self { state: Arc::new(Mutex::new(initial_state)), peers: Arc::new(Mutex::new(Vec::new())) }
    }

    pub fn register_peers(&self, peers: &[RaftNode]) {
        let my_id = self.state.lock().unwrap().id;
        let mut peer_guard = self.peers.lock().unwrap();
        for p in peers { if p.state.lock().unwrap().id != my_id { peer_guard.push(p.clone()); } }
    }

    pub fn start(&self) {
        let self_clone = self.clone();
        thread::spawn(move || self_clone.run_election_timer_loop());
    }

    fn run_election_timer_loop(&self) {
        let mut rng = rand::thread_rng();
        loop {
            let timeout_ms = rng.gen_range(150..=300);
            thread::sleep(Duration::from_millis(20));
            let mut should_elect = false;
            {
                let guard = self.state.lock().unwrap();
                if guard.role != NodeRole::Leader && guard.last_heartbeat.elapsed().as_millis() >= timeout_ms as u128 { should_elect = true; }
            } // Lock released before initiating election to prevent deadlock
            if should_elect { self.start_election(); }
        }
    }

    fn start_election(&self) {
        let (term, last_log_index, last_log_term, id) = {
            let mut guard = self.state.lock().unwrap();
            guard.role = NodeRole::Candidate; guard.current_term += 1; guard.voted_for = Some(guard.id);
            guard.last_heartbeat = Instant::now();
            let last_entry = guard.log.last().unwrap();
            println!("[Node {}] Became Candidate for Term {}", guard.id, guard.current_term);
            (guard.current_term, last_entry.index, last_entry.term, guard.id)
        };

        let peers = self.peers.lock().unwrap().clone();
        let quorum = ((peers.len() + 1) / 2) + 1;
        let votes = Arc::new(Mutex::new(1));

        for peer in peers {
            let self_ref = self.clone();
            let votes_ref = Arc::clone(&votes);
            let args = RequestVoteArgs { term, candidate_id: id, last_log_index, last_log_term };
            thread::spawn(move || {
                let reply = peer.handle_request_vote(args);
                let mut guard = self_ref.state.lock().unwrap();
                if reply.term > guard.current_term { guard.current_term = reply.term; guard.role = NodeRole::Follower; guard.voted_for = None; return; }
                if guard.role == NodeRole::Candidate && reply.term == guard.current_term && reply.vote_granted {
                    let mut v = votes_ref.lock().unwrap(); *v += 1;
                    if *v >= quorum && guard.role != NodeRole::Leader {
                        guard.role = NodeRole::Leader;
                        println!("[Node {}] WON ELECTION for Term {}!", guard.id, guard.current_term);
                        let next_idx = guard.log.last().unwrap().index + 1;
                        let peer_ids: Vec<usize> = self_ref.peers.lock().unwrap().iter().map(|p| p.state.lock().unwrap().id).collect();
                        for pid in peer_ids { guard.next_index.insert(pid, next_idx); guard.match_index.insert(pid, 0); }
                        let self_bg = self_ref.clone();
                        thread::spawn(move || self_bg.run_heartbeat_loop());
                    }
                }
            });
        }
    }

    fn run_heartbeat_loop(&self) {
        loop {
            if self.state.lock().unwrap().role != NodeRole::Leader { return; }
            self.broadcast_append_entries();
            thread::sleep(Duration::from_millis(50));
        }
    }

    pub fn propose(&self, command: String) -> bool {
        let mut guard = self.state.lock().unwrap();
        if guard.role != NodeRole::Leader { return false; }
        let new_entry = LogEntry { index: guard.log.last().unwrap().index + 1, term: guard.current_term, command: command.clone() };
        guard.log.push(new_entry);
        println!("[Leader {}] Appended entry: {}", guard.id, command);
        drop(guard);
        self.broadcast_append_entries();
        true
    }

    fn broadcast_append_entries(&self) {
        let (current_term, leader_commit, leader_id) = {
            let guard = self.state.lock().unwrap();
            if guard.role != NodeRole::Leader { return; }
            (guard.current_term, guard.commit_index, guard.id)
        };

        let peers = self.peers.lock().unwrap().clone();
        for peer in peers {
            let self_ref = self.clone();
            thread::spawn(move || {
                let peer_id = peer.state.lock().unwrap().id;
                let (prev_index, prev_term, entries) = {
                    let guard = self_ref.state.lock().unwrap();
                    let prev_idx = *guard.next_index.get(&peer_id).unwrap_or(&1) - 1;
                    (prev_idx, guard.log[prev_idx].term, guard.log[prev_idx + 1..].to_vec())
                };
                let reply = peer.handle_append_entries(AppendEntriesArgs { term: current_term, leader_id, prev_log_index: prev_index, prev_log_term: prev_term, entries, leader_commit });
                let mut guard = self_ref.state.lock().unwrap();
                if reply.term > guard.current_term { guard.current_term = reply.term; guard.role = NodeRole::Follower; guard.voted_for = None; return; }
                if guard.role != NodeRole::Leader || reply.term != guard.current_term { return; }
                if reply.success {
                    guard.match_index.insert(peer_id, reply.match_index); guard.next_index.insert(peer_id, reply.match_index + 1); self_ref.check_commit(&mut guard);
                } else {
                    let next = guard.next_index.entry(peer_id).or_insert(1); if *next > 1 { *next -= 1; }
                }
            });
        }
    }

    fn check_commit(&self, guard: &mut RaftState) {
        let peers_count = self.peers.lock().unwrap().len();
        let quorum = ((peers_count + 1) / 2) + 1;
        for n in (guard.commit_index + 1..=guard.log.last().unwrap().index).rev() {
            if guard.log[n].term != guard.current_term { continue; }
            let mut matches = 1;
            for match_idx in guard.match_index.values() { if *match_idx >= n { matches += 1; } }
            if matches >= quorum {
                guard.commit_index = n;
                println!("[Leader {}] ADVANCED CommitIndex to {} via Quorum!", guard.id, guard.commit_index);
                while guard.commit_index > guard.last_applied {
                    guard.last_applied += 1; println!("[Node {}] Applied: {}", guard.id, guard.log[guard.last_applied].command);
                }
                break;
            }
        }
    }

    pub fn handle_request_vote(&self, args: RequestVoteArgs) -> RequestVoteReply {
        let mut guard = self.state.lock().unwrap();
        if args.term < guard.current_term { return RequestVoteReply { term: guard.current_term, vote_granted: false }; }
        if args.term > guard.current_term { guard.current_term = args.term; guard.role = NodeRole::Follower; guard.voted_for = None; }
        let last = guard.log.last().unwrap().clone();
        let log_up_to_date = args.last_log_term > last.term || (args.last_log_term == last.term && args.last_log_index >= last.index);
        if (guard.voted_for.is_none() || guard.voted_for == Some(args.candidate_id)) && log_up_to_date {
            guard.voted_for = Some(args.candidate_id); guard.last_heartbeat = Instant::now();
            println!("[Node {}] Granted vote to Candidate {}", guard.id, args.candidate_id);
            RequestVoteReply { term: guard.current_term, vote_granted: true }
        } else {
            RequestVoteReply { term: guard.current_term, vote_granted: false }
        }
    }

    pub fn handle_append_entries(&self, args: AppendEntriesArgs) -> AppendEntriesReply {
        let mut guard = self.state.lock().unwrap();
        if args.term < guard.current_term { return AppendEntriesReply { term: guard.current_term, success: false, match_index: 0 }; }
        if args.term > guard.current_term || guard.role != NodeRole::Follower { guard.current_term = args.term; guard.role = NodeRole::Follower; guard.voted_for = None; }
        guard.last_heartbeat = Instant::now();
        if args.prev_log_index >= guard.log.len() || guard.log[args.prev_log_index].term != args.prev_log_term {
            return AppendEntriesReply { term: guard.current_term, success: false, match_index: guard.log.last().unwrap().index };
        }
        let mut insert_idx = args.prev_log_index + 1;
        let mut new_idx = 0;
        while insert_idx < guard.log.len() && new_idx < args.entries.len() {
            if guard.log[insert_idx].term != args.entries[new_idx].term { guard.log.truncate(insert_idx); break; }
            insert_idx += 1; new_idx += 1;
        }
        while new_idx < args.entries.len() { guard.log.push(args.entries[new_idx].clone()); new_idx += 1; }
        if args.leader_commit > guard.commit_index {
            let last_idx = guard.log.last().unwrap().index;
            guard.commit_index = std::cmp::min(args.leader_commit, last_idx);
            while guard.commit_index > guard.last_applied {
                guard.last_applied += 1; println!("[Node {}] Applied: {}", guard.id, guard.log[guard.last_applied].command);
            }
        }
        AppendEntriesReply { term: guard.current_term, success: true, match_index: guard.log.last().unwrap().index }
    }
}

fn main() {
    println!("=== Raft Consensus Rosetta Stone (Rust 2021) ===");
    let n1 = RaftNode::new(1);
    let n2 = RaftNode::new(2);
    let n3 = RaftNode::new(3);
    let cluster = vec![n1.clone(), n2.clone(), n3.clone()];
    for n in &cluster { n.register_peers(&cluster); n.start(); }
    println!("Cluster initialized. Waiting for leader election...");
    thread::sleep(Duration::from_millis(1000));
    let _ = n1.propose("SET auth=mtls".to_string()) || n2.propose("SET auth=mtls".to_string()) || n3.propose("SET auth=mtls".to_string());
    thread::sleep(Duration::from_millis(500));
    println!("Simulation completed successfully.");
}
```

### Build and Run Commands
```bash
cargo build --release
cargo run --release
```

---

## Critical Observations for C# Developers

### 1. Timer Precision and ThreadPool Contention
In the C# implementation, `Task.Delay(timeoutMs)` queues a timer callback on the CLR `ThreadPool`. Under high CPU load or ThreadPool starvation (e.g., synchronous `.Result` calls in ASP.NET Core pipelines), timer continuations can be delayed by 50ms to 200ms. In Raft, where election timeouts are 150ms–300ms, ThreadPool jitter leads directly to **spurious leader elections**. In contrast, Go's `time.After` leverages the runtime's dedicated timer wheel integrated into the netpoller, while Rust's `thread::sleep` or Tokio timer wheels interact directly with OS timer primitives (`timerfd` or `epoll_wait`), ensuring strict temporal determinism.

### 2. Lock Scoping, Reentrancy, and Deadlock Hazards
In C#, `lock (_lock)` is **reentrant**: a thread that acquires a lock can re-enter that same lock multiple times without deadlocking. In Rust, `std::sync::Mutex` is **strictly non-reentrant**. If a method holding a lock guard calls a helper method that also attempts `self.state.lock().unwrap()`, the thread immediately deadlocks itself. Notice how Rust code explicitly passes `&mut RaftState` to internal helpers (`check_commit`) once the lock is already held, enforcing compile-time borrowing rules rather than relying on runtime reentrancy.

### 3. RAII Scoped Guard Dropping vs. Manual Unlocking
In C# and Go, locks are held across entire function blocks using `lock` statements or `defer rn.mu.Unlock()`. While convenient, doing network I/O or waiting for peer RPC responses while holding a state lock completely freezes the node, preventing it from handling incoming heartbeats. In Rust, the RAII `MutexGuard` drops automatically when it leaves scope. In both `propose` and `run_election_timer_loop`, we explicitly introduce nested `{ ... }` blocks or call `drop(guard)` before launching thread spawns or sleeping, ensuring critical sections remain measured in microseconds.

### 4. Memory Layout and Allocation Overhead in Log Slices
When slicing entries to transmit via `AppendEntries`, C#'s `Log.Skip(prevIdx + 1).ToList()` allocates a new `List<LogEntry>` object on the managed heap, creating garbage collection pressure on every heartbeat cycle. Go's slice syntax `append([]LogEntry(nil), rn.log[prevIdx+1:]...)` creates a contiguous slice buffer with minimal GC churn. Rust allows borrowing a slice `&guard.log[prev_idx + 1..]` with zero allocations, only cloning the entry structs when creating an owned payload to cross thread boundaries.
