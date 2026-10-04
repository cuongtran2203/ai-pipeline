#!/usr/bin/env python3
"""Trang thai giao dich (transactional state) cho file JSON/JSONL dung chung nhieu tien trinh.

Sua loi P1 "ghi trang thai khong giao dich" (runs/ai-pipeline-v2/reviews/debate_v2_improvements.md):
thay cho read-modify-write khong khoa va append truc tiep, moi thao tac o day duoc bao ve
bang khoa lien tien trinh + ghi nguyen tu (temp roi os.replace).

API:
    read_json(path, default=None) -> du lieu | default   # chiu BOM UTF-8; thieu file/hong -> default
    update_json(path, fn, default=None) -> du lieu moi    # khoa + doc-sua-ghi + temp/os.replace; file hong/RONG -> StateCorrupt (khong ghi de)
    append_jsonl(path, obj) -> None                        # khoa + 1 lan ghi + flush/fsync
    read_jsonl(path, strict=False) -> list                 # bo qua dong cuoi ghi do / dong hong; strict=True -> bao loi dong hong GIUA file
    file_lock(path, timeout=...)                           # context manager khi can giu khoa qua nhieu buoc

Bao dam:
- Nhieu tien trinh cung ghi khong mat ban ghi, khong ghi de lan nhau.
- File chinh luon la JSON/JSONL hop le: ghi ra file tam cung thu muc, fsync, roi os.replace.
- Chi file KHONG TON TAI moi la legacy-missing (dung default); file ton tai ma RONG la corrupt.
- Khoa do OS quan ly (msvcrt.locking tren Windows, fcntl.flock tren POSIX): tien trinh chet
  giua chung tu nha khoa, khong de lai khoa mo coi. Cho qua timeout -> LockTimeout.

Gioi han: khong phai CSDL (khong index/truy van), doc/ghi toan bo file nen chi hop file nho;
tren Windows os.replace co the vuong neu mot tien trinh khac dang mo file dich (co retry ngan).

Stdlib only.
"""
import contextlib
import json
import os
import tempfile
import time

try:
    import msvcrt  # Windows
except ImportError:
    msvcrt = None

try:
    import fcntl  # POSIX
except ImportError:
    fcntl = None

DEFAULT_TIMEOUT = 30.0
LOCK_SUFFIX = ".lock"
_REPLACE_TRIES = 25  # Windows: os.replace can fail briefly while a reader has the file open
_REPLACE_DELAY = 0.04


class LockTimeout(RuntimeError):
    """Khong lay duoc khoa trong thoi gian cho phep."""


# --- Khoa lien tien trinh (OS tu nha khi tien trinh chet -> khong khoa mo coi) ---

def _lock_acquire(handle):
    if msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def open_read(path, encoding="utf-8-sig", tries=None, delay=None):
    """open(path) for reading that survives a concurrent os.replace on Windows (PermissionError for a moment)."""
    tries = _REPLACE_TRIES if tries is None else tries
    delay = _REPLACE_DELAY if delay is None else delay
    for attempt in range(tries):
        try:
            return open(path, encoding=encoding)
        except PermissionError:
            if attempt == tries - 1 or os.name != "nt":
                raise
            time.sleep(delay)


def _lock_release(handle):
    if msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

@contextlib.contextmanager
def file_lock(path, timeout=DEFAULT_TIMEOUT, poll=0.02):
    """Giu khoa doc quyen cho `path` (khoa dat o <path>.lock)."""
    if msvcrt is None and fcntl is None:
        raise RuntimeError("khong co khoa lien tien trinh stdlib tren nen tang nay")
    lock_path = path + LOCK_SUFFIX
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    handle = open(lock_path, "a+b")
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        if os.fstat(handle.fileno()).st_size == 0:
            # Initialising the lock file races with another process that already locks byte 0 (Windows
            # refuses a write into a locked range). Locking works on an empty file, so losing this race is harmless.
            try:
                handle.write(b"\0")
                handle.flush()
            except OSError:
                pass
        while True:
            try:
                _lock_acquire(handle)
                break
            except OSError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise LockTimeout(f"het {timeout}s cho khoa {lock_path}")
                time.sleep(poll)
        try:
            yield
        finally:
            _lock_release(handle)
    finally:
        handle.close()


# --- Ghi nguyen tu ---

def _fsync_dir(directory):
    if os.name != "posix":
        return
    try:
        dfd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _replace_atomic(tmp, path):
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(_REPLACE_DELAY)


def _atomic_write(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        handle = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
    except BaseException:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        with handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_atomic(tmp, path)
        _fsync_dir(directory)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- API cong khai ---

def read_json(path, default=None):
    """Doc JSON; chiu BOM UTF-8. Thieu file hoac JSON hong -> tra `default`."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class StateCorrupt(ValueError):
    """File trang thai ton tai nhung khong phai JSON hop le: KHONG ghi de (tranh mat du lieu)."""


def _read_strict(path, default):
    """Thieu file -> default; file ton tai ma RONG hoac hong -> StateCorrupt (giu nguyen file).

    Chi file KHONG TON TAI moi duoc coi la legacy-missing va dung default. Mot file da ton tai
    nhung rong thuong la dau hieu write bi cat/hong: ghi de bang `fn(default)` se che mat su co.
    """
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return default
    if not text.strip():
        raise StateCorrupt(
            f"{path} rong (file ton tai nhung khong co JSON); khong ghi de. "
            "Sua/xoa tay hoac khoi phuc ban sao.")
    try:
        return json.loads(text)
    except ValueError as e:
        raise StateCorrupt(f"{path} khong phai JSON hop le ({e}); khong ghi de. Sua/xoa tay hoac khoi phuc ban sao.") from e


def update_json(path, fn, default=None):
    """Khoa + doc-sua-ghi nguyen tu. `fn(du_lieu_cu)` tra du lieu moi; tra ve du lieu moi.
    File hong hoac RONG (da ton tai) -> StateCorrupt, khong bao gio ghi de im lang."""
    with file_lock(path):
        updated = fn(_read_strict(path, default))
        _atomic_write(path, updated)
        return updated

def append_jsonl(path, obj):
    """Them 1 ban ghi JSONL duoi cung khoa; ghi 1 lan roi flush/fsync."""
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with file_lock(path):
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


def read_jsonl(path, strict=False):
    """Doc JSONL.

    - Mac dinh (strict=False): bo qua dong trong va dong khong parse duoc (ke ca dong cuoi ghi do).
    - strict=True: chi cho phep DONG CUOI bi ghi do (khong co newline ket thuc). Bat ky dong hong
      hoac dong trong o GIUA file -> StateCorrupt, de reader audit/KG/journal khong che mat ban ghi.
    """
    out = []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            raw_lines = f.readlines()
    except OSError:
        return out
    content_idx = [i for i, ln in enumerate(raw_lines) if ln.strip()]
    last = content_idx[-1] if content_idx else -1
    for i, line in enumerate(raw_lines):
        stripped = line.strip()
        if not stripped:
            if strict and i < last:
                raise StateCorrupt(
                    f"{path}: dong trong o giua file (dong {i + 1}) - co the do ghi do/chen ngang; khong che mat ban ghi.")
            continue
        try:
            out.append(json.loads(stripped))
        except ValueError as e:
            if strict:
                # Dòng cuối bị cắt dở khi append (không có newline) là trường hợp duy nhất được bỏ qua.
                if i == last and not line.endswith("\n"):
                    continue
                raise StateCorrupt(
                    f"{path}: dong {i + 1} khong phai JSON hop le ({e}); ban ghi bi hong/mat.") from e
            continue
    return out
