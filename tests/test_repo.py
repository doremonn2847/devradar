import os
import subprocess

from devradar.collector.repo import resolve_project


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "a.txt").write_text("a", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")


def test_worktree_maps_to_main(tmp_path):
    main = tmp_path / "main"
    _make_repo(main)
    wt = tmp_path / "wt-feature"
    _git(main, "worktree", "add", "-q", str(wt))
    key_wt, name_wt = resolve_project(str(wt))
    key_main, name_main = resolve_project(str(main))
    assert key_wt == key_main
    assert name_wt == name_main == "main"


def test_missing_dir_falls_back(tmp_path):
    p = str(tmp_path / "gone" / "proj")
    key, name = resolve_project(p)
    assert key == os.path.normcase(os.path.normpath(p))
    assert name == "proj"


def test_none_cwd():
    assert resolve_project(None) == ("unknown", "unknown")


def test_unicode_path(tmp_path):
    repo = tmp_path / "Đề Cũ"
    _make_repo(repo)
    _, name = resolve_project(str(repo))
    assert name == "Đề Cũ"


def test_deleted_subfolder_resolves_to_enclosing_repo(tmp_path):
    repo = tmp_path / "mvp-exam-assistant"
    _make_repo(repo)
    gone = repo / "tmp" / "tc" / "159"          # scratch folder an agent used, since deleted
    key, name = resolve_project(str(gone))
    assert (key, name) == resolve_project(str(repo))
    assert name == "mvp-exam-assistant"
