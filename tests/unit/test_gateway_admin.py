"""Gateway admin: config composition, profile recognition and apply/rollback."""

import json
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

from cfa import gateway_admin as ga

REPO_GATEWAY = Path(__file__).resolve().parents[2] / "gateway"
ENV = {
    "FAKE_LLM_API_KEY": "fake",
    "LITELLM_MASTER_KEY": "master",
    **{
        f"MODEL_{s}_{k}": f"{s.lower()}-{k.lower()}"
        for s in ("FAST", "REASONING")
        for k in ("LITELLM_MODEL", "API_BASE", "API_KEY", "API_VERSION")
    },
    "MODEL_FAST_API_BASE": "https://example.openai.azure.com/",
}


@pytest.fixture
def gateway_dir(tmp_path: Path) -> Path:
    shutil.copytree(REPO_GATEWAY / "profiles", tmp_path / "profiles")
    shutil.copy(REPO_GATEWAY / "catalog.yaml", tmp_path / "catalog.yaml")
    shutil.copy(tmp_path / "profiles" / "live.yaml", tmp_path / "active.yaml")
    (tmp_path / "active.profile").write_text("live\n")
    return tmp_path


class FakeGateway(ga.Gateway):
    """No child process: `restart` just reports the scripted outcome."""

    def __init__(self, directory: Path, outcomes: list[bool]) -> None:
        super().__init__(directory, ["true"], "http://unused", ENV)
        self.outcomes = outcomes
        self.restarts = 0

    def restart(self, timeout_s: float) -> bool:
        self.restarts += 1
        return self.outcomes.pop(0)


def _catalog() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((REPO_GATEWAY / "catalog.yaml").read_text())
    return data


def _profiles() -> dict[str, str]:
    return {p.stem: p.read_text() for p in (REPO_GATEWAY / "profiles").glob("*.yaml")}


def test_catalog_entries_match_profile_entries_exactly() -> None:
    """Every named profile's aliases must be recognisable as catalog models,
    except deliberately broken keys."""
    catalog = _catalog()
    for name, text in _profiles().items():
        selection = ga.current_selection(catalog, yaml.safe_load(text))
        broken = [a for a, m in selection.items() if m is None]
        assert broken == ([] if "broken" not in name else ["assistant-reasoning"]), name


@pytest.mark.parametrize(
    ("selection", "profile"),
    [
        ({"assistant-fast": "env-fast", "assistant-reasoning": "env-reasoning"}, "live"),
        ({"assistant-fast": "env-reasoning", "assistant-reasoning": "env-fast"}, "live-swapped"),
        ({"assistant-fast": "fake-fast", "assistant-reasoning": "fake-reasoning"}, "fake"),
        ({"assistant-fast": "fake-fast", "assistant-reasoning": "fake-fast"}, "fake-swapped"),
    ],
)
def test_selection_equal_to_a_profile_reuses_its_exact_bytes(selection: dict[str, str], profile: str) -> None:
    name, text = ga.compose(_catalog(), _profiles(), selection, ENV)
    assert (name, text) == (profile, _profiles()[profile])


def test_other_selection_is_custom_and_keeps_base_settings() -> None:
    selection = {"assistant-fast": "env-fast", "assistant-reasoning": "azure-gpt-4.1"}
    name, text = ga.compose(_catalog(), _profiles(), selection, ENV)
    config = yaml.safe_load(text)
    live = yaml.safe_load(_profiles()["live"])
    assert name == ga.CUSTOM
    assert text.startswith("# Composed by the gateway admin")
    assert [m["model_name"] for m in config["model_list"]] == ["assistant-fast", "assistant-reasoning", "eval-weak"]
    assert config["model_list"][1]["litellm_params"]["model"] == "azure/gpt-4.1"
    assert {k: v for k, v in config.items() if k != "model_list"} == {
        k: v for k, v in live.items() if k != "model_list"
    }


@pytest.mark.parametrize(
    ("selection", "error"),
    [
        ({"assistant-fast": "env-fast"}, "every alias"),
        ({"assistant-fast": "env-fast", "assistant-reasoning": "nope"}, "unknown model"),
        ({"assistant-fast": "env-fast", "assistant-reasoning": "env-alt"}, "MODEL_ALT_API_BASE"),
    ],
)
def test_invalid_selections_are_rejected(selection: dict[str, str], error: str) -> None:
    with pytest.raises(ga.SelectionError, match=error):
        ga.compose(_catalog(), _profiles(), selection, ENV)


def test_models_are_described_without_secrets() -> None:
    catalog = {m["id"]: m for m in _catalog()["models"]}
    fast = ga.describe_model(catalog["env-fast"], ENV)
    assert fast["resolves_to"] == "fast-litellm_model"
    assert fast["api_base_host"] == "example.openai.azure.com"
    assert fast["available"]
    assert "fast-api_key" not in json.dumps(fast)
    alt = ga.describe_model(catalog["env-alt"], ENV)
    assert not alt["available"]
    assert alt["missing"] == ["MODEL_ALT_API_BASE", "MODEL_ALT_API_KEY", "MODEL_ALT_LITELLM_MODEL"]


def test_profile_needing_unset_env_is_rejected() -> None:
    with pytest.raises(ga.SelectionError, match="MODEL_ALT"):
        ga.profile_config(_profiles(), "live-alt", ENV)
    with pytest.raises(ga.SelectionError, match="unknown profile"):
        ga.profile_config(_profiles(), "../etc/passwd", ENV)


def test_apply_writes_config_and_restarts(gateway_dir: Path) -> None:
    gateway = FakeGateway(gateway_dir, [True])
    view = gateway.apply({"selection": {"assistant-fast": "env-reasoning", "assistant-reasoning": "env-fast"}})
    assert view["profile"] == "live-swapped"
    assert (gateway_dir / "active.yaml").read_text() == (gateway_dir / "profiles/live-swapped.yaml").read_text()
    assert (gateway_dir / "active.profile").read_text() == "live-swapped\n"
    assert view["selection"] == {"assistant-fast": "env-reasoning", "assistant-reasoning": "env-fast"}
    assert gateway.restarts == 1


def test_apply_same_config_is_a_no_op(gateway_dir: Path) -> None:
    gateway = FakeGateway(gateway_dir, [])
    assert gateway.apply({"profile": "live"})["profile"] == "live"
    assert gateway.restarts == 0


def test_failed_start_rolls_back(gateway_dir: Path) -> None:
    before = (gateway_dir / "active.yaml").read_text()
    gateway = FakeGateway(gateway_dir, [False, True])
    with pytest.raises(RuntimeError, match="rolled back to 'live'"):
        gateway.apply({"profile": "fake"})
    assert (gateway_dir / "active.yaml").read_text() == before
    assert (gateway_dir / "active.profile").read_text() == "live\n"
    assert gateway.restarts == 2


def test_http_api_requires_master_key(gateway_dir: Path) -> None:
    gateway = FakeGateway(gateway_dir, [True])
    server = ThreadingHTTPServer(("127.0.0.1", 0), ga.make_handler(gateway, "secret"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(f"{base}/catalog", timeout=5)  # noqa: S310
        assert denied.value.code == 401
        get = urllib.request.Request(f"{base}/catalog", headers={"authorization": "Bearer secret"})  # noqa: S310
        with urllib.request.urlopen(get, timeout=5) as response:  # noqa: S310
            assert json.loads(response.read())["profile"] == "live"
        post = urllib.request.Request(  # noqa: S310
            f"{base}/select",
            data=json.dumps({"profile": "fake"}).encode(),
            headers={"authorization": "Bearer secret", "content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(post, timeout=5) as response:  # noqa: S310
            assert json.loads(response.read())["profile"] == "fake"
    finally:
        server.shutdown()
