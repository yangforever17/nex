"""Controlled conformance experiment for semantic retirement and neural exceptions.

The experiment deliberately contains no model sampling.  It generates effect
dependency DAGs, injects semantic provenance at several consumer nodes, and
compares two independent computations of a rejected prediction's dynamic
invalidation cone:

* the runtime path propagates provenance forward in topological order; and
* the oracle path performs graph reachability from the prediction's direct
  consumers.

Only nodes executed by the time a delayed guard rejects are considered.  This
is the precise-neural-exception boundary exercised by the prototype: NEX
invalidates the rejected dependency cone, whereas a whole-program retry
invalidates the complete executed window.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd


TOPOLOGIES = (
    "chain",
    "fork_join",
    "layered",
    "clustered",
    "cross_file",
    "workflow",
)


@dataclass(frozen=True)
class ConformanceResult:
    topology: str
    n_operations: int
    n_assumptions: int
    guard_latency: int
    failure_quantile: float
    seed: int
    rejected_assumption: int
    failure_operation: int
    detection_operation: int
    executed_operations: int
    oracle_cone: int
    runtime_cone: int
    exact_cone: bool
    false_positive_nodes: int
    false_negative_nodes: int
    nex_invalidated: int
    full_retry_invalidated: int
    nex_invalidation_amplification: float
    full_retry_invalidation_amplification: float
    nex_preserved_ratio: float
    retired_unrelated_operations: int


def _parents(topology: str, n: int, rng: np.random.Generator) -> list[set[int]]:
    """Create a topologically ordered effect graph with bounded fan-in."""

    parents: list[set[int]] = [set() for _ in range(n)]
    if topology == "chain":
        for node in range(1, n):
            parents[node].add(node - 1)
            if node >= 8 and node % 11 == 0:
                parents[node].add(node - 8)

    elif topology == "fork_join":
        branches = 4
        for node in range(branches, n):
            parents[node].add(node - branches)
            if node % 20 == 19:
                parents[node].update(range(node - branches + 1, node))

    elif topology == "layered":
        width = 8
        for node in range(width, n):
            start = max(0, (node // width - 1) * width)
            stop = min(node, start + width)
            candidates = np.arange(start, stop)
            fan_in = min(3, len(candidates))
            for parent in rng.choice(candidates, size=fan_in, replace=False):
                parents[node].add(int(parent))

    elif topology == "clustered":
        cluster_size = max(8, n // 8)
        for node in range(1, n):
            if node // cluster_size == (node - 1) // cluster_size:
                parents[node].add(node - 1)
            elif rng.random() < 0.35:
                parents[node].add(node - 1)
            if node >= 3 and rng.random() < 0.18:
                candidate = node - int(rng.integers(2, min(cluster_size, node) + 1))
                if candidate // cluster_size == node // cluster_size:
                    parents[node].add(candidate)

    elif topology == "cross_file":
        files = 8
        for node in range(files, n):
            parents[node].add(node - files)
            if node % 13 == 0:
                parents[node].add(max(0, node - files - 1))

    elif topology == "workflow":
        width = max(6, n // 10)
        for node in range(1, n):
            stage = node // width
            if node % width:
                parents[node].add(node - 1)
            if stage and node % width < 3:
                previous_start = (stage - 1) * width
                previous_stop = min(stage * width, n)
                candidates = np.arange(previous_start, previous_stop)
                fan_in = min(2, len(candidates))
                for parent in rng.choice(candidates, size=fan_in, replace=False):
                    parents[node].add(int(parent))
    else:
        raise ValueError(f"unknown topology: {topology}")

    return parents


def _direct_consumers(
    topology: str,
    n: int,
    n_assumptions: int,
    rng: np.random.Generator,
) -> list[set[int]]:
    """Choose repeated, topology-aware direct consumers for each prediction."""

    consumers: list[set[int]] = []
    upper = max(4, int(n * 0.62))
    for assumption in range(n_assumptions):
        base = int((assumption + 1) * upper / (n_assumptions + 1))
        jitter = int(rng.integers(-max(1, n // 64), max(2, n // 64 + 1)))
        first = min(max(1, base + jitter), upper - 1)
        uses = {first}

        if topology == "cross_file":
            step = 8
        elif topology == "fork_join":
            step = 4
        elif topology == "clustered":
            step = max(2, n // 32)
        else:
            step = max(2, n // 24)
        second = first + step
        if second < upper:
            uses.add(second)
        consumers.append(uses)
    return consumers


def _children(parents: list[set[int]]) -> list[set[int]]:
    children: list[set[int]] = [set() for _ in parents]
    for node, node_parents in enumerate(parents):
        for parent in node_parents:
            children[parent].add(node)
    return children


def _oracle_descendants(children: list[set[int]], sources: set[int]) -> set[int]:
    reached = set(sources)
    queue = deque(sources)
    while queue:
        node = queue.popleft()
        for child in children[node]:
            if child not in reached:
                reached.add(child)
                queue.append(child)
    return reached


def _runtime_provenance(
    parents: list[set[int]], direct: list[set[int]]
) -> list[frozenset[int]]:
    direct_at: list[set[int]] = [set() for _ in parents]
    for assumption, uses in enumerate(direct):
        for node in uses:
            direct_at[node].add(assumption)

    provenance: list[frozenset[int]] = []
    for node, node_parents in enumerate(parents):
        tags = set(direct_at[node])
        for parent in node_parents:
            tags.update(provenance[parent])
        provenance.append(frozenset(tags))
    return provenance


def simulate_conformance(
    *,
    topology: str,
    n_operations: int,
    n_assumptions: int,
    guard_latency: int,
    failure_quantile: float,
    seed: int,
) -> ConformanceResult:
    if topology not in TOPOLOGIES:
        raise ValueError(f"unsupported topology: {topology}")
    if n_operations < 16 or n_assumptions < 1 or guard_latency < 0:
        raise ValueError("invalid experiment dimensions")
    if not 0 < failure_quantile <= 1:
        raise ValueError("failure_quantile must be in (0, 1]")

    rng = np.random.default_rng(seed)
    parents = _parents(topology, n_operations, rng)
    children = _children(parents)
    direct = _direct_consumers(topology, n_operations, n_assumptions, rng)
    rejected = int(rng.integers(0, n_assumptions))

    oracle_all = sorted(_oracle_descendants(children, direct[rejected]))
    failure_index = min(len(oracle_all) - 1, int((len(oracle_all) - 1) * failure_quantile))
    failure_operation = oracle_all[failure_index]
    detection_operation = min(n_operations - 1, failure_operation + guard_latency)
    executed = set(range(detection_operation + 1))

    oracle = set(oracle_all) & executed
    provenance = _runtime_provenance(parents, direct)
    runtime = {node for node in executed if rejected in provenance[node]}
    false_positive = runtime - oracle
    false_negative = oracle - runtime
    if not oracle:
        raise AssertionError("constructed rejection has an empty executed cone")

    nex_invalidated = len(runtime)
    full_invalidated = len(executed)
    preserved = len(executed - runtime)
    return ConformanceResult(
        topology=topology,
        n_operations=n_operations,
        n_assumptions=n_assumptions,
        guard_latency=guard_latency,
        failure_quantile=failure_quantile,
        seed=seed,
        rejected_assumption=rejected,
        failure_operation=failure_operation,
        detection_operation=detection_operation,
        executed_operations=len(executed),
        oracle_cone=len(oracle),
        runtime_cone=len(runtime),
        exact_cone=not false_positive and not false_negative,
        false_positive_nodes=len(false_positive),
        false_negative_nodes=len(false_negative),
        nex_invalidated=nex_invalidated,
        full_retry_invalidated=full_invalidated,
        nex_invalidation_amplification=nex_invalidated / len(oracle),
        full_retry_invalidation_amplification=full_invalidated / len(oracle),
        nex_preserved_ratio=preserved / len(executed),
        retired_unrelated_operations=preserved,
    )


def generate_suite(seeds_per_cell: int = 20) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    cell = 0
    for topology in TOPOLOGIES:
        for n_operations in (64, 128, 256):
            for n_assumptions in (4, 8, 16):
                for guard_latency in (0, 2, 4, 8):
                    for failure_quantile in (0.25, 0.50, 0.75):
                        for replicate in range(seeds_per_cell):
                            seed = 20260831 + cell * 1009 + replicate
                            result = simulate_conformance(
                                topology=topology,
                                n_operations=n_operations,
                                n_assumptions=n_assumptions,
                                guard_latency=guard_latency,
                                failure_quantile=failure_quantile,
                                seed=seed,
                            )
                            rows.append(asdict(result))
                        cell += 1
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("artifacts/precise_exception_dags.csv"))
    parser.add_argument("--seeds-per-cell", type=int, default=20)
    args = parser.parse_args()
    frame = generate_suite(args.seeds_per_cell)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False)
    print(
        f"wrote {len(frame):,} DAG replays; exact cones="
        f"{int(frame.exact_cone.sum()):,}/{len(frame):,}; "
        f"median full-retry amplification={frame.full_retry_invalidation_amplification.median():.2f}x"
    )


if __name__ == "__main__":
    main()
