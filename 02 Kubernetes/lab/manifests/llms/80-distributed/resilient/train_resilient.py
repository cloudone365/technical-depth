#!/usr/bin/env python3
"""Checkpoint/resume + silent-data-corruption canary (Volume 26).

- trains a small model on synthetic data (single rank, GPU)
- async distributed checkpoint (torch.distributed.checkpoint.async_save) every
  CKPT_EVERY steps into /ckpt/step-N, keeping the last KEEP
- on start, resumes from the newest complete checkpoint
- every CANARY_EVERY steps, runs a fixed deterministic GEMM and compares its
  checksum with the value recorded on first run → flags silent data corruption
Env: STEPS (default 400), CKPT_EVERY (50), KEEP (3), CANARY_EVERY (25), STEP_SLEEP (0.05)
"""
import glob
import hashlib
import json
import os
import shutil
import time

import torch
import torch.distributed as dist
import torch.distributed.checkpoint as dcp

STEPS = int(os.environ.get("STEPS", "400"))
CKPT_EVERY = int(os.environ.get("CKPT_EVERY", "50"))
KEEP = int(os.environ.get("KEEP", "3"))
CANARY_EVERY = int(os.environ.get("CANARY_EVERY", "25"))
STEP_SLEEP = float(os.environ.get("STEP_SLEEP", "0.05"))
ROOT = os.environ.get("CKPT_DIR", "/ckpt")
DEV = "cuda" if torch.cuda.is_available() else "cpu"     # CPU fallback = laptop/CI dry run


def canary():
    g = torch.Generator(device=DEV).manual_seed(1234)
    a = torch.randn(2048, 2048, device=DEV, generator=g, dtype=torch.float32)
    torch.backends.cuda.matmul.allow_tf32 = False
    c = (a @ a.T).sum(dim=0)
    return hashlib.sha256(c.cpu().numpy().tobytes()).hexdigest()[:16]


def latest():
    done = sorted(glob.glob(f"{ROOT}/step-*/COMPLETE"), key=lambda p: int(p.split("step-")[1].split("/")[0]))
    return os.path.dirname(done[-1]) if done else None


def prune():
    """Keep the newest KEEP *complete* checkpoints; never touch the one being written."""
    done = sorted((os.path.dirname(p) for p in glob.glob(f"{ROOT}/step-*/COMPLETE")),
                  key=lambda p: int(p.split("step-")[1]))
    for old in done[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)


def main():
    dist.init_process_group("gloo", init_method="tcp://127.0.0.1:29511", rank=0, world_size=1)
    torch.manual_seed(0)
    model = torch.nn.Sequential(torch.nn.Linear(1024, 4096), torch.nn.GELU(), torch.nn.Linear(4096, 1024)).to(DEV)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
    state = {"model": model.state_dict(), "optim": opt.state_dict(), "step": 0}
    ck = latest()
    if ck:
        dcp.load(state, checkpoint_id=ck)
        model.load_state_dict(state["model"]); opt.load_state_dict(state["optim"])
        print(f"RESUMED from {ck} at step {state['step']}", flush=True)
    ref_file = f"{ROOT}/canary.json"
    ref = json.load(open(ref_file))["sha"] if os.path.exists(ref_file) else None
    if ref is None:
        ref = canary(); os.makedirs(ROOT, exist_ok=True); json.dump({"sha": ref}, open(ref_file, "w"))
        print(f"canary reference recorded: {ref}", flush=True)
    pending = None
    step = int(state["step"])
    while step < STEPS:
        step += 1
        x = torch.randn(64, 1024, device=DEV)
        loss = torch.nn.functional.mse_loss(model(x), x)
        opt.zero_grad(); loss.backward(); opt.step()
        time.sleep(STEP_SLEEP)
        if step % CANARY_EVERY == 0:
            got = canary()
            status = "OK" if got == ref else "SDC SUSPECTED"
            print(f"step {step} loss {loss.item():.4f} canary {got} {status}", flush=True)
            if got != ref:
                raise SystemExit(86)          # podFailurePolicy: FailJob → quarantine the node
        if step % CKPT_EVERY == 0:
            if pending is not None:
                pending.result()              # previous async save must finish first
                open(f"{prev_dir}/COMPLETE", "w").close()
            prev_dir = f"{ROOT}/step-{step}"
            t0 = time.perf_counter()
            state = {"model": model.state_dict(), "optim": opt.state_dict(), "step": step}
            pending = dcp.async_save(state, checkpoint_id=prev_dir)
            print(f"step {step} checkpoint started (blocking {1e3*(time.perf_counter()-t0):.0f} ms)", flush=True)
            prune()
    if pending is not None:
        pending.result(); open(f"{prev_dir}/COMPLETE", "w").close()
        prune()
    print(f"DONE at step {step}", flush=True)
    dist.destroy_process_group()


if __name__ == "__main__":
    main()
