"""Evidence-gated execution for independent, versioned migration sites.

The host owns the live Python continuation, snapshots, certificates and sink.
A rejected value invalidates a conservative unresolved window, not a general
minimal dependency DAG. All adapter and provider callbacks are trusted code.
"""

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import time
from typing import Any, Mapping, Protocol

from .compiler import CompiledWorkflow
from .ledger import PublicationLedger
from .providers import PredictionProvider, RepairRequest


class Verdict(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    UNKNOWN = "unknown"


class RecoveryPolicy(str, Enum):
    NEX = "nex"
    FULL_RETRY = "full-retry"


class WorkflowExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Site:
    site_id: str


class MigrationAdapter(Protocol):
    """Trusted adapter: sites have disjoint state and version-stable contracts.

    Mutations stay in a private workspace. snapshot/restore cover every byte
    changed by apply. ACCEPT certifies the whole local contract, not just parse.
    final_validate certifies the entire task, including cross-site constraints.
    """

    task_id: str
    sites: tuple[Site, ...]

    def observe(self, site: Site) -> str: ...
    def snapshot(self, site: Site) -> str: ...
    def apply(self, site: Site, decision: str) -> None: ...
    def restore(self, site: Site, snapshot: str) -> None: ...
    def validate(self, site: Site) -> Verdict: ...
    def final_validate(self) -> bool: ...


@dataclass
class Metrics:
    model_calls: int = 0
    tool_calls: int = 0
    validator_calls: int = 0
    final_validations: int = 0
    exceptions: int = 0
    rolled_back_sites: int = 0
    replayed_sites: int = 0
    preserved_at_first_failure: int = 0
    publications: int = 0
    deduplicated_publications: int = 0


@dataclass(frozen=True)
class RunResult:
    success: bool
    policy: str
    program_sha256: str
    error: str | None
    elapsed_s: float
    metrics: Metrics
    events: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Runtime:
    """Single-use, synchronous execution session; no process-crash resumption."""

    def __init__(self, workflow: CompiledWorkflow, adapter: MigrationAdapter,
                 provider: PredictionProvider, ledger: PublicationLedger, *,
                 policy: RecoveryPolicy | str = RecoveryPolicy.NEX,
                 guard_delay: int = 4, max_repairs: int = 128,
                 publication_id: str | None = None) -> None:
        if type(guard_delay) is not int or not 0 <= guard_delay <= 10000:
            raise ValueError("guard_delay must be an integer from 0 to 10000")
        if type(max_repairs) is not int or not 1 <= max_repairs <= 10000:
            raise ValueError("max_repairs must be an integer from 1 to 10000")
        sites = tuple(adapter.sites)
        if not 1 <= len(sites) <= 10000 or any(type(s) is not Site for s in sites):
            raise ValueError("adapter must expose 1–10000 Site handles")
        if any(not isinstance(s.site_id, str) or not s.site_id or len(s.site_id) > 256 for s in sites):
            raise ValueError("site IDs must contain 1–256 characters")
        if len({s.site_id for s in sites}) != len(sites):
            raise ValueError("duplicate site IDs")
        self.workflow, self.adapter, self.provider, self.ledger = workflow, adapter, provider, ledger
        self.policy = RecoveryPolicy(policy)
        self.guard_delay, self.max_repairs = guard_delay, max_repairs
        self.publication_id = publication_id or f"{adapter.task_id}:report"
        self.sites = sites
        self.by_id = {s.site_id: s for s in sites}
        self.states = {s.site_id: "unseen" for s in sites}
        self.versions = {s.site_id: 0 for s in sites}
        self.snapshots: dict[str, str] = {}
        self.applied: list[str] = []
        self.queue: list[str] = []
        self.metrics = Metrics()
        self.events: list[dict[str, Any]] = []
        self.decision = ""
        self.completed_region = False
        self.publish_requested = False
        self._used = False
        self._running = False
        self._prediction_done = False

    def _emit(self, event: str, **fields: Any) -> None:
        self.events.append({"seq": len(self.events), "event": event, **fields})

    def _site(self, site: Site) -> str:
        if not self._running or type(site) is not Site or self.by_id.get(site.site_id) is not site:
            raise WorkflowExecutionError("foreign site handle or inactive session")
        return site.site_id

    @staticmethod
    def _decision(value: Any) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= 4096:
            raise WorkflowExecutionError("provider must return a nonempty decision string of at most 4096 characters")
        return value

    def _observe(self, sites: tuple[Site, ...]) -> tuple[str, ...]:
        return tuple(self.adapter.observe(self.by_id[self._site(site)]) for site in sites)

    def _semantic(self, observations: tuple[str, ...], question: str) -> str:
        if self._prediction_done:
            raise WorkflowExecutionError("prediction already consumed")
        self.metrics.model_calls += 1
        self.decision = self._decision(self.provider.predict(observations, question))
        self._prediction_done = True
        self._emit("neural_prediction", consumers=len(self.sites))
        return self.decision

    def _stage(self, key: str, decision: str, *, replay: bool = False) -> None:
        site = self.by_id[key]
        if key not in self.snapshots:
            self.snapshots[key] = self.adapter.snapshot(site)
        self.adapter.apply(site, decision)
        self.states[key] = "staged"
        self.versions[key] += 1
        self.metrics.tool_calls += 1
        self.metrics.replayed_sites += int(replay)
        if key not in self.applied:
            self.applied.append(key)
        self._emit("replay" if replay else "stage", site=key, version=self.versions[key])

    def _apply(self, site: Site, decision: str) -> None:
        key = self._site(site)
        if self.completed_region:
            return  # the bounded region repair already materialized its suffix
        if self.states[key] != "unseen":
            raise WorkflowExecutionError("workflow attempted to rewrite an already-consumed site")
        self._stage(key, decision)
        self.queue.append(key)
        while len(self.queue) > self.guard_delay:
            self._guard_one()

    def _verdict(self, key: str) -> Verdict:
        self.metrics.validator_calls += 1
        verdict = self.adapter.validate(self.by_id[key])
        if type(verdict) is not Verdict:
            raise WorkflowExecutionError("validator must return Verdict, not bool or a truthy object")
        self._emit("certificate", site=key, verdict=verdict.value, version=self.versions[key])
        return verdict

    def _accept(self, key: str) -> None:
        self.states[key] = "retired" if self.policy == RecoveryPolicy.NEX else "validated"
        self._emit("retire" if self.policy == RecoveryPolicy.NEX else "validated_region_pending", site=key)

    def _guard_one(self) -> None:
        key = self.queue.pop(0)
        verdict = self._verdict(key)
        if verdict == Verdict.ACCEPT:
            self._accept(key)
        elif verdict == Verdict.REJECT:
            self._recover(key)
        # UNKNOWN stays staged even after its queued check has completed.

    def _repair(self, keys: list[str], reason: str) -> dict[str, str]:
        request = RepairRequest(tuple(keys), tuple(self.adapter.observe(self.by_id[k]) for k in keys), reason)
        self.metrics.model_calls += 1
        mapping = self.provider.repair(request)
        if not isinstance(mapping, Mapping) or set(mapping) != set(keys):
            raise WorkflowExecutionError("repair keys must exactly match the runtime-authorized sites")
        return {key: self._decision(mapping[key]) for key in keys}

    def _recover(self, failed: str | None) -> None:
        if self.metrics.exceptions >= self.max_repairs:
            raise WorkflowExecutionError("repair budget exhausted")
        if not self.metrics.exceptions:
            self.metrics.preserved_at_first_failure = sum(state == "retired" for state in self.states.values())
        self.metrics.exceptions += 1
        full = self.policy == RecoveryPolicy.FULL_RETRY
        invalidated = list(self.applied) if full else [k for k in self.applied if self.states[k] != "retired"]
        self._emit("exception", failed_site=failed, invalidated=invalidated,
                   preserved=[k for k in self.applied if k not in invalidated])
        self.metrics.rolled_back_sites += len(invalidated)
        for key in reversed(invalidated):
            self.adapter.restore(self.by_id[key], self.snapshots[key])
            self.states[key] = "invalidated"
            self.versions[key] += 1
        self.queue.clear()
        keys = list(self.by_id) if full else (invalidated if failed is None else [failed])
        mapping = self._repair(keys, "global rejection without local witness" if failed is None else f"rejected: {failed}")
        replay = list(self.by_id) if full else invalidated
        for key in replay:
            self._stage(key, mapping.get(key, self.decision), replay=True)
            if key in mapping:
                verdict = self._verdict(key)
                if verdict == Verdict.REJECT:
                    raise WorkflowExecutionError(f"repair rejected at {key}")
                if verdict == Verdict.ACCEPT:
                    self._accept(key)
            else:
                self.queue.append(key)
        self.completed_region = full
        self._emit("resume", replayed=len(replay), policy=self.policy.value)

    def _request_publish(self, sites: tuple[Site, ...]) -> None:
        if sites != self.sites or self.publish_requested:
            raise WorkflowExecutionError("invalid or repeated publication request")
        self.publish_requested = True
        self._emit("publication_held", unresolved=sum(s != "retired" for s in self.states.values()))

    def _global_check(self) -> bool:
        self.metrics.final_validations += 1
        valid = self.adapter.final_validate()
        if type(valid) is not bool:
            raise WorkflowExecutionError("final validator must return bool")
        self._emit("global_certificate", accepted=valid)
        return valid

    def _final_validate(self) -> bool:
        while self.queue:
            self._guard_one()
        if not self._global_check():
            unresolved = [k for k in self.applied if self.states[k] != "retired"]
            if not unresolved:
                raise WorkflowExecutionError("global rejection contradicts locally retired state; no publication")
            self._recover(None)
            while self.queue:
                self._guard_one()
            if not self._global_check():
                raise WorkflowExecutionError("global validation rejected the repaired task")
        for key in self.states:
            self.states[key] = "retired"
        if not self.publish_requested:
            raise WorkflowExecutionError("missing publication request")
        state = [(s.site_id, self.adapter.snapshot(s)) for s in self.sites]
        digest = hashlib.sha256(json.dumps(state, ensure_ascii=True).encode()).hexdigest()
        committed = self.ledger.commit(self.publication_id, {
            "task_id": self.adapter.task_id, "program_sha256": self.workflow.sha256,
            "sites": len(self.sites), "state_sha256": digest,
        })
        self.metrics.publications += int(committed)
        self.metrics.deduplicated_publications += int(not committed)
        self._emit("publication_committed" if committed else "publication_deduplicated",
                   logical_id=self.publication_id)
        return True

    def execute(self) -> RunResult:
        if self._used:
            raise WorkflowExecutionError("Runtime sessions are single-use")
        self._used, self._running = True, True
        start = time.perf_counter()
        error = None
        success = False
        self._emit("assumption_inferred", value=self.workflow.assumption.value,
                   fanout=self.workflow.assumption.symbolic_fanout)
        try:
            namespace = {"__builtins__": {}, "observe": self._observe, "semantic": self._semantic,
                         "apply_change": self._apply, "publish_report": self._request_publish,
                         "final_validate": self._final_validate}
            exec(self.workflow.code, namespace)
            success = namespace["migrate"](self.sites) is True
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._emit("execution_failed", error=error)
        finally:
            self._running = False
        return RunResult(success, self.policy.value, self.workflow.sha256, error,
                         time.perf_counter() - start, self.metrics, tuple(self.events))
