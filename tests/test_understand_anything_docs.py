# -*- coding: utf-8 -*-
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_understand_anything_snapshot_exists_with_required_sections() -> None:
    snapshot = REPO_ROOT / ".understand-anything" / "README.md"

    assert snapshot.exists()
    content = snapshot.read_text(encoding="utf-8")

    assert "# Repository Knowledge Graph Snapshot" in content
    assert "## Entrypoints and run modes" in content
    assert "## Directory collaboration" in content
    assert "## Accepted scope for future `.understand-anything/` updates" in content
    assert "main.py" in content
    assert "server.py" in content
    assert "data_provider/" in content
    assert "apps/dsa-web/" in content


def test_contributing_guides_document_understand_anything_boundary() -> None:
    zh = (REPO_ROOT / "docs" / "CONTRIBUTING.md").read_text(encoding="utf-8")
    en = (REPO_ROOT / "docs" / "CONTRIBUTING_EN.md").read_text(encoding="utf-8")

    assert ".understand-anything/" in zh
    assert "静态、可审查、可再生成" in zh
    assert ".understand-anything/" in en
    assert "static, reviewable, regenerable" in en


def test_changelog_mentions_knowledge_graph_docs_entry() -> None:
    changelog = (REPO_ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")

    assert ".understand-anything/README.md" in changelog
