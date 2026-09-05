import pytest

pytest.importorskip("numpy")
pytest.importorskip("pandas")

from nex.experiments.conformance import TOPOLOGIES, simulate_conformance
from nex.experiments.envelopes import replay_case


@pytest.mark.parametrize("topology", TOPOLOGIES)
def test_exact_and_opaque_boundaries(topology):
    case = simulate_conformance(topology=topology, n_operations=64, n_assumptions=8,
                                guard_latency=4, failure_quantile=.5, seed=700)
    assert case.exact_cone and not case.false_negative_nodes
    exact = replay_case(case, 0)
    opaque = replay_case(case, 1)
    assert exact.invalidation_amplification == 1
    assert opaque.envelope_cone == opaque.executed_operations
    assert not opaque.false_negative_nodes
