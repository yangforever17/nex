"""Model-neutral callbacks. Importing NEX never starts network or GPU work."""

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol


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
