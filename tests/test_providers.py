import pytest

from nex import BackendError, PublicationLedger, Runtime, StructuredProvider, WorkflowCompiler
from nex.backends import ChatBackend, Completion
from nex.demo import DECISIONS, JsonMigrationAdapter, WORKFLOW
from nex.providers import CallbackProvider, RepairRequest


class ScriptedBackend(ChatBackend):
    def __init__(self, outputs):
        super().__init__("test-model")
        self.outputs = iter(outputs)
        self.messages = []

    def _generate(self, messages):
        self.messages.append(messages)
        return Completion(next(self.outputs), 10, 5)


def test_one_protocol_for_prediction_and_bounded_repair(tmp_path):
    backend = ScriptedBackend(['{"decision":"milliseconds"}', '{"decisions":["seconds"]}'])
    adapter = JsonMigrationAdapter(tmp_path / "workspace")
    ledger = PublicationLedger(tmp_path / "ledger.sqlite")
    result = Runtime(WorkflowCompiler().compile(WORKFLOW), adapter,
                     StructuredProvider(backend, DECISIONS), ledger).execute()
    assert result.success
    assert result.metrics.rolled_back_sites == 5
    assert len(ledger.records()) == 1
    repair = backend.messages[1][1]["content"]
    assert '"unit": "s"' in repair
    assert "site-0007" not in repair  # IDs are never model-authored.
    assert len(backend.describe()["calls"]) == 2


@pytest.mark.parametrize("output", ["", "not json", "[]", "null", '{"decision":"bad"}',
    '{"decision":"milliseconds","extra":1}', '{"decision":null}',
    '{"decision":"seconds","decision":"milliseconds"}',
    '```json\n{"decision":"milliseconds"}\n```', "x" * 262145])
def test_invalid_predictions_fail_closed_without_echoing_output(tmp_path, output):
    ledger = PublicationLedger(tmp_path / "ledger.sqlite")
    result = Runtime(WorkflowCompiler().compile(WORKFLOW), JsonMigrationAdapter(tmp_path / "workspace"),
                     StructuredProvider(ScriptedBackend([output]), DECISIONS), ledger).execute()
    assert not result.success
    assert "BackendError" in result.error
    assert ledger.records() == []


@pytest.mark.parametrize("output", ['{}', '{"decisions":[]}', '{"decisions":{}}',
    '{"decisions":["seconds","milliseconds"]}', '{"decisions":["invalid"]}',
    '{"decisions":{"a":"seconds","b":"seconds"}}', '{"decisions":{"a":"invalid"}}',
    '{"decisions":{"a":"seconds"},"extra":0}',
    '{"decisions":{"a":"seconds","a":"milliseconds"}}'])
def test_repair_cannot_widen_or_omit_runtime_scope(output):
    provider = StructuredProvider(ScriptedBackend([output]), DECISIONS)
    with pytest.raises(BackendError):
        provider.repair(RepairRequest(("a",), ('{"unit":"s"}',), "reject"))


def test_host_binds_ordered_values_to_exact_authorized_ids():
    provider = StructuredProvider(ScriptedBackend(['{"decisions":["milliseconds","seconds"]}']), DECISIONS)
    request = RepairRequest(("opaque-id-a", "opaque-id-b"), ('{"unit":"ms"}', '{"unit":"s"}'), "rejection")
    assert provider.repair(request) == {"opaque-id-a": "milliseconds", "opaque-id-b": "seconds"}


@pytest.mark.parametrize("ids,observations", [((), ()), (("a",), ()), (("a", "a"), ("x", "y"))])
def test_malformed_repair_requests_do_not_call_model(ids, observations):
    backend = ScriptedBackend([])
    with pytest.raises(ValueError):
        StructuredProvider(backend, DECISIONS).repair(RepairRequest(ids, observations, "reject"))
    assert not backend.records


def test_callback_provider_stays_model_neutral():
    provider = CallbackProvider(lambda obs, question: obs[0], lambda request: {request.site_ids[0]: "fixed"})
    assert provider.predict(("original",), "question") == "original"
    assert provider.repair(RepairRequest(("a",), ("original",), "reason")) == {"a": "fixed"}


@pytest.mark.parametrize("contract", [{}, {"": "description"}, {"ok": 3}])
def test_invalid_decision_contract(contract):
    with pytest.raises(ValueError):
        StructuredProvider(ScriptedBackend([]), contract)
