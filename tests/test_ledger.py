from concurrent.futures import ThreadPoolExecutor

import pytest

from nex import PublicationConflict, PublicationLedger


def test_idempotence_survives_reopen(tmp_path):
    path = tmp_path / "ledger.sqlite"
    assert PublicationLedger(path).commit("stable-id", {"result": "accepted"})
    assert not PublicationLedger(path).commit("stable-id", {"result": "accepted"})
    assert len(PublicationLedger(path).records()) == 1
    with pytest.raises(PublicationConflict):
        PublicationLedger(path).commit("stable-id", {"result": "different"})
    assert len(PublicationLedger(path).records()) == 1


def test_concurrent_identical_publication_is_committed_once(tmp_path):
    ledger = PublicationLedger(tmp_path / "ledger.sqlite")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ledger.commit("same", {"v": 1}), range(24)))
    assert sum(results) == 1
    assert len(ledger.records()) == 1


@pytest.mark.parametrize("key", ["", None, 42, "a" * 513])
def test_bad_logical_ids(tmp_path, key):
    ledger = PublicationLedger(tmp_path / "ledger.sqlite")
    with pytest.raises(ValueError):
        ledger.commit(key, {})


def test_payload_must_be_valid_json(tmp_path):
    ledger = PublicationLedger(tmp_path / "ledger.sqlite")
    with pytest.raises(ValueError):
        ledger.commit("id", {"value": float("nan")})
    assert not ledger.records()
