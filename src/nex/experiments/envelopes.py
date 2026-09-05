"""Controlled sweep for conservative dependency envelopes.

The precise-exception conformance suite already fixes a true dependency DAG,
an executed prefix, and a rejected semantic obligation.  This experiment
replays every case while progressively replacing exact operations with opaque
operations.  An opaque operation receives ``TOP_U`` provenance: it may depend
on every currently unresolved obligation.  Descendants inherit that tag in
the normal forward dataflow pass.

The construction is intentionally one-sided.  It can add dependency edges but
cannot remove a true edge, so increasing opacity may enlarge the invalidation
cone but must never permit early retirement.  At 100% opacity the envelope is
the complete executed window, i.e. the Full-Retry boundary.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .conformance import (
    _children,
    _direct_consumers,
    _oracle_descendants,
    _parents,
)


OPAQUE_FRACTIONS = (0.0, 0.10, 0.25, 0.50, 0.75, 1.0)


@dataclass(frozen=True)
class EnvelopeResult:
    topology: str
    n_operations: int
    n_assumptions: int
    guard_latency: int
    failure_quantile: float
    seed: int
    rejected_assumption: int
    executed_operations: int
    opaque_fraction: float
    opaque_operations: int
    true_cone: int
    envelope_cone: int
    false_positive_nodes: int
    false_negative_nodes: int
    safety_violation: bool
    invalidation_amplification: float
    full_retry_amplification: float
    preserved_ratio: float


def _runtime_envelope(
    parents: list[set[int]],
    direct: list[set[int]],
    n_assumptions: int,
    opaque: set[int],
) -> list[frozenset[int]]:
    """Propagate exact provenance, assigning TOP_U at opaque operations."""

    direct_at: list[set[int]] = [set() for _ in parents]
    for assumption, uses in enumerate(direct):
        for node in uses:
            direct_at[node].add(assumption)

    top_u = set(range(n_assumptions))
    provenance: list[frozenset[int]] = []
    for node, node_parents in enumerate(parents):
        tags = set(direct_at[node])
        for parent in node_parents:
            tags.update(provenance[parent])
        if node in opaque:
            tags.update(top_u)
        provenance.append(frozenset(tags))
    return provenance


def replay_case(row: object, opaque_fraction: float) -> EnvelopeResult:
    rng = np.random.default_rng(int(row.seed))
    parents = _parents(str(row.topology), int(row.n_operations), rng)
    children = _children(parents)
    direct = _direct_consumers(
        str(row.topology), int(row.n_operations), int(row.n_assumptions), rng
    )
    rejected = int(rng.integers(0, int(row.n_assumptions)))
    if rejected != int(row.rejected_assumption):
        raise AssertionError("failed to reconstruct the pinned rejected assumption")

    executed = set(range(int(row.detection_operation) + 1))
    oracle = _oracle_descendants(children, direct[rejected]) & executed
    if len(oracle) != int(row.oracle_cone):
        raise AssertionError("failed to reconstruct the pinned oracle cone")

    count = int(round(len(executed) * opaque_fraction))
    if count:
        # A disjoint RNG keeps the program/DAG reconstruction identical while
        # making the placement of opaque boundaries deterministic per level.
        opaque_rng = np.random.default_rng(
            int(row.seed) * 37 + int(round(opaque_fraction * 1000)) * 1_000_003
        )
        opaque = set(
            int(x)
            for x in opaque_rng.choice(
                np.fromiter(sorted(executed), dtype=int), size=count, replace=False
            )
        )
    else:
        opaque = set()

    provenance = _runtime_envelope(
        parents, direct, int(row.n_assumptions), opaque
    )
    envelope = {node for node in executed if rejected in provenance[node]}
    false_negative = oracle - envelope
    false_positive = envelope - oracle

    return EnvelopeResult(
        topology=str(row.topology),
        n_operations=int(row.n_operations),
        n_assumptions=int(row.n_assumptions),
        guard_latency=int(row.guard_latency),
        failure_quantile=float(row.failure_quantile),
        seed=int(row.seed),
        rejected_assumption=rejected,
        executed_operations=len(executed),
        opaque_fraction=float(opaque_fraction),
        opaque_operations=len(opaque),
        true_cone=len(oracle),
        envelope_cone=len(envelope),
        false_positive_nodes=len(false_positive),
        false_negative_nodes=len(false_negative),
        safety_violation=bool(false_negative),
        invalidation_amplification=len(envelope) / len(oracle),
        full_retry_amplification=len(executed) / len(oracle),
        preserved_ratio=len(executed - envelope) / len(executed),
    )


def build_sweep(source: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for pinned in source.itertuples(index=False):
        for fraction in OPAQUE_FRACTIONS:
            rows.append(asdict(replay_case(pinned, fraction)))
    frame = pd.DataFrame(rows)
    if frame.safety_violation.any() or frame.false_negative_nodes.sum() != 0:
        raise AssertionError("a conservative envelope omitted a true dependency")
    exact = frame[np.isclose(frame.opaque_fraction, 0.0)]
    opaque = frame[np.isclose(frame.opaque_fraction, 1.0)]
    if not np.allclose(exact.invalidation_amplification, 1.0):
        raise AssertionError("0% opacity must reproduce exact dependency recovery")
    if not np.allclose(
        opaque.invalidation_amplification, opaque.full_retry_amplification
    ):
        raise AssertionError("100% opacity must converge to Full Retry")
    return frame


def summarize(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.groupby("opaque_fraction", as_index=False)
        .agg(
            cases=("seed", "size"),
            amplification_median=("invalidation_amplification", "median"),
            amplification_q25=("invalidation_amplification", lambda x: x.quantile(0.25)),
            amplification_q75=("invalidation_amplification", lambda x: x.quantile(0.75)),
            full_retry_median=("full_retry_amplification", "median"),
            false_positive_nodes_mean=("false_positive_nodes", "mean"),
            false_negative_nodes=("false_negative_nodes", "sum"),
            safety_violations=("safety_violation", "sum"),
            preserved_ratio_median=("preserved_ratio", "median"),
        )
        .sort_values("opaque_fraction")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", type=Path, default=Path("artifacts/precise_exception_dags.csv")
    )
    parser.add_argument(
        "--out", type=Path, default=Path("artifacts/dependency_envelope_sweep.csv")
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("artifacts/e5_dependency_envelope_summary.csv"),
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        default=Path("artifacts/DEPENDENCY_ENVELOPE_PROVENANCE.json"),
    )
    args = parser.parse_args()

    source = pd.read_csv(args.source)
    frame = build_sweep(source)
    summary = summarize(frame)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False)
    summary.to_csv(args.summary, index=False)
    args.provenance.write_text(
        json.dumps(
            {
                "source": str(args.source),
                "source_rows": len(source),
                "opaque_fractions": list(OPAQUE_FRACTIONS),
                "output_rows": len(frame),
                "semantics": "opaque operation receives TOP_U; provenance otherwise propagates by union",
                "assertions": {
                    "zero_false_negative_dependencies": int(frame.false_negative_nodes.sum()),
                    "zero_safety_violations": int(frame.safety_violation.sum()),
                    "zero_percent_is_exact": True,
                    "one_hundred_percent_equals_full_retry": True,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {len(frame):,} conservative-envelope replays; "
        f"safety violations={int(frame.safety_violation.sum())}; "
        f"median amplification {summary.iloc[0].amplification_median:.2f}x"
        f" -> {summary.iloc[-1].amplification_median:.2f}x"
    )


if __name__ == "__main__":
    main()
