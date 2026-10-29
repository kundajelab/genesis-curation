"""Pinned model artifact integrity and bounded controller downloads."""

from __future__ import annotations

import hashlib
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from genesis_curation.llm import local_models


def main() -> None:
    models = local_models.catalog()
    assert len({m["id"] for m in models}) == 3
    assert all(len(m["revision"]) == 40 and len(m["sha256"]) == 64 for m in models)
    assert all(m["evaluation"] == "Biological accuracy not evaluated" for m in models)
    data = b"synthetic model artifact"
    item = {**models[0], "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with (
            patch.object(local_models, "preset", return_value=item),
            patch.object(local_models.urllib.request, "urlopen", return_value=io.BytesIO(data)),
        ):
            result = local_models.download("gemma-4-12b", root)
            assert local_models.verify(Path(result["path"]), item)
            with patch.object(local_models.urllib.request, "urlopen", side_effect=AssertionError):
                assert local_models.download("gemma-4-12b", root)["status"] == "verified"
            Path(result["path"]).write_bytes(b"tampered")
            try:
                local_models.download("gemma-4-12b", root)
            except ValueError as error:
                assert "refusing overwrite" in str(error)
            else:
                raise AssertionError("Accepted tampered artifact")
        Path(result["path"]).unlink()
        with (
            patch.object(local_models, "preset", return_value=item),
            patch.object(local_models.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")),
        ):
            try:
                local_models.download("gemma-4-12b", root)
            except ValueError as error:
                assert "verification" in str(error)
            else:
                raise AssertionError("Accepted incomplete artifact")
            assert not list(root.iterdir())
    print("PASS local models: pinned catalog, checksum, reuse, tamper and partial-file rejection")


if __name__ == "__main__":
    main()
