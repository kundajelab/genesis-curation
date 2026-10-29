"""Model switching must preserve queued provider identities and fail closed."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from genesis_curation.web import model_manager, runtime


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        runtime.initialize(root, "curator", "synthetic-password-for-tests")
        model_manager.initialize(root)
        state = {
            "active_provider": "qwen38-q8",
            "maintenance": False,
            "heartbeat": time.time(),
            "managed_providers": list(model_manager.PROVIDERS.values()),
        }

        def publish() -> None:
            with sqlite3.connect(root / "app.sqlite") as db:
                db.execute(
                    "INSERT OR REPLACE INTO model_manager_state VALUES(1,?)", (json.dumps(state),)
                )

        publish()
        gemma = runtime.enqueue(root, "probe", "curator", {"provider": "local-gemma-4-e4b"})
        qwen = runtime.enqueue(root, "probe", "curator", {"provider": "qwen38-q8"})
        claim = runtime.claim(root)
        assert claim is not None and claim["id"] == qwen
        assert runtime.job(root, gemma)["status"] == "QUEUED"
        runtime.finish(root, qwen, {"test": True})
        state["maintenance"] = True
        publish()
        assert runtime.claim(root) is None
        state.update(maintenance=False, active_provider="local-gemma-4-e4b")
        publish()
        claim = runtime.claim(root)
        assert claim is not None and claim["id"] == gemma
        assert claim["payload"] == {"provider": "local-gemma-4-e4b"}
        runtime.finish(root, gemma, {"test": True})
        legacy = runtime.enqueue(root, "mining", "curator", {"mining": "legacy"})
        assert runtime.claim(root) is None and runtime.job(root, legacy)["status"] == "QUEUED"
        with model_manager.exclusive(root, "local-gemma-4-e4b") as cancellation:
            assert not cancellation.is_set()
            assert model_manager.state(root)["evaluation"]
            assert runtime.claim(root) is None
            for action in (
                lambda: model_manager.request(root, "gemma-4-12b", "curator"),
                lambda: model_manager.lease(root, "second-evaluation", "local-gemma-4-e4b"),
            ):
                try:
                    action()
                except ValueError:
                    pass
                else:
                    raise AssertionError("Exclusive evaluation reservation bypassed")
        assert not model_manager.state(root)["evaluation"]
        requested = model_manager.request(root, "gemma-4-12b", "curator")
        assert requested["provider"] == "local-gemma-4-12b"
        try:
            model_manager.request(root, "gemma-4-e4b", "curator")
        except ValueError as error:
            assert "already" in str(error)
        else:
            raise AssertionError("Concurrent switch accepted")
        state["heartbeat"] = 0
        publish()
        assert not model_manager.state(root)["available"] and runtime.claim(root) is None
        try:
            model_manager.lease(root, "stale", "local-gemma-4-e4b")
        except ValueError as error:
            assert "not running" in str(error)
        else:
            raise AssertionError("Stale controller accepted an evaluation")

    path = Path(__file__).parents[1] / "deploy/web/model-controller.py"
    spec = importlib.util.spec_from_file_location("model_controller_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    controller = module.Controller.__new__(module.Controller)
    controller.baseline = {"alias": "baseline"}
    controller.value = {
        "rollback_container": "genesis-model-rollback-test",
        "active_provider": "qwen38-q8",
        "previous_provider": "qwen38-q8",
    }
    calls: list[tuple[str, ...]] = []

    def docker(*args: str) -> str:
        calls.append(args)
        return ""

    def publish_fake(**changes: object) -> None:
        controller.value.update(changes)

    def http(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"data": [{"id": "baseline"}]}

    controller.docker, controller.publish, controller.http = docker, publish_fake, http
    controller.rollback()
    assert not any(c[0] in {"rm", "stop", "rename"} for c in calls)
    assert controller.value["rollback_container"] is None
    calls.clear()
    controller.value.update(
        rollback_container="genesis-model-rollback-test", active_provider="local-gemma-4-e4b"
    )

    def recovery_docker(*args: str) -> str:
        calls.append(args)
        if args[0] == "ps":
            return "owned-container"
        if args[0] == "inspect":
            return json.dumps(
                [{"Config": {"Labels": {"com.docker.compose.project": "genesis-curation-web"}}}]
            )
        return ""

    controller.docker = recovery_docker
    controller.rollback()
    mutations = [call for call in calls if call[0] in {"rm", "rename", "start"}]
    assert mutations == [
        ("rm", "-f", module.NAME),
        ("rename", "genesis-model-rollback-test", module.NAME),
        ("start", module.NAME),
    ]
    assert controller.value["active_provider"] == "qwen38-q8"
    assert controller.value["rollback_container"] is None
    print(
        "PASS model manager: provider-bound queues, drain, stale controller, single request, "
        "exclusive evaluations, safe pre-rename and replacement recovery"
    )


if __name__ == "__main__":
    main()
