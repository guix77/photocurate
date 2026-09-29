import json

from photocurate.annotate import SCALE, annotate
from photocurate.cli import main
from photocurate.sets import create_set


def test_annotation_repeats_scale_corrects_previous_including_last_and_resumes(store, indexed):
    create_set(store, "sample", indexed, 3, 42)
    answers = iter(("0", "b", "3", "1", "q"))
    output = []
    result = annotate(
        store, "sample", "Guix", viewer=False, ask=lambda _: next(answers), output=output.append
    )
    assert result["labelled"] == 2
    members = store.members(store.get_set("sample")["id"])
    assert [row["label"] for row in members] == ["3", "1", None]
    assert output.count(SCALE) == 5
    answers = iter(("2", "b", "3", "q"))
    result = annotate(
        store, "sample", "Guix", viewer=False, ask=lambda _: next(answers), output=output.append
    )
    assert result["labelled"] == 3
    assert store.members(store.get_set("sample")["id"])[2]["label"] == "3"
    history = store.db.execute("SELECT label FROM label_history ORDER BY id").fetchall()
    assert [row[0] for row in history] == ["0", "3", "1", "2", "3"]


def test_cli_runtime_paths_prepare_and_report_without_key(tmp_path, corpus, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    common = ["--state-dir", str(tmp_path / "state"), "--env-file", str(tmp_path / "missing.env")]
    assert main([*common, "index", str(corpus)]) == 0
    assert (
        main([*common, "sample", "create", "sample", "--source", str(corpus), "--size", "3"]) == 0
    )
    assert main([*common, "run", "sample", "--model", "test/model", "--prepare-only"]) == 0
    assert main([*common, "report", "1"]) == 0
    report = json.loads((tmp_path / "state/reports/runs-1.json").read_text())
    assert report["runs"][0]["operations"]["pending"] == 3
    assert report["runs"][0]["config"]["temperature"] is None
    assert main([*common, "resume", "1"]) == 1
    assert "OPENROUTER_API_KEY is missing" in capsys.readouterr().err


def test_env_file_key_loaded_without_being_stored(tmp_path, corpus, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=unique-test-secret\n")
    common = ["--state-dir", str(tmp_path / "state"), "--env-file", str(env)]
    assert main([*common, "index", str(corpus)]) == 0
    assert (
        main([*common, "sample", "create", "sample", "--source", str(corpus), "--size", "1"]) == 0
    )
    assert main([*common, "run", "sample", "--model", "test/model", "--prepare-only"]) == 0
    assert "unique-test-secret" not in (tmp_path / "state/state.sqlite3").read_bytes().decode(
        "utf-8", errors="ignore"
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
