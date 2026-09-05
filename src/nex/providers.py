"""Model-neutral callbacks. Importing NEX never starts network or GPU work."""

from dataclasses import dataclass
import json
from typing import Callable, Mapping, Protocol

from .backends import BackendError, ChatBackend


@dataclass(frozen=True)
class RepairRequest:
    """A bounded request, not authority to mutate state or publish effects."""

    site_ids: tuple[str, ...]
    observations: tuple[str, ...]
    reason: str


class PredictionProvider(Protocol):
    def predict(self, observations: tuple[str, ...], question: str) -> str: ...

    def repair(self, request: RepairRequest) -> Mapping[str, str]: ...


@dataclass(frozen=True)
class CallbackProvider:
    """Wrap an existing model client; callbacks return structured decisions."""

    predict_fn: Callable[[tuple[str, ...], str], str]
    repair_fn: Callable[[RepairRequest], Mapping[str, str]]

    def predict(self, observations: tuple[str, ...], question: str) -> str:
        return self.predict_fn(observations, question)

    def repair(self, request: RepairRequest) -> Mapping[str, str]:
        return self.repair_fn(request)


class StructuredProvider:
    """One decision protocol for local and remote models; no effect authority."""

    def __init__(self, backend: ChatBackend, decisions: Mapping[str, str]):
        if (not decisions or any(not isinstance(k, str) or not k or not isinstance(v, str)
                                 for k, v in decisions.items())):
            raise ValueError("decisions must map nonempty labels to their tool semantics")
        self.backend, self.decisions = backend, dict(decisions)

    @staticmethod
    def _object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def _query(self, request: str) -> dict:
        system = ("Return only JSON. Select a decision label from this tool contract:\n" +
                  "\n".join(f"- {label}: {description}" for label, description in self.decisions.items()))
        text = self.backend.complete([{"role": "system", "content": system},
                                      {"role": "user", "content": request}])
        try:
            if not isinstance(text, str) or len(text) > 262144:
                raise ValueError("invalid size")
            result = json.loads(text, object_pairs_hook=self._object)
            if not isinstance(result, dict):
                raise ValueError("expected object")
            return result
        except (ValueError, TypeError, RecursionError):
            raise BackendError("model output must be a single JSON object without duplicate keys") from None

    def _check(self, value):
        if not isinstance(value, str) or value not in self.decisions:
            raise BackendError("model returned a decision outside the tool contract")
        return value

    def predict(self, observations: tuple[str, ...], question: str) -> str:
        result = self._query("Task: " + question + "\nInput observations:\n" + "\n".join(observations) +
                             '\nChoose one shared decision for these inputs. Return {"decision": "LABEL"}.')
        if set(result) != {"decision"}:
            raise BackendError("prediction must contain exactly the decision field")
        return self._check(result["decision"])

    def repair(self, request: RepairRequest) -> Mapping[str, str]:
        if (not request.site_ids or len(set(request.site_ids)) != len(request.site_ids)
                or len(request.site_ids) != len(request.observations)):
            raise ValueError("repair requires unique site IDs and one observation per site")
        # IDs remain host-owned: the model supplies an ordered vector of values,
        # then exact cardinality checking binds them to the authorized handles.
        observations = "\n".join(f"{i}. {obs}" for i, obs in enumerate(request.observations, 1))
        form = json.dumps({"decisions": ["LABEL"] * len(request.site_ids)})
        result = self._query("Choose the decision independently for each input below, in the same order.\n" +
                             observations + "\nReturn " + form + ".")
        values = result.get("decisions")
        if set(result) != {"decisions"} or not isinstance(values, list) or len(values) != len(request.site_ids):
            raise BackendError("repair must return exactly one ordered decision per authorized site")
        return {key: self._check(value) for key, value in zip(request.site_ids, values)}
