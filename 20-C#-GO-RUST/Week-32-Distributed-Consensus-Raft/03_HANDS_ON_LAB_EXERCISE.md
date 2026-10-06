# Week 32 Hands-On Lab: Distributed Consensus Cluster Simulation

## Executive Lab Briefing & Architecture

In enterprise .NET architectures, engineers rely on managed infrastructure to handle failover automation. When an Azure SQL database or Kafka broker fails, high availability agents execute failover orchestration behind the scenes. In systems engineering with Go and Rust, you are responsible for constructing the consensus engines that guarantee zero data loss and eliminate split-brain conditions.

In this intensive team lab, your engineering team will construct a local 3-node Raft consensus cluster equipped with an in-memory **Virtual Network Fault Injector**. You will observe leader election, execute log replication across healthy nodes, introduce an asymmetric network partition isolating Node 1, witness the majority cluster elect a new leader and commit writes, attempt a write on the isolated minority, and finally heal the partition to observe automatic log truncation and reconciliation without data loss.

```
+-----------------------------------------------------------------------------------------+
|                               Lab Architecture Topology                                 |
|                                                                                         |
|   Healthy State:                                                                        |
|   [Node 1: Leader (T1)] <====== RPC ======> [Node 2: Follower]                          |
|             ^                                      ^                                    |
|             |================ RPC =================|                                    |
|             v                                      v                                    |
|                                             [Node 3: Follower]                          |
|                                                                                         |
|   Partitioned State:                                                                    |
|   +-----------------------+              +------------------------------------------+   |
|   |   Minority Island     |              |             Majority Island              |   |
|   | [Node 1: Stale Leader]|     XXXX     | [Node 2: New Leader (T2)] <== RPC ==>   |   |
|   |  - Uncommitted Writes |   (Dropped)  | [Node 3: Follower (T2)]                  |   |
|   |  - Quorum Impossible  |              |  - Quorum Active (2/3)                   |   |
|   +-----------------------+              +------------------------------------------+   |
+-----------------------------------------------------------------------------------------+
```

---

## Day 1–2: Complete Reference Implementation (Go 1.21+)

The reference implementation below provides a fully functioning, self-contained Raft cluster featuring a thread-safe `VirtualNetwork` router capable of injecting dropped packets and simulated partitions on demand.

### Complete Reference Code (`cluster_lab.go`)

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

func (r NodeRole) String() string {
	switch r { case Follower: return "Follower"; case Candidate: return "Candidate"; case Leader: return "Leader"; default: return "Unknown" }
}

type LogEntry struct { Index, Term int; Command string }
type RequestVoteArgs struct { Term, CandidateID, LastLogIndex, LastLogTerm int }
type RequestVoteReply struct { Term int; VoteGranted bool }
type AppendEntriesArgs struct { Term, LeaderID, PrevLogIndex, PrevLogTerm, LeaderCommit int; Entries []LogEntry }
type AppendEntriesReply struct { Term, MatchIndex int; Success bool }

// VirtualNetwork simulates an unreliable physical network with partition injection
type VirtualNetwork struct {
	mu           sync.RWMutex
	nodes        map[int]*RaftNode
	isolatedNode int // -1 means healthy; >= 0 indicates the isolated node ID
}

func NewVirtualNetwork() *VirtualNetwork {
	return &VirtualNetwork{nodes: make(map[int]*RaftNode), isolatedNode: -1}
}

func (vn *VirtualNetwork) Register(node *RaftNode) {
	vn.mu.Lock(); defer vn.mu.Unlock(); vn.nodes[node.id] = node
}

func (vn *VirtualNetwork) Partition(nodeID int) {
	vn.mu.Lock(); defer vn.mu.Unlock(); vn.isolatedNode = nodeID
	fmt.Printf("\n>>> [NETWORK FAULT INJECTED] Node %d isolated into minority partition! <<<\n\n", nodeID)
}

func (vn *VirtualNetwork) Heal() {
	vn.mu.Lock(); defer vn.mu.Unlock(); vn.isolatedNode = -1
	fmt.Printf("\n>>> [NETWORK HEALED] All partition boundaries removed! <<<\n\n")
}

func (vn *VirtualNetwork) isBlocked(from, to int) bool {
	vn.mu.RLock(); defer vn.mu.RUnlock()
	return vn.isolatedNode != -1 && (from == vn.isolatedNode || to == vn.isolatedNode)
}

func (vn *VirtualNetwork) SendRequestVote(from, to int, args RequestVoteArgs) (RequestVoteReply, bool) {
	if vn.isBlocked(from, to) { return RequestVoteReply{}, false }
	vn.mu.RLock(); target, exists := vn.nodes[to]; vn.mu.RUnlock()
	if !exists { return RequestVoteReply{}, false }
	return target.HandleRequestVote(args), true
}

func (vn *VirtualNetwork) SendAppendEntries(from, to int, args AppendEntriesArgs) (AppendEntriesReply, bool) {
	if vn.isBlocked(from, to) { return AppendEntriesReply{}, false }
	vn.mu.RLock(); target, exists := vn.nodes[to]; vn.mu.RUnlock()
	if !exists { return AppendEntriesReply{}, false }
	return target.HandleAppendEntries(args), true
}

type RaftNode struct {
	mu            sync.Mutex
	id            int
	net           *VirtualNetwork
	peerIDs       []int
	currentTerm   int
	votedFor      int
	log           []LogEntry
	role          NodeRole
	commitIndex   int
	lastApplied   int
	lastHeartbeat time.Time
	nextIndex     map[int]int
	matchIndex    map[int]int
	stopCh        chan struct{}
}

func NewRaftNode(id int, peerIDs []int, net *VirtualNetwork) *RaftNode {
	rn := &RaftNode{
		id: id, net: net, peerIDs: peerIDs, votedFor: -1, role: Follower,
		lastHeartbeat: time.Now(), nextIndex: make(map[int]int), matchIndex: make(map[int]int), stopCh: make(chan struct{}),
	}
	rn.log = append(rn.log, LogEntry{Index: 0, Term: 0, Command: "<ROOT>"})
	net.Register(rn)
	return rn
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
			if rn.role != Leader && time.Since(rn.lastHeartbeat) >= timeout {
				rn.mu.Unlock(); rn.startElection()
			} else { rn.mu.Unlock() }
		}
	}
}

func (rn *RaftNode) startElection() {
	rn.mu.Lock()
	rn.role = Candidate; rn.currentTerm++; rn.votedFor = rn.id; rn.lastHeartbeat = time.Now()
	term := rn.currentTerm; last := rn.log[len(rn.log)-1]
	fmt.Printf("[Node %d] Timed out! Starting election for Term %d\n", rn.id, term)
	rn.mu.Unlock()

	var voteMu sync.Mutex
	votes := 1; quorum := ((len(rn.peerIDs) + 1) / 2) + 1

	for _, peerID := range rn.peerIDs {
		go func(pID int) {
			reply, ok := rn.net.SendRequestVote(rn.id, pID, RequestVoteArgs{
				Term: term, CandidateID: rn.id, LastLogIndex: last.Index, LastLogTerm: last.Term,
			})
			if !ok { return } // Dropped by partition router
			rn.mu.Lock(); defer rn.mu.Unlock()
			if reply.Term > rn.currentTerm { rn.stepDown(reply.Term); return }
			if rn.role == Candidate && reply.Term == rn.currentTerm && reply.VoteGranted {
				voteMu.Lock(); votes++; hasQuorum := votes >= quorum; voteMu.Unlock()
				if hasQuorum && rn.role != Leader {
					rn.role = Leader
					fmt.Printf("[Node %d] *** ELECTED LEADER for Term %d (received %d votes) ***\n", rn.id, rn.currentTerm, votes)
					nextIdx := rn.log[len(rn.log)-1].Index + 1
					for _, id := range rn.peerIDs { rn.nextIndex[id] = nextIdx; rn.matchIndex[id] = 0 }
					go rn.runHeartbeatLoop()
				}
			}
		}(peerID)
	}
}

func (rn *RaftNode) stepDown(term int) { rn.currentTerm = term; rn.role = Follower; rn.votedFor = -1 }

func (rn *RaftNode) runHeartbeatLoop() {
	ticker := time.NewTicker(40 * time.Millisecond); defer ticker.Stop()
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
	fmt.Printf("[Leader %d] Appended proposal: Idx=%d, Term=%d, Cmd='%s'\n", rn.id, entry.Index, entry.Term, command)
	rn.mu.Unlock(); rn.broadcastAppendEntries()
	return true
}

func (rn *RaftNode) broadcastAppendEntries() {
	rn.mu.Lock()
	if rn.role != Leader { rn.mu.Unlock(); return }
	term := rn.currentTerm; commit := rn.commitIndex; rn.mu.Unlock()

	for _, peerID := range rn.peerIDs {
		go func(pID int) {
			rn.mu.Lock()
			prevIdx := rn.nextIndex[pID] - 1; prevTerm := rn.log[prevIdx].Term
			entries := append([]LogEntry(nil), rn.log[prevIdx+1:]...)
			rn.mu.Unlock()

			reply, ok := rn.net.SendAppendEntries(rn.id, pID, AppendEntriesArgs{
				Term: term, LeaderID: rn.id, PrevLogIndex: prevIdx, PrevLogTerm: prevTerm, Entries: entries, LeaderCommit: commit,
			})
			if !ok { return }

			rn.mu.Lock(); defer rn.mu.Unlock()
			if reply.Term > rn.currentTerm { rn.stepDown(reply.Term); return }
			if rn.role != Leader || reply.Term != rn.currentTerm { return }

			if reply.Success {
				rn.matchIndex[pID] = reply.MatchIndex; rn.nextIndex[pID] = reply.MatchIndex + 1; rn.checkCommit()
			} else if rn.nextIndex[pID] > 1 { rn.nextIndex[pID]-- }
		}(peerID)
	}
}

func (rn *RaftNode) checkCommit() {
	quorum := ((len(rn.peerIDs) + 1) / 2) + 1
	for n := rn.log[len(rn.log)-1].Index; n > rn.commitIndex; n-- {
		if rn.log[n].Term != rn.currentTerm { continue }
		matches := 1
		for _, pID := range rn.peerIDs { if rn.matchIndex[pID] >= n { matches++ } }
		if matches >= quorum {
			rn.commitIndex = n
			fmt.Printf("[Leader %d] >>> COMMITTED Index %d to state machine via Quorum (%d/%d) <<<\n", rn.id, n, matches, len(rn.peerIDs)+1)
			for rn.commitIndex > rn.lastApplied {
				rn.lastApplied++; fmt.Printf("[Node %d StateMachine] Applied: Idx=%d, Cmd='%s'\n", rn.id, rn.lastApplied, rn.log[rn.lastApplied].Command)
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
	canVote := rn.votedFor == -1 || rn.votedFor == args.CandidateID

	if canVote && upToDate {
		rn.votedFor = args.CandidateID; rn.lastHeartbeat = time.Now()
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

	insertIdx := args.PrevLogIndex + 1; newIdx := 0
	for insertIdx < len(rn.log) && newIdx < len(args.Entries) {
		if rn.log[insertIdx].Term != args.Entries[newIdx].Term {
			fmt.Printf("[Node %d] Log conflict at Idx=%d! Truncating uncommitted suffix...\n", rn.id, insertIdx)
			rn.log = rn.log[:insertIdx]; break
		}
		insertIdx++; newIdx++
	}
	for newIdx < len(args.Entries) { rn.log = append(rn.log, args.Entries[newIdx]); newIdx++ }

	if args.LeaderCommit > rn.commitIndex {
		lastIdx := rn.log[len(rn.log)-1].Index
		if args.LeaderCommit < lastIdx { rn.commitIndex = args.LeaderCommit } else { rn.commitIndex = lastIdx }
		for rn.commitIndex > rn.lastApplied {
			rn.lastApplied++; fmt.Printf("[Node %d StateMachine] Applied: Idx=%d, Cmd='%s'\n", rn.id, rn.lastApplied, rn.log[rn.lastApplied].Command)
		}
	}
	return AppendEntriesReply{Term: rn.currentTerm, Success: true, MatchIndex: rn.log[len(rn.log)-1].Index}
}

func (rn *RaftNode) PrintLog() {
	rn.mu.Lock(); defer rn.mu.Unlock()
	fmt.Printf("Node %d (Role=%s, Term=%d, Commit=%d): ", rn.id, rn.role, rn.currentTerm, rn.commitIndex)
	for _, entry := range rn.log { fmt.Printf("[%d:T%d '%s'] ", entry.Index, entry.Term, entry.Command) }
	fmt.Println()
}

func main() {
	fmt.Println("================================================================")
	fmt.Println("    WEEK 32 LAB: RAFT NETWORK PARTITION & LOG RECONCILIATION   ")
	fmt.Println("================================================================")
	net := NewVirtualNetwork()
	n1 := NewRaftNode(1, []int{2, 3}, net)
	n2 := NewRaftNode(2, []int{1, 3}, net)
	n3 := NewRaftNode(3, []int{1, 2}, net)
	cluster := []*RaftNode{n1, n2, n3}
	for _, n := range cluster { n.Start() }

	fmt.Println("\n[PHASE 1] Initial Cluster Startup & Election (waiting 800ms)...")
	time.Sleep(800 * time.Millisecond)

	fmt.Println("\n[PHASE 2] Initial Client Write in Healthy Cluster...")
	for _, n := range cluster { if n.Propose("SET key1=healthy_val") { break } }
	time.Sleep(300 * time.Millisecond)

	fmt.Println("\n--- Cluster State Prior to Partition ---")
	for _, n := range cluster { n.PrintLog() }

	fmt.Println("\n[PHASE 3] Injecting Partition: Isolating Node 1...")
	net.Partition(1)
	time.Sleep(1000 * time.Millisecond) // Allow Node 2/3 to elect a new leader in Term 2

	fmt.Println("\n[PHASE 4] Attempting Write on Isolated Minority (Node 1)...")
	if n1.Propose("SET key1=stale_isolated_val") {
		fmt.Println("[Client] Sent write to Node 1. Awaiting commit...")
	}
	time.Sleep(300 * time.Millisecond) // Observe write failing to commit

	fmt.Println("\n[PHASE 5] Sending Write to Majority Cluster Leader...")
	for _, n := range []*RaftNode{n2, n3} { if n.Propose("SET key1=authoritative_majority_val") { break } }
	time.Sleep(300 * time.Millisecond) // Observe majority write committing

	fmt.Println("\n--- Cluster State During Partition ---")
	for _, n := range cluster { n.PrintLog() }

	fmt.Println("\n[PHASE 6] Healing Partition: Reconnecting Node 1...")
	net.Heal()
	time.Sleep(800 * time.Millisecond) // Allow heartbeats to reconcile logs

	fmt.Println("\n--- Final Reconciled Cluster State ---")
	for _, n := range cluster { n.PrintLog() }
	for _, n := range cluster { n.Stop() }
	fmt.Println("\nLab simulation completed successfully.")
}
```

---

## Day 3–4: Rust Production Implementation Lab

### The 3 Specific Rust Systems Challenges You Will Fight

When porting the Raft consensus engine to Rust, C# engineers consistently encounter three architectural friction points:

1. **Borrow Checker Contention on Mutable Slices (`truncate` and `drain`)**:
   In C#, modifying a `List<LogEntry>` inside a loop is straightforward (`_log.RemoveRange`). In Rust, holding an immutable reference to `guard.log[prev_idx].term` while simultaneously calling `guard.log.truncate(insert_idx)` triggers compilation error `E0502: cannot borrow *guard.log as mutable because it is also borrowed as immutable`. You must either copy the primitive `term` field out of the log before truncating, or explicitly structure index calculations to drop read references prior to mutation.

2. **Async Actor Loops vs. Deadlocking Shared Mutexes**:
   In C#, `lock (_lock)` allows nested reentrancy. In Rust with `tokio`, using `std::sync::Mutex` inside async blocks can deadlock the Tokio executor worker threads if a task holding a lock yields across an `.await` boundary. You must use `Arc<tokio::sync::Mutex<RaftState>>`, or better yet, implement the single-threaded Actor pattern using `tokio::sync::mpsc` channels to sequence state mutations.

3. **Cancellation Safety & Timer Resetting in `tokio::select!`**:
   In Go, `select` with `time.After` drops and creates timers on each tick. In Rust Tokio, instantiating `sleep` inside a loop without pinning can lead to timer leaks. You must use `tokio::pin!` on `tokio::time::sleep` or use `tokio::time::Interval` to ensure timer cancellations do not drop in-flight RPC responses unexpectedly.

### Rust Starter Skeleton (`src/main.rs`)

```rust
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NodeRole { Follower, Candidate, Leader }

#[derive(Clone, Debug)]
pub struct LogEntry { pub index: usize, pub term: usize, pub command: String }

#[derive(Clone, Debug)]
pub struct AppendEntriesArgs {
    pub term: usize, pub leader_id: usize, pub prev_log_index: usize,
    pub prev_log_term: usize, pub entries: Vec<LogEntry>, pub leader_commit: usize,
}

#[derive(Clone, Debug)]
pub struct AppendEntriesReply { pub term: usize, pub success: bool, pub match_index: usize }

pub struct RaftState {
    pub id: usize, pub current_term: usize, pub voted_for: Option<usize>, pub log: Vec<LogEntry>,
    pub role: NodeRole, pub commit_index: usize, pub last_applied: usize,
    pub next_index: HashMap<usize, usize>, pub match_index: HashMap<usize, usize>,
}

pub struct RaftNode {
    pub state: Arc<Mutex<RaftState>>,
    pub peers: Vec<usize>,
}

impl RaftNode {
    // TODO 1: Implement the Log Consistency & Truncation check
    // Rust Concept to fight: Avoid E0502 (simultaneous mutable & immutable borrows).
    // Extract prev_log_term by value BEFORE modifying self.log.
    pub async fn handle_append_entries(&self, args: AppendEntriesArgs) -> AppendEntriesReply {
        let mut guard = self.state.lock().await;

        if args.term < guard.current_term {
            return AppendEntriesReply { term: guard.current_term, success: false, match_index: 0 };
        }

        // STEP 1: Handle term updates
        if args.term > guard.current_term || guard.role != NodeRole::Follower {
            guard.current_term = args.term;
            guard.role = NodeRole::Follower;
            guard.voted_for = None;
        }

        // STEP 2: Log matching consistency check
        if args.prev_log_index >= guard.log.len() || guard.log[args.prev_log_index].term != args.prev_log_term {
            return AppendEntriesReply { term: guard.current_term, success: false, match_index: guard.log.last().unwrap().index };
        }

        // TODO 2: Perform log reconciliation (truncate conflicting suffix, append new entries)
        let mut insert_idx = args.prev_log_index + 1;
        let mut new_idx = 0;
        while insert_idx < guard.log.len() && new_idx < args.entries.len() {
            if guard.log[insert_idx].term != args.entries[new_idx].term {
                guard.log.truncate(insert_idx);
                break;
            }
            insert_idx += 1;
            new_idx += 1;
        }

        while new_idx < args.entries.len() {
            guard.log.push(args.entries[new_idx].clone());
            new_idx += 1;
        }

        // STEP 3: Advance commit index
        if args.leader_commit > guard.commit_index {
            guard.commit_index = std::cmp::min(args.leader_commit, guard.log.last().unwrap().index);
        }

        AppendEntriesReply {
            term: guard.current_term,
            success: true,
            match_index: guard.log.last().unwrap().index,
        }
    }
}

#[tokio::main]
async fn main() {
    println!("=== Rust Raft Starter Skeleton ===");
    // Team Task: Implement the VirtualNetwork channel router and execute the partition simulation!
}
```

---

## Friday: Mob Review, Benchmarks & Validation

### Benchmarking Commands
To measure failover time and log replication throughput under simulated network latency:

```bash
# 1. Run the Go partition lab with race detector enabled
go run -race cluster_lab.go

# 2. Run throughput benchmarks with simulated 10ms network delay
go test -bench=BenchmarkRaftReplication -benchmem -cpu=4

# 3. Compile and test the Rust implementation with release optimizations
cargo test --release -- --nocapture
```

### Empirical Comparison Matrix
Fill in the metrics gathered during your mob review:

| Cluster Scenario | Active Term | Active Leader | Quorum Achieved? (Y/N) | Write Latency (ms) | Data Reconciled? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Healthy 3-Node Cluster** | 1 | Node 1 | Yes (3/3 nodes) | ~5ms - 15ms | Yes |
| **Minority Partition (Node 1)** | 1 | Node 1 (Stale) | No (1/3 nodes) | Timeout / Stalled | Uncommitted |
| **Majority Partition (Nodes 2 & 3)** | 2 | Node 2 or 3 | Yes (2/3 nodes) | ~10ms - 20ms | Committed |
| **Partition Healed & Reconciled** | 2 | Node 2 or 3 | Yes (3/3 nodes) | ~5ms - 10ms | Overwritten & Synced |

---

### Technical Discussion Questions & Authoritative Solutions

#### 1. Why does Raft use randomized election timeouts to solve the FLP Impossibility Theorem?
**Answer**: FLP proves that in an asynchronous network, no deterministic protocol can guarantee termination with even one crash failure. Symmetrical election timers cause split-vote deadlocks where all nodes time out simultaneously and divide the votes equally. Randomizing timeouts ($150\text{ms} - 300\text{ms}$) introduces probabilistic asymmetry: with near certainty, one node times out first, solicits votes, and achieves a majority before competitors wake up, circumventing FLP without requiring synchronized hardware clocks.

#### 2. What exact mechanism caused Node 1 to discard its uncommitted write (`SET key1=stale_isolated_val`) when the partition healed?
**Answer**: When the partition healed, the new leader (Node 2) sent `AppendEntries` with `prevLogIndex = 1` and `prevLogTerm = 1`. Node 1's log at index 2 had `Term = 1` (`stale_isolated_val`), whereas Node 2's `entries` had `Term = 2` (`authoritative_majority_val`). Node 1 detected the term conflict at index 2, truncated its local log from index 2 onwards, and appended Node 2's entry, strictly preserving the Log Matching Invariant.

#### 3. Why does a 4-node cluster provide strictly worse write availability than a 3-node cluster?
**Answer**: A 3-node cluster requires $\lfloor 3/2 \rfloor + 1 = 2$ nodes for quorum, tolerating 1 failure. A 4-node cluster requires $\lfloor 4/2 \rfloor + 1 = 3$ nodes for quorum, also tolerating only 1 failure ($4 - 1 = 3 \ge 3$; 2 failures leave 2 nodes, which is strictly less than 3). However, because a 4-node cluster requires an additional network RPC round-trip to achieve quorum, write latency increases and the probability of a node failure impairing quorum increases, making 4 nodes worse than 3.

#### 4. How does async fan-out in C# (`Task.WhenAll`) differ from Go's `sync.WaitGroup` and Rust's `JoinSet`?
**Answer**: In C#, `Task.WhenAll` allocates task completion objects on the CLR heap and relies on the global ThreadPool. If the ThreadPool is exhausted, completions stall. In Go, spawning goroutines with `sync.WaitGroup` creates lightweight 2KB stacks managed by the runtime M:N netpoller with minimal overhead. In Rust, `tokio::task::JoinSet` or `futures::future::join_all` schedules futures directly on the async task queue with zero heap allocations for the synchronization barrier, eliminating GC pauses completely.

#### 5. If a client issues a `GET key1` read request to Node 1 during the partition, what happens, and how do production systems solve it?
**Answer**: Without read consensus, Node 1 returns stale data (`healthy_val` or uncommitted `stale_isolated_val`), violating linearizability. Production systems solve this via **ReadIndex**: before serving a read, the leader records its `commitIndex` and exchanges a heartbeat with a majority quorum to verify it has not been deposed. Only after quorum confirmation does it execute the read against its state machine.

#### 6. What is the "Figure 8" edge case from the Raft paper, and why does rule 5.4.2 forbid committing prior-term entries by replica count?
**Answer**: If a leader replicates an entry from an older term to a majority of nodes and crashes before committing it, an alternate leader could be elected in a subsequent term and legally overwrite that entry if the older entry was not committed through a current-term write. Therefore, Raft dictates that leaders only commit entries from their **current term** by counting replicas; committing a current-term entry indirectly commits all prior entries by induction.

#### 7. How does the Pre-Vote protocol prevent an isolated node from disrupting a healthy cluster upon reconnection?
**Answer**: Without Pre-Vote, an isolated node increments its term repeatedly (e.g. to Term 50) while partitioned. When reconnected, its high term forces the active leader to immediately step down, causing unnecessary cluster downtime. Pre-Vote adds a speculative phase: a node only increments its term if a majority of peers acknowledge that an election is warranted (i.e., their own leader heartbeats have timed out).

---

## Team Sign-Off Checklist

Before completing this lab, every team member must independently verify and answer:

- [ ] **1. Quorum Verification**: Can you prove mathematically why an isolated node in a 3-node cluster cannot commit writes? (Answer: $1 < \lfloor 3/2 \rfloor + 1 = 2$).
- [ ] **2. Log Reconciliation**: Did you verify in console logs that the conflicting entry on Node 1 was explicitly truncated and overwritten?
- [ ] **3. Stepping Down**: Did Node 1 immediately abdicate leadership upon receiving an RPC with a higher term?
- [ ] **4. Lock Safety**: Does your implementation drop all locks before initiating network RPCs to prevent deadlocks?
- [ ] **5. Invariant Preservation**: Did the committed state machine output remain identical across all non-faulty nodes after partition healing?
- [ ] **6. Production Sizing**: Can every engineer explain why consensus clusters are always sized with odd node counts ($3, 5, 7$)?

---

## Stretch Goals for Fast Learners

1. **Implement Pre-Vote**: Extend the `VirtualNetwork` and `RaftNode` to implement the Pre-Vote algorithm, verifying that an isolated node does not bump its term while partitioned.
2. **Implement Linearizable ReadIndex**: Add a `Read(key string) (string, error)` method that broadcasts empty heartbeat RPCs to verify majority lease before returning state machine data.
3. **Persist State to Disk (WAL Engine)**: Integrate the Write-Ahead Log from Week 31, flushing `currentTerm`, `votedFor`, and `log[]` to disk via `fsync` before replying to any RPC.
