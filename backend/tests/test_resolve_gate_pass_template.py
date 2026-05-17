"""Runtime Gate Pass template path (sidecar / env override)."""

from pathlib import Path

import pytest


def test_resolve_gate_pass_prefers_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    docx = tmp_path / "custom_gate.docx"
    docx.write_bytes(b"fake")
    monkeypatch.setenv("GATE_PASS_TEMPLATE_DOCX", str(docx))
    monkeypatch.delenv("SAATHI_BASE_DIR", raising=False)

    from app.config import resolve_gate_pass_template_docx

    assert resolve_gate_pass_template_docx() == docx.resolve()


def test_resolve_gate_pass_saathi_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    saathi = tmp_path / "Saathi"
    cached = saathi / "templates" / "word" / "Gate Pass Template.docx"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"fake")
    monkeypatch.delenv("GATE_PASS_TEMPLATE_DOCX", raising=False)
    monkeypatch.setenv("SAATHI_BASE_DIR", str(saathi))

    from app.config import resolve_gate_pass_template_docx

    assert resolve_gate_pass_template_docx() == cached.resolve()
