"""Offline, deterministic model stand-in with real private JSON-file writes."""

import json
from pathlib import Path
from typing import Mapping

from .providers import RepairRequest
from .runtime import Site, Verdict


WORKFLOW = '''def migrate(sites):
    observations = observe(sites[:2])
    rule = semantic(observations, "Select the source-unit migration rule for these observations")
    for site in sites:
        apply_change(site, rule)
    publish_report(sites)
    return final_validate()
'''

DECISIONS = {
    "milliseconds": "The source unit is ms. Divide timeout by 1000 to produce timeout_s; preserve retries.",
    "seconds": "The source unit is s. Copy timeout unchanged to timeout_s; preserve retries.",
}


class JsonMigrationAdapter:
    """Each site owns a new JSON file; no user file is overwritten.

    The local certificate proves the exact registered object (including the
    unchanged retries field). It does NOT establish arbitrary API equivalence.
    """

    task_id = "timeout-migration"

    def __init__(self, workspace: Path, *, n_sites: int = 16, failure_site: int | None = 7,
                 local_certificates: bool = True) -> None:
        if type(n_sites) is not int or not 1 <= n_sites <= 10000:
            raise ValueError("n_sites must be an integer from 1 to 10000")
        if failure_site is not None and (type(failure_site) is not int or not 1 <= failure_site <= n_sites):
            raise ValueError("failure_site must be within the workspace or None")
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=False)
        self.sites = tuple(Site(f"site-{i:04d}") for i in range(1, n_sites + 1))
        self.local_certificates = local_certificates
        for i, site in enumerate(self.sites, 1):
            value = {"timeout": 30 if i == failure_site else 30000,
                     "unit": "s" if i == failure_site else "ms", "retries": 3}
            self._path(site).write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    def _path(self, site: Site) -> Path:
        if not any(site is known for known in self.sites):
            raise ValueError("foreign site handle")
        return self.workspace / f"{site.site_id}.json"

    def observe(self, site: Site) -> str:
        return self.snapshot(site)

    def snapshot(self, site: Site) -> str:
        return self._path(site).read_text(encoding="utf-8")

    def restore(self, site: Site, snapshot: str) -> None:
        self._path(site).write_text(snapshot, encoding="utf-8")

    def apply(self, site: Site, decision: str) -> None:
        if decision not in {"milliseconds", "seconds"}:
            raise ValueError("unsupported migration decision")
        old = json.loads(self.snapshot(site))
        value = {"timeout_s": old["timeout"] / (1000 if decision == "milliseconds" else 1),
                 "retries": old["retries"]}
        self.restore(site, json.dumps(value, sort_keys=True))

    def _correct(self, site: Site) -> bool:
        try:
            return json.loads(self.snapshot(site)) == {"timeout_s": 30, "retries": 3}
        except (ValueError, OSError):
            return False

    def validate(self, site: Site) -> Verdict:
        if not self.local_certificates:
            return Verdict.UNKNOWN
        return Verdict.ACCEPT if self._correct(site) else Verdict.REJECT

    def final_validate(self) -> bool:
        return all(self._correct(site) for site in self.sites)


class DemoProvider:
    """A deterministic replay provider; this is explicitly not a measured LLM."""

    def predict(self, observations: tuple[str, ...], question: str) -> str:
        return "milliseconds"

    def repair(self, request: RepairRequest) -> Mapping[str, str]:
        return {key: "milliseconds" if json.loads(obs)["unit"] == "ms" else "seconds"
                for key, obs in zip(request.site_ids, request.observations)}
