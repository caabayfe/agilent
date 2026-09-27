"""Gateway admin: supervises LiteLLM and lets an operator re-point its aliases.

Runs as the entrypoint of the LiteLLM container (stdlib + PyYAML only; it imports
nothing from ``cfa``) and:

* spawns ``litellm`` as a child process on the active config;
* serves a small internal HTTP API (compose network only, master-key auth):
  ``GET /catalog`` lists the models in ``gateway/catalog.yaml`` per alias, whether
  each one is usable (its ``os.environ/`` references are set) and what is selected;
  ``POST /select`` activates a named profile, or composes a config from a per-alias
  selection, writes ``active.yaml`` atomically and restarts the child, rolling back
  if the gateway does not come back healthy.

A composed config that is identical to a named profile is written as that profile's
exact bytes, so the eval certificate's ``gateway_config_hash`` matches ``make gateway``.
Anything else is profile ``custom``: the certificate goes STALE until re-evaluated.
"""

import hmac
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import urlparse

import yaml

log = logging.getLogger("gateway-admin")
ENV_REF = "os.environ/"
CUSTOM = "custom"


class SelectionError(ValueError):
    """The requested profile or selection cannot be activated."""


# --------------------------------------------------------------------------- pure logic


def env_refs(value: Any) -> list[str]:
    """Environment variables a config fragment references via ``os.environ/NAME``."""
    if isinstance(value, str):
        return [value.removeprefix(ENV_REF)] if value.startswith(ENV_REF) else []
    if isinstance(value, Mapping):
        return [ref for v in value.values() for ref in env_refs(v)]
    if isinstance(value, list):
        return [ref for v in value for ref in env_refs(v)]
    return []


def missing_env(value: Any, env: Mapping[str, str]) -> list[str]:
    return sorted({ref for ref in env_refs(value) if not env.get(ref)})


def resolve(value: str, env: Mapping[str, str]) -> str:
    return env.get(value.removeprefix(ENV_REF), "") if value.startswith(ENV_REF) else value


def describe_model(model: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    """Catalog entry as shown to the UI: resolved model name and host, never keys."""
    params: Mapping[str, Any] = model["litellm_params"]
    base = resolve(str(params.get("api_base", "")), env)
    missing = missing_env(params, env)
    return {
        "id": model["id"],
        "label": model.get("label", model["id"]),
        "vendor": model.get("vendor", ""),
        "notes": model.get("notes", ""),
        "resolves_to": resolve(str(params.get("model", "")), env),
        "api_base_host": urlparse(base).hostname or base,
        "available": not missing,
        "missing": missing,
    }


def current_selection(catalog: Mapping[str, Any], active: Mapping[str, Any]) -> dict[str, str | None]:
    """Which catalog model each alias points at (None: not a catalog model, e.g. a broken key)."""
    by_alias = {m["model_name"]: m.get("litellm_params") for m in active.get("model_list", [])}
    return {
        alias: next((m["id"] for m in catalog["models"] if m["litellm_params"] == by_alias.get(alias)), None)
        for alias in catalog["aliases"]
    }


def compose(
    catalog: Mapping[str, Any],
    profiles: Mapping[str, str],
    selection: Mapping[str, str],
    env: Mapping[str, str],
) -> tuple[str, str]:
    """Config text for ``selection`` (alias -> catalog model id) and the profile it equals."""
    models = {m["id"]: m for m in catalog["models"]}
    aliases: list[str] = catalog["aliases"]
    if set(selection) != set(aliases):
        raise SelectionError(f"select a model for every alias: {', '.join(aliases)}")
    for alias, model_id in selection.items():
        if model_id not in models:
            raise SelectionError(f"unknown model '{model_id}' for {alias}")
        if missing := missing_env(models[model_id]["litellm_params"], env):
            raise SelectionError(f"model '{model_id}' is not configured: set {', '.join(missing)} in .env")
    base: dict[str, Any] = yaml.safe_load(profiles[catalog["base_profile"]])
    model_list = [{"model_name": a, "litellm_params": models[selection[a]]["litellm_params"]} for a in aliases]
    model_list += [m for m in base.get("model_list", []) if m["model_name"] not in aliases]
    config = {**base, "model_list": model_list}
    for name in sorted(profiles):
        if yaml.safe_load(profiles[name]) == config:
            return name, profiles[name]
    header = "".join(f"#   {a} -> {selection[a]}\n" for a in aliases)
    text = "# Composed by the gateway admin (UI model selector); not a named profile.\n" + header
    return CUSTOM, text + yaml.safe_dump(config, sort_keys=False)


def profile_config(profiles: Mapping[str, str], name: str, env: Mapping[str, str]) -> str:
    if name not in profiles:
        raise SelectionError(f"unknown profile '{name}'")
    text = profiles[name]
    if missing := missing_env(yaml.safe_load(text), env):
        raise SelectionError(f"profile '{name}' is not configured: set {', '.join(missing)} in .env")
    return text


# --------------------------------------------------------------------------- supervisor


class Gateway:
    """Owns the LiteLLM child process and the active config files."""

    def __init__(self, directory: Path, argv: list[str], health_url: str, env: Mapping[str, str]) -> None:
        self.directory = directory
        self.argv = argv
        self.health_url = health_url
        self.env = env
        self.lock = threading.Lock()
        self.child: subprocess.Popen[bytes] | None = None
        self.restarting = False

    # files
    def catalog(self) -> dict[str, Any]:
        data: dict[str, Any] = yaml.safe_load((self.directory / "catalog.yaml").read_text())
        return data

    def profiles(self) -> dict[str, str]:
        return {p.stem: p.read_text() for p in sorted((self.directory / "profiles").glob("*.yaml"))}

    def active(self) -> tuple[str, str]:
        marker = self.directory / "active.profile"
        profile = marker.read_text().strip() if marker.exists() else "unknown"
        return profile, (self.directory / "active.yaml").read_text()

    def _write(self, name: str, text: str) -> None:
        tmp = self.directory / f".{name}.tmp"
        tmp.write_text(text)
        tmp.replace(self.directory / name)

    def write_active(self, profile: str, text: str) -> None:
        self._write("active.yaml", text)
        self._write("active.profile", profile + "\n")

    # process
    def start(self) -> None:
        self.child = subprocess.Popen(self.argv)  # noqa: S603 - fixed argv from the container command

    def stop(self) -> None:
        if self.child and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.health_url, timeout=2) as response:  # noqa: S310 - fixed local URL
                return bool(response.status == HTTPStatus.OK)
        except (urllib.error.URLError, OSError):
            return False

    def restart(self, timeout_s: float) -> bool:
        self.stop()
        self.start()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.child and self.child.poll() is not None:
                return False
            if self.healthy():
                return True
            time.sleep(0.5)
        return False

    # API
    def view(self) -> dict[str, Any]:
        catalog, profiles = self.catalog(), self.profiles()
        profile, text = self.active()
        return {
            "profile": profile,
            "aliases": catalog["aliases"],
            "models": [describe_model(m, self.env) for m in catalog["models"]],
            "selection": current_selection(catalog, yaml.safe_load(text)),
            "profiles": [{"name": n, "missing": missing_env(yaml.safe_load(t), self.env)} for n, t in profiles.items()],
            "busy": self.lock.locked(),
        }

    def apply(self, body: Mapping[str, Any], timeout_s: float = 90.0) -> dict[str, Any]:
        profiles = self.profiles()
        if isinstance(body.get("profile"), str):
            name, text = body["profile"], profile_config(profiles, body["profile"], self.env)
        elif isinstance(body.get("selection"), dict):
            name, text = compose(self.catalog(), profiles, body["selection"], self.env)
        else:
            raise SelectionError("send either 'profile' or 'selection'")
        previous = self.active()
        if previous == (name, text):
            return self.view()
        log.warning("gateway: %s -> %s", previous[0], name)
        self.write_active(name, text)
        self.restarting = True
        try:
            if self.restart(timeout_s):
                return self.view()
            log.error("gateway did not become healthy on '%s'; rolling back to '%s'", name, previous[0])
            self.write_active(*previous)
            self.restart(timeout_s)
            raise RuntimeError(f"the gateway did not start on '{name}'; rolled back to '{previous[0]}'")
        finally:
            self.restarting = False


def make_handler(gateway: Gateway, master_key: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            log.info("admin %s", format % args)

        def _send(self, code: int, body: Mapping[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            supplied = self.headers.get("authorization", "").removeprefix("Bearer ")
            if master_key and hmac.compare_digest(supplied.encode(), master_key.encode()):
                return True
            self._send(HTTPStatus.UNAUTHORIZED, {"detail": "unauthorized"})
            return False

        def do_GET(self) -> None:
            if not self._authorized():
                return
            if self.path != "/catalog":
                self._send(HTTPStatus.NOT_FOUND, {"detail": "not found"})
                return
            self._send(HTTPStatus.OK, gateway.view())

        def do_POST(self) -> None:
            if not self._authorized():
                return
            if self.path != "/select":
                self._send(HTTPStatus.NOT_FOUND, {"detail": "not found"})
                return
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("content-length", 0)), 65536)))
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, {"detail": "invalid JSON"})
                return
            if not isinstance(body, dict):
                self._send(HTTPStatus.BAD_REQUEST, {"detail": "expected a JSON object"})
                return
            if not gateway.lock.acquire(blocking=False):
                self._send(HTTPStatus.CONFLICT, {"detail": "a gateway change is already in progress"})
                return
            try:
                self._send(HTTPStatus.OK, gateway.apply(body))
            except SelectionError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
            except RuntimeError as exc:
                self._send(HTTPStatus.BAD_GATEWAY, {"detail": str(exc)})
            finally:
                gateway.lock.release()

    return Handler


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s gateway-admin %(levelname)s %(message)s")
    child_argv = argv[argv.index("--") + 1 :] if "--" in argv else ["litellm", "--config", "/app/gateway/active.yaml"]
    directory = Path(os.environ.get("GATEWAY_DIR", "/app/gateway"))
    gateway = Gateway(
        directory,
        child_argv,
        os.environ.get("GATEWAY_HEALTH_URL", "http://127.0.0.1:4000/health/liveliness"),
        os.environ,
    )
    server = ThreadingHTTPServer(
        ("0.0.0.0", int(os.environ.get("GATEWAY_ADMIN_PORT", "4001"))),  # noqa: S104 - compose network only
        make_handler(gateway, os.environ.get("LITELLM_MASTER_KEY", "")),
    )
    stopping = threading.Event()

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    gateway.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while not stopping.wait(1.0):
        child = gateway.child
        if not gateway.restarting and child is not None and (code := child.poll()) is not None:
            log.error("litellm exited with %s", code)
            return code or 1
    gateway.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
