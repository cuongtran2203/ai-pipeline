"""Lay ban framework moi nhat tu git cho `ai-pipeline update`. Stdlib only.

Chi `git ls-remote` / `git clone` vao thu muc tam roi sao chep file; khong pip install,
khong chay code cua repo tai ve.
"""
import contextlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

DEFAULT_REPO = "https://github.com/cuongtran2203/ai-pipeline.git"
REPO_ENV = "AI_PIPELINE_REPO"
GIT_TIMEOUT = 120

_VER = re.compile(r"^v?(\d+(?:\.\d+)*)(.*)$")
_HEX = re.compile(r"^[0-9a-fA-F]{7,40}$")


class UpdateError(Exception):
    pass


def resolve_repo(arg=None):
    return arg or os.environ.get(REPO_ENV) or DEFAULT_REPO


def parse_version(text):
    """'1.2.0.rc' -> ((1,2,0), 'rc'); '1.2.0' -> ((1,2,0), ''); khong phai semver -> None."""
    m = _VER.match(text.strip())
    if not m:
        return None
    nums = tuple(int(x) for x in m.group(1).split("."))
    while len(nums) > 1 and nums[-1] == 0:
        nums = nums[:-1]
    return nums, m.group(2).strip(".-_+").lower()


def version_key(text):
    """Khoa so sanh: ban on dinh > pre-release cung so; pre-release so theo chuoi hau to."""
    p = parse_version(text)
    if p is None:
        return None
    nums, pre = p
    return nums, (1, "") if not pre else (0, pre)


def is_newer(candidate, current):
    a, b = version_key(candidate), version_key(current)
    return a is not None and b is not None and a > b


def pick_latest(tags, pre=False):
    best = None
    for t in tags:
        p = parse_version(t)
        if p is None or (p[1] and not pre):
            continue
        if best is None or version_key(t) > version_key(best):
            best = t
    return best


def _git(args, cwd=None):
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=GIT_TIMEOUT, cwd=cwd)
    except FileNotFoundError:
        raise UpdateError("khong tim thay `git` trong PATH. Cai git roi chay lai, hoac dung --offline.")
    except subprocess.TimeoutExpired:
        raise UpdateError(f"git qua {GIT_TIMEOUT}s khong phan hoi (mang cham/chet?). Thu lai hoac dung --offline.")
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip().splitlines()
        raise UpdateError(f"git {args[0]} loi: {detail[-1] if detail else 'ma ' + str(r.returncode)}")
    return r.stdout


def list_tags(repo):
    out = _git(["ls-remote", "--tags", "--refs", "--", repo])
    tags = []
    for line in out.splitlines():
        _, _, ref = line.partition("\t")
        if ref.startswith("refs/tags/"):
            tags.append(ref[len("refs/tags/"):])
    return tags


def latest_tag(repo, pre=False):
    tag = pick_latest(list_tags(repo), pre)
    if not tag:
        raise UpdateError(f"{repo} khong co tag release hop le" + ("" if pre else " (thu --pre hoac --ref)"))
    return tag


def _rmtree(path):
    def fix(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=fix)


def read_version(src, fallback):
    f = Path(src) / "ai_pipeline" / "__init__.py"
    if f.is_file():
        m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", f.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return fallback


@contextlib.contextmanager
def fetched(repo, ref):
    """Clone nong <ref> vao thu muc tam, yield Path; luon don thu muc tam."""
    tmp = Path(tempfile.mkdtemp(prefix="ai-pipeline-update-"))
    try:
        dest = tmp / "src"
        try:
            _git(["clone", "--quiet", "--depth", "1", "--branch", ref, "--", repo, str(dest)])
        except UpdateError:
            if not _HEX.match(ref):
                raise UpdateError(f"khong clone duoc ref '{ref}' tu {repo} (ref/tag/branch co ton tai khong?)")
            if dest.exists():
                _rmtree(dest)
            _git(["clone", "--quiet", "--", repo, str(dest)])
            _git(["checkout", "--quiet", ref], cwd=str(dest))
        if not (dest / "skills").is_dir() or not (dest / "AGENTS.md").is_file():
            raise UpdateError(f"{repo}@{ref} khong phai repo ai-pipeline (thieu skills/ hoac AGENTS.md)")
        yield dest
    finally:
        _rmtree(tmp)
