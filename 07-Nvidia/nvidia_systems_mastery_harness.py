#!/usr/bin/env python3
"""
NVIDIA Hardware, Silicon Interlinks & Systems Engineering Mastery Test Harness.
Executes 30 comprehensive architectural, mathematical, and diagnostic verification
challenges corresponding directly to Volumes 01 through 30 in 07-Nvidia.
"""

import math
import sys

def test_all_30_hardware_challenges():
    print("=" * 85)
    print("STARTING NVIDIA HARDWARE & LOW-LEVEL SYSTEMS MASTERY TEST HARNESS (VOLUMES 01-30)")
    print("=" * 85)
    passed = 0

    # -------------------------------------------------------------------------
    # Challenge 01: Reticle Area & CoWoS-L NV-HBI Bisection Bandwidth (Volume 01)
    # -------------------------------------------------------------------------
    # ASML scanner field: 26mm x 33mm = 858 mm^2.
    # Blackwell dual-die NV-HBI: 4096 bidirectional wires @ 10 GHz SDR across LSI bridge.
    wires = 4096
    freq = 10.0e9  # 10 GHz
    bidirectional_factor = 2
    bw_bytes_sec = (wires * freq / 8.0) * bidirectional_factor
    bw_tb_sec = bw_bytes_sec / 1.0e12
    assert 10.0 <= bw_tb_sec <= 10.5, f"Expected ~10.24 TB/s, got {bw_tb_sec}"
    print("Challenge 01 [Silicon Reticle & NV-HBI 10 TB/s Math]      : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 02: SM Occupancy & Register File Limits (Volume 02)
    # -------------------------------------------------------------------------
    # SM has 65,536 registers. A kernel uses 64 registers/thread. 32 threads/warp.
    # SMem is 128 KB, block uses 48 KB (512 threads = 16 warps/block).
    total_regs = 65536
    regs_per_thread = 64
    threads_per_warp = 32
    max_warps_regs = total_regs // (regs_per_thread * threads_per_warp) # 32 warps
    smem_cap_kb = 128
    smem_per_block_kb = 48
    blocks_smem = smem_cap_kb // smem_per_block_kb # 2 blocks
    max_warps_smem = blocks_smem * 16 # 32 warps
    active_warps = min(48, max_warps_regs, max_warps_smem)
    assert active_warps == 32
    print("Challenge 02 [SM Occupancy & Register File Allocation]    : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 03: HBM3e Bus Width & Arithmetic Intensity Inflexion (Volume 03)
    # -------------------------------------------------------------------------
    # 8 stacks of HBM3e @ 1024 bits = 8192 bits total bus width.
    # Pin speed = 7.8125 Gbps. Total bandwidth = 8.0 TB/s.
    # B200 Compute Peak = 4,500 TFLOPS (FP8).
    bus_width = 8 * 1024
    pin_speed = 7.8125e9
    peak_bw = (bus_width * pin_speed) / 8.0 # 8.0 TB/s
    peak_compute = 4500.0e12 # 4,500 TFLOPS
    i_crit = peak_compute / peak_bw # FLOPs per byte
    assert abs(i_crit - 562.5) < 1e-3
    print("Challenge 03 [HBM3e 8192-bit Bus & Roofline Inflexion]     : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 04: Bit-Exact FP8 E4M3 vs. E5M2 Value Decoding (Volume 04)
    # -------------------------------------------------------------------------
    # Decode byte 0b01011100 in FP8 E4M3:
    # Sign = 0, Exp = 0b1011 = 11, Bias = 7, Actual Exp = 4.
    # Mantissa = 0b100 = 1.5. Value = 1.5 * 2^4 = 24.0.
    byte_val = 0b01011100
    s = (byte_val >> 7) & 0x1
    e = (byte_val >> 3) & 0xF
    m = byte_val & 0x7
    unscaled_val = ((-1)**s) * (2**(e - 7)) * (1.0 + m / 8.0)
    assert abs(unscaled_val - 24.0) < 1e-6
    print("Challenge 04 [Bit-Exact FP8 E4M3 Numerical Decoding]      : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 05: NVLink-C2C 900 GB/s vs PCIe Gen 5 Latency (Volume 05)
    # -------------------------------------------------------------------------
    # Checkpoint size = 400 GB.
    # PCIe Gen 5 effective = 64 * 0.90 = 57.6 GB/s.
    # NVLink-C2C effective = 900 * 0.92 = 828.0 GB/s.
    model_gb = 400.0
    t_pcie = model_gb / 57.6
    t_c2c = model_gb / 828.0
    speedup_c2c = t_pcie / t_c2c
    assert 14.0 <= speedup_c2c <= 15.0
    print("Challenge 05 [NVLink-C2C 900 GB/s vs. PCIe Gen 5 Speedup]  : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 06: GSP RPC Queue Latency vs. Legacy Driver Traps (Volume 06)
    # -------------------------------------------------------------------------
    # 10,000 allocations. Legacy = 15us trap. GSP = 2.5us shared-memory RPC.
    allocs = 10000
    t_legacy = allocs * 15.0e-6
    t_gsp = allocs * 2.5e-6
    saved_ms = (t_legacy - t_gsp) * 1000.0
    assert abs(saved_ms - 125.0) < 1e-4
    print("Challenge 06 [GSP RISC-V RPC Latency vs. Kernel Traps]    : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 07: UVM Demand Page Fault Storm vs. Prefetch (Volume 07)
    # -------------------------------------------------------------------------
    # 4 GB array = 1,048,576 pages (4 KB). Demand fault = 20us. Bulk C2C = 4GB / 900GB/s.
    pages = 1048576
    t_demand_faults = pages * 20.0e-6 # ~20.97 seconds
    t_bulk_prefetch = 4.0 / 900.0     # ~0.00444 seconds
    ratio = t_demand_faults / t_bulk_prefetch
    assert ratio > 4000.0
    print("Challenge 07 [UVM Hardware Page Fault Storm vs. Prefetch]  : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 08: cuMem Virtual Address Decoupled Paging (Volume 08)
    # -------------------------------------------------------------------------
    # PagedAttention KV-cache: Virtual reservation 64 GB. Chunks allocated = 64 MB.
    virtual_reserve_gb = 64
    chunk_size_mb = 64
    total_physical_chunks = (virtual_reserve_gb * 1024) // chunk_size_mb
    assert total_physical_chunks == 1024
    print("Challenge 08 [cuMem Decoupled Virtual Memory Chunking]    : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 09: Power Capping Linear Compute Scaling (Volume 09)
    # -------------------------------------------------------------------------
    # H100 SXM5 700W delivers 1979 TFLOPS. Capped to 500W.
    base_power = 700.0
    capped_power = 500.0
    base_tflops = 1979.0
    scaled_tflops = base_tflops * (capped_power / base_power)
    assert 1400.0 <= scaled_tflops <= 1420.0
    print("Challenge 09 [NVML Power Capping Compute Scaling Ratio]    : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 10: MIG 1g.10gb Guaranteed Bandwidth Slicing (Volume 10)
    # -------------------------------------------------------------------------
    # 5 stacks HBM3 = 3.35 TB/s. 7 instances of 1g.10gb.
    h100_bw_tb = 3.35
    mig_slice_bw_gb = (h100_bw_tb * 1000.0) / 7.0
    assert 475.0 <= mig_slice_bw_gb <= 480.0
    print("Challenge 10 [MIG 1g.10gb Physical Hardware Slicing Math]  : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 11: Register Spilling Local Memory DRAM Traffic (Volume 11)
    # -------------------------------------------------------------------------
    # 64 bytes spill store + 64 bytes spill load = 128 bytes/thread. 1e8 threads.
    spill_bytes_thread = 128
    threads = 100000000
    spill_traffic_gb = (threads * spill_bytes_thread) / 1.0e9
    assert abs(spill_traffic_gb - 12.8) < 1e-4
    print("Challenge 11 [SASS Register Spilling Memory Penalty]       : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 12: Memory Coalescing Sector Alignment Check (Volume 12)
    # -------------------------------------------------------------------------
    # Aligned 128-byte warp load = 16 sectors (32 bytes each).
    # Misaligned load (+4 bytes) straddles sector boundaries = 17 to 18 sectors.
    aligned_sectors = 16
    misaligned_sectors = 18
    overhead_pct = ((misaligned_sectors - aligned_sectors) / aligned_sectors) * 100.0
    assert abs(overhead_pct - 12.5) < 1e-3
    print("Challenge 12 [compute-sanitizer Memory Sector Misalignment]: PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 13: CUDA Stream Pipeline Latency Overlap (Volume 13)
    # -------------------------------------------------------------------------
    # Serialized: Copy (4ms) + Compute (2ms) = 6ms.
    # Overlapped dual streams: max(4ms, 2ms) = 4ms. Speedup = 6ms / 4ms = 1.5x.
    t_copy = 4.0
    t_kernel = 2.0
    t_serial = t_copy + t_kernel
    t_pipelined = max(t_copy, t_kernel)
    assert abs(t_serial / t_pipelined - 1.5) < 1e-4
    print("Challenge 13 [Nsight Systems Stream Concurrency Speedup]   : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 14: Nsight Compute Roofline Bound Classification (Volume 14)
    # -------------------------------------------------------------------------
    # Kernel performs 1.2e12 FLOPs, moves 800 MB. I = 1,500 FLOPs/Byte.
    # Machine H100: 989 TFLOPS / 3.35 TB/s => I_crit = 295.2.
    i_kernel = 1.2e12 / 800.0e6
    i_crit_h100 = 989.0e12 / 3.35e12
    is_compute_bound = i_kernel > i_crit_h100
    assert is_compute_bound is True
    print("Challenge 14 [Nsight Compute Roofline Inflexion Analysis]  : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 15: CUTLASS SwiGLU Epilogue Fusion HBM Savings (Volume 15)
    # -------------------------------------------------------------------------
    # M=4096, N=16384, BF16 (2 bytes). Activation = 128 MB.
    # Unfused: GEMM writes (128MB) + GELU reads (128MB) + GELU writes (128MB) = 384MB.
    # Fused: GEMM writes directly = 128MB. Savings = 256MB (66.7%).
    act_size_mb = 128.0
    unfused_mb = act_size_mb * 3.0
    fused_mb = act_size_mb
    savings_pct = ((unfused_mb - fused_mb) / unfused_mb) * 100.0
    assert abs(savings_pct - 66.6666) < 1e-3
    print("Challenge 15 [CUTLASS 3.x Epilogue Fusion Bandwidth Save]  : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 16: NVLink 5 PAM4 SerDes Bisection Bandwidth (Volume 16)
    # -------------------------------------------------------------------------
    # 18 ports @ 100 GB/s bidirectional per port = 1.8 TB/s.
    ports = 18
    port_bw_gb = 100.0
    gpu_bw_tb = (ports * port_bw_gb) / 1000.0
    assert abs(gpu_bw_tb - 1.8) < 1e-4
    print("Challenge 16 [NVLink 5 PAM4 SerDes 1.8 TB/s Aggregation]   : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 17: GB200 NVL72 Copper Spine Bisection Bandwidth (Volume 17)
    # -------------------------------------------------------------------------
    # 72 GPUs * 1.8 TB/s = 129.6 TB/s (~130 TB/s).
    total_gpus = 72
    rack_bw_tb = total_gpus * 1.8
    assert abs(rack_bw_tb - 129.6) < 1e-3
    print("Challenge 17 [GB200 NVL72 Copper Spine 130 TB/s Fabric]    : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 18: InfiniBand vs. TCP AllReduce Speedup (Volume 18)
    # -------------------------------------------------------------------------
    # TCP latency = 12.5 ms (12,500 us). InfiniBand NDR + SHARP = 180 us.
    t_tcp = 12500.0
    t_ib = 180.0
    speedup_ib = t_tcp / t_ib
    assert speedup_ib > 65.0
    print("Challenge 18 [Quantum InfiniBand NDR vs. TCP AllReduce]    : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 19: GPUDirect Storage (cuFile) DMA Acceleration (Volume 19)
    # -------------------------------------------------------------------------
    # 100 GB checkpoint. POSIX = 12.5 GB/s (8.0s). GDS = 46.0 GB/s (2.174s).
    chk_gb = 100.0
    t_posix = chk_gb / 12.5
    t_gds = chk_gb / 46.0
    time_saved = t_posix - t_gds
    assert 5.8 <= time_saved <= 5.9
    print("Challenge 19 [GPUDirect Storage cuFile Direct DMA Math]    : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 20: NCCL Bus Bandwidth vs. Algorithm Bandwidth (Volume 20)
    # -------------------------------------------------------------------------
    # 8 ranks: Correction factor = 2(8-1)/8 = 14/8 = 1.75.
    # AlgBW = 222.22 GB/s => BusBW = 388.89 GB/s.
    n_ranks = 8
    f_factor = (2.0 * (n_ranks - 1)) / n_ranks
    algbw = 222.2222
    busbw = algbw * f_factor
    assert abs(busbw - 388.8888) < 1e-2
    print("Challenge 20 [NCCL Ring BusBW 2(N-1)/N Correction Factor] : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 21: BlueField-3 DPU CPU Offload Core Savings (Volume 21)
    # -------------------------------------------------------------------------
    # 128-core host. Infrastructure consumes 28%. Offloading frees 35.84 (~36 cores).
    host_cores = 128
    infra_pct = 0.28
    liberated_cores = round(host_cores * infra_pct)
    assert liberated_cores == 36
    print("Challenge 21 [BlueField-3 DPU Infrastructure Core Offload] : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 22: 54V DC Joule's Law I^2R Loss Reduction Math (Volume 22)
    # -------------------------------------------------------------------------
    # 120 kW rack. At 12V: 10,000A. At 54V: 2,222.2A.
    # Loss reduction ratio = (54 / 12)^2 = (4.5)^2 = 20.25x.
    i_12v = 120000.0 / 12.0
    i_54v = 120000.0 / 54.0
    ratio_loss = (i_12v / i_54v) ** 2
    assert abs(ratio_loss - 20.25) < 1e-4
    print("Challenge 22 [54V Busbar 20.25x Resistive Loss Reduction]  : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 23: DCGM Profiling Active Tensor FLOPs Derivation (Volume 23)
    # -------------------------------------------------------------------------
    # H100 SXM5 1979 TFLOPS. DCGM_FI_PROF_PIPE_TENSOR_ACTIVE = 0.62 => 1226.98 TFLOPS.
    tensor_pipe_util = 0.62
    achieved_tflops = 1979.0 * tensor_pipe_util
    assert abs(achieved_tflops - 1226.98) < 1e-3
    print("Challenge 23 [DCGM FID 1003 Tensor Pipe Utilization Math]  : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 24: Kernel XID Error Categorization & Forensic Map (Volume 24)
    # -------------------------------------------------------------------------
    # Map XIDs to fault domains
    xid_map = {
        31: "PAGE_FAULT",
        43: "GPU_STOPPED_RESPONDING",
        62: "INTERNAL_SRAM_UNCORRECTABLE",
        79: "FELL_OFF_BUS",
        92: "NVLINK_SERDES_FAIL"
    }
    assert xid_map[62] == "INTERNAL_SRAM_UNCORRECTABLE"
    assert xid_map[79] == "FELL_OFF_BUS"
    print("Challenge 24 [Kernel NVRM XID Forensic Classification]     : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 25: InfiniBand PortXmitWait vs SymbolError Triage (Volume 25)
    # -------------------------------------------------------------------------
    # If PortXmitWait > 0 and SymbolErrors == 0: Congestion / Credit Stall.
    # If SymbolErrors > 0: Physical optical transceiver / cable degradation.
    def diagnose_ib(port_xmit_wait, symbol_errors):
        if port_xmit_wait > 0 and symbol_errors == 0:
            return "DOWNSTREAM_CONGESTION_OR_STALL"
        elif symbol_errors > 0:
            return "PHYSICAL_CABLE_OR_OPTICAL_FAULT"
        return "HEALTHY"

    assert diagnose_ib(1000000, 0) == "DOWNSTREAM_CONGESTION_OR_STALL"
    assert diagnose_ib(0, 42) == "PHYSICAL_CABLE_OR_OPTICAL_FAULT"
    print("Challenge 25 [InfiniBand perfquery Credit vs Symbol Triage]: PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 26: PagedAttention Block Sizing & In-Flight Batching (Volume 26)
    # -------------------------------------------------------------------------
    # Llama-3-70B: L=80, H_kv=8, D=128, prec=2.0 bytes (FP16).
    # Per-token KV cache = 2 * 2 * 80 * 8 * 128 = 327,680 bytes.
    # At TP=8, per-GPU per-token footprint = 40,960 bytes.
    # In-Flight batching speedup with L_min=50, L_max=2000:
    # Speedup = 2 * L_max / (L_min + L_max) = 4000 / 2050 = 1.9512x.
    per_token_total = 2 * 2 * 80 * 8 * 128
    assert per_token_total == 327680
    per_token_gpu = per_token_total / 8
    assert per_token_gpu == 40960
    ifb_speedup = (2.0 * 2000.0) / (50.0 + 2000.0)
    assert abs(ifb_speedup - 1.9512) < 1e-3
    print("Challenge 26 [PagedAttention Block Footprint & In-Flight Speedup]: PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 27: Megatron-Core 3D State & 1F1B Bubble Math (Volume 27)
    # -------------------------------------------------------------------------
    # 70B Model, ZeRO-1: TP=8, PP=4, DP=2 (64 GPUs).
    # Sharded Model State = (2*70B/32) + (2*70B/32) + (4*70B/64) + (8*70B/64)
    # = 4.375B + 4.375B + 4.375B + 8.75B = 21.875B bytes = 20.3727 GiB.
    # 1F1B Bubble Fraction: p=4, m=32 => (4-1) / (32 + 4 - 1) = 3/35 = 8.5714%.
    phi = 70.0 * 1e9
    weights_b = (2 * phi) / 32
    grads_b = (2 * phi) / 32
    master_b = (4 * phi) / 64
    opt_b = (8 * phi) / 64
    total_model_gib = (weights_b + grads_b + master_b + opt_b) / (1024**3)
    assert abs(total_model_gib - 20.3727) < 1e-2
    bubble_fraction = (4 - 1) / (32 + 4 - 1)
    assert abs(bubble_fraction - 0.085714) < 1e-4
    print("Challenge 27 [Megatron-Core 3D Model State & 1F1B Bubble Math] : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 28: Triton Dynamic Batching Turnaround Optimization (Volume 28)
    # -------------------------------------------------------------------------
    # alpha=8.0ms, beta=0.5ms/item. D_max=5ms, arrival lambda=2000 RPS.
    # Expected batch = lambda * D_max = 2000 * 0.005 = 10 items.
    # Turnaround = (D_max / 2) + alpha + beta * batch = 2.5 + 8.0 + 0.5 * 10 = 15.5ms.
    d_max = 5.0
    alpha = 8.0
    beta = 0.5
    arrival_rate = 2000.0
    batch_size = arrival_rate * (d_max / 1000.0)
    assert batch_size == 10.0
    turnaround_ms = (d_max / 2.0) + alpha + (beta * batch_size)
    assert turnaround_ms == 15.5
    print("Challenge 28 [Triton Dynamic Batching Turnaround Sizing Math]   : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 29: Slurm Topology-Aware Leaf vs. Spine Penalty (Volume 29)
    # -------------------------------------------------------------------------
    # Single-switch leaf latency = 1.5 us. Cross-spine traversal adds 2*0.5us = 1.0us.
    # If 50% traffic traverses spine (k/N = 0.5), base latency = 1.5 + 0.5*1.0 = 2.0 us.
    # Relative latency degradation = (2.0 - 1.5) / 1.5 = +33.33%.
    t_local = 1.5
    delta_hop = 0.5 * 2 # round-trip spine
    t_hybrid = t_local + (0.5 * delta_hop)
    degradation = (t_hybrid - t_local) / t_local
    assert abs(degradation - 0.3333) < 1e-3
    print("Challenge 29 [Slurm Topology-Aware Leaf Locality Penalty Math]  : PASSED")
    passed += 1

    # -------------------------------------------------------------------------
    # Challenge 30: RoCE DCQCN ECN Threshold Sizing Formulation (Volume 30)
    # -------------------------------------------------------------------------
    # Line speed C = 400 Gbps (50 GB/s). RTT = 5 us.
    # BDP = 50e9 * 5e-6 = 250,000 bytes.
    # K_min = BDP / 2 = 125,000 bytes.
    # K_max = 3 * K_min = 375,000 bytes.
    # PFC high-water mark > K_max + BDP = 375,000 + 250,000 = 625,000 bytes.
    c_bytes_sec = 50.0 * 1e9
    rtt_sec = 5.0 * 1e-6
    bdp_bytes = c_bytes_sec * rtt_sec
    assert abs(bdp_bytes - 250000.0) < 1e-3
    k_min = bdp_bytes / 2.0
    k_max = 3.0 * k_min
    pfc_hwm = k_max + bdp_bytes
    assert abs(k_min - 125000.0) < 1e-3
    assert abs(k_max - 375000.0) < 1e-3
    assert abs(pfc_hwm - 625000.0) < 1e-3
    print("Challenge 30 [RoCE DCQCN ECN Threshold Sizing Formulation]     : PASSED")
    passed += 1

    print("=" * 85)
    print(f"ALL 30 HARDWARE & SYSTEMS CHALLENGES VERIFIED: {passed}/30 PASSED (100% SUCCESS)")
    print("=" * 85)

if __name__ == "__main__":
    test_all_30_hardware_challenges()

