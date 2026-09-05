import pytest

from nex import PublicationLedger, RecoveryPolicy, Runtime, Verdict, WorkflowCompiler, WorkflowExecutionError
from nex.demo import DemoProvider, JsonMigrationAdapter, WORKFLOW


def session(tmp_path, *, n=16, failure=7, delay=4, policy="nex", local=True, provider=None, adapter_cls=JsonMigrationAdapter):
    adapter = adapter_cls(tmp_path / "workspace", n_sites=n, failure_site=failure, local_certificates=local)
    ledger = PublicationLedger(tmp_path / "publications.sqlite")
    runtime = Runtime(WorkflowCompiler().compile(WORKFLOW), adapter, provider or DemoProvider(), ledger,
                      policy=policy, guard_delay=delay)
    return runtime, adapter, ledger


@pytest.mark.parametrize("policy", list(RecoveryPolicy))
@pytest.mark.parametrize("n", [4, 8, 16])
@pytest.mark.parametrize("delay", [0, 2, 8])
@pytest.mark.parametrize("position", ["head", "middle", "tail"])
def test_count_oracle_and_publication_order(tmp_path, policy, n, delay, position):
    failure = {"head": 1, "middle": n // 2, "tail": n}[position]
    runtime, adapter, ledger = session(tmp_path, n=n, failure=failure, delay=delay, policy=policy)
    result = runtime.execute()
    assert result.success, result.error
    expected = min(n, failure + delay) if policy == RecoveryPolicy.FULL_RETRY else min(delay + 1, n - failure + 1)
    assert result.metrics.rolled_back_sites == expected
    assert result.metrics.tool_calls == n + expected
    assert result.metrics.model_calls == 2
    assert len(ledger.records()) == 1
    assert adapter.final_validate()
    events = [event["event"] for event in result.events]
    assert events.index("publication_held") < events.index("global_certificate") < events.index("publication_committed")
    with pytest.raises(WorkflowExecutionError):
        runtime.execute()


@pytest.mark.parametrize("policy", list(RecoveryPolicy))
def test_global_only_converges_to_full_retry(tmp_path, policy):
    runtime, adapter, ledger = session(tmp_path, local=False, policy=policy)
    result = runtime.execute()
    assert result.success, result.error
    assert result.metrics.rolled_back_sites == 16
    assert result.metrics.preserved_at_first_failure == 0
    assert result.metrics.final_validations == 2
    assert len(ledger.records()) == 1


def test_fault_free_path_has_no_recovery(tmp_path):
    runtime, _, ledger = session(tmp_path, failure=None)
    result = runtime.execute()
    assert result.success
    assert result.metrics.model_calls == 1
    assert result.metrics.tool_calls == 16
    assert result.metrics.exceptions == 0
    assert len(ledger.records()) == 1


class OverScopedProvider(DemoProvider):
    def repair(self, request):
        return {**super().repair(request), "unauthorized-site": "seconds"}


class WrongProvider(DemoProvider):
    def repair(self, request):
        return {key: "milliseconds" for key in request.site_ids}


class EmptyPrediction(DemoProvider):
    def predict(self, observations, question):
        return ""


@pytest.mark.parametrize("provider", [OverScopedProvider(), WrongProvider(), EmptyPrediction()])
def test_bad_neural_outputs_never_publish(tmp_path, provider):
    runtime, _, ledger = session(tmp_path, provider=provider)
    result = runtime.execute()
    assert not result.success and result.error
    assert ledger.records() == []


class GlobalRejectAdapter(JsonMigrationAdapter):
    def final_validate(self):
        return False


def test_global_rejection_does_not_publish_local_success(tmp_path):
    runtime, _, ledger = session(tmp_path, failure=None, adapter_cls=GlobalRejectAdapter)
    result = runtime.execute()
    assert not result.success
    assert "contradicts" in result.error
    assert ledger.records() == []


class WeakBoolAdapter(JsonMigrationAdapter):
    def validate(self, site):
        return True


def test_truthy_filter_is_not_a_certificate(tmp_path):
    runtime, _, ledger = session(tmp_path, adapter_cls=WeakBoolAdapter)
    result = runtime.execute()
    assert not result.success
    assert "Verdict" in result.error
    assert not ledger.records()


class MixedCertificates(JsonMigrationAdapter):
    def validate(self, site):
        if site.site_id in {"site-0002", "site-0007"}:
            return Verdict.UNKNOWN
        return super().validate(site)


def test_unknown_obligations_remain_in_global_recovery_scope(tmp_path):
    runtime, _, ledger = session(tmp_path, adapter_cls=MixedCertificates)
    result = runtime.execute()
    assert result.success, result.error
    exception = next(e for e in result.events if e["event"] == "exception")
    assert set(exception["invalidated"]) == {"site-0002", "site-0007"}
    assert len(exception["preserved"]) == 14
    assert len(ledger.records()) == 1


def test_demo_never_overwrites_existing_workspace(tmp_path):
    path = tmp_path / "workspace"
    path.mkdir()
    sentinel = path / "precious.txt"
    sentinel.write_text("user content")
    with pytest.raises(FileExistsError):
        JsonMigrationAdapter(path)
    assert sentinel.read_text() == "user content"


@pytest.mark.parametrize("value", [-1, 1.5, True, 10001])
def test_bad_guard_delay_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        session(tmp_path, delay=value)
