#!/usr/bin/env python3
"""Mark a plan task done after the coordinator verified `worker_done` (outcome succeeded + acceptance met).

Usage:
  settle_task.py <run_dir> <orca_task_id>   # mark one task done + sync KG
  settle_task.py <run_dir> --reconcile      # retry the KG sync of every pending task

Maps the Orca task id back to the plan id through task_map.json and appends it to done.json.
`done.json` is written through statefile.update_json (inter-process lock + atomic replace),
idempotent by plan ID: settling the same task twice leaves exactly one entry, and a non-list
file is refused instead of being overwritten. The KG sync runs only AFTER done.json has been
committed; if it fails, the plan id is recorded in knowledge/sync_pending.json so `--reconcile`
can retry without ever corrupting done.json.

All KG writes go through the validated API of scripts/kg.py (no direct JSONL append).
Do NOT call it for failed outcomes or artifacts that miss the acceptance line.
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kg  # noqa: E402  (validated single write API for the knowledge graph)
import statefile  # noqa: E402  (transactional JSON writes)


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _pending_path(run_dir):
    return os.path.join(kg.kg_dir(run_dir), "sync_pending.json")


def _record_pending(run_dir, plan_id, error):
    """Remember that done.json committed but the KG is not in sync yet. Never touches done.json."""
    def fn(data):
        data = data if isinstance(data, dict) else {}
        data[plan_id] = {"error": str(error), "ts": _now()}
        return data
    statefile.update_json(_pending_path(run_dir), fn, default={})


def _clear_pending(run_dir, plan_id):
    def fn(data):
        if isinstance(data, dict):
            data.pop(plan_id, None)
        return data
    statefile.update_json(_pending_path(run_dir), fn, default={})


def read_pending(run_dir):
    data = statefile.read_json(_pending_path(run_dir), {})
    return data if isinstance(data, dict) else {}


def sync_kg_task(run_dir, plan_id):
    """Write Task entity + depends_on/uses edges through kg.py's validated API.

    Raises on any KG contract violation; the caller decides to record pending (no silent swallow).
    Returns True when something was written, False when there is nothing to sync."""
    plan_path = os.path.join(run_dir, "plan.json")
    if not os.path.exists(plan_path):
        return False
    plan = statefile.read_json(plan_path, {}) or {}
    tasks_by_id = {t.get("id"): t for t in plan.get("tasks", [])}
    task_info = tasks_by_id.get(plan_id)
    if not task_info:
        return False

    now_str = _now()
    task_node_id = f"task:{plan_id}"
    # Outputs stay in Task.properties as path (+version tag when the plan carries one):
    # none of the 8 accepted edge types expresses "task produces artifact", so no output
    # edge is emitted (see runs/ai-pipeline-v2/artifacts/FE/README.md).
    outputs = [o for o in (task_info.get("outputs") or []) if o and o != "none"]

    kg.upsert_entity(
        run_dir, task_node_id, "Task", task_info.get("title", plan_id),
        body=task_info.get("change", ""),
        properties={
            "role": task_info.get("role"),
            "phase": task_info.get("phase"),
            "kind": task_info.get("kind", "worker"),
            "status": "done",
            "outputs": outputs,
        },
        created_at=now_str,
    )

    known = kg.read_entities(run_dir)
    for dep in task_info.get("deps") or []:
        dep_node_id = f"task:{dep}"
        dep_info = tasks_by_id.get(dep)
        if dep_info and dep_node_id not in known:
            kg.upsert_entity(run_dir, dep_node_id, "Task", dep_info.get("title", dep),
                             properties={"kind": dep_info.get("kind", "worker")},
                             created_at=now_str)
        kg.add_edge_checked(run_dir, task_node_id, dep_node_id, "depends_on",
                            valid_from=now_str, recorded_at=now_str,
                            source_ref=f"plan.json:{plan_id}")

    for inp in task_info.get("inputs") or []:
        if inp and inp != "none":
            art_id = kg.upsert_artifact_ref(run_dir, inp, created_at=now_str, kind="input")
            kg.add_edge_checked(run_dir, task_node_id, art_id, "uses",
                                valid_from=now_str, recorded_at=now_str,
                                source_ref=f"plan.json:{plan_id}")
    return True


def _resolve_plan_id(run_dir, task):
    task_map_path = os.path.join(run_dir, "task_map.json")
    if not os.path.exists(task_map_path):
        sys.exit(f"task_map.json not found in {run_dir}")
    tm = statefile.read_json(task_map_path, None)
    if not isinstance(tm, dict):
        sys.exit(f"task_map.json không hợp lệ (phải là object) trong {run_dir}")
    ids = [k for k, v in tm.items() if v == task]
    if not ids:
        sys.exit(f"unknown orca task id {task} for {run_dir}")
    return ids[0]


def settle(run_dir, task):
    run_dir = os.path.abspath(run_dir)
    plan_id = _resolve_plan_id(run_dir, task)
    done_path = os.path.join(run_dir, "done.json")

    def add(done):
        if done is None:
            done = []
        if not isinstance(done, list):
            raise statefile.StateCorrupt(
                f"done.json phải là list, thấy {type(done).__name__}; từ chối ghi đè")
        if plan_id not in done:
            done.append(plan_id)
        return done

    done = statefile.update_json(done_path, add, default=[])
    print("done:", plan_id, done)

    # KG sync only after done.json has been committed; a KG failure never loses done.json.
    try:
        sync_kg_task(run_dir, plan_id)
        _clear_pending(run_dir, plan_id)
    except Exception as ex:  # noqa: BLE001  (record pending, keep done.json authoritative)
        _record_pending(run_dir, plan_id, ex)
        print(f"Warning: KG sync pending for {plan_id} ({ex}); chạy "
              f"'settle_task.py {run_dir} --reconcile' để thử lại", file=sys.stderr)
    return plan_id


def reconcile(run_dir):
    run_dir = os.path.abspath(run_dir)
    pending = read_pending(run_dir)
    if not pending:
        print("no pending KG sync")
        return 0
    failed, succeeded = {}, {}
    for plan_id, rec in pending.items():
        try:
            sync_kg_task(run_dir, plan_id)
        except Exception as ex:  # noqa: BLE001
            failed[plan_id] = {"error": str(ex), "ts": _now()}
            print(f"  still pending: {plan_id} ({ex})", file=sys.stderr)
        else:
            succeeded[plan_id] = rec
            print(f"  reconciled: {plan_id}")

    def fn(data):
        data = data if isinstance(data, dict) else {}
        # Chỉ xoá key đã sync thành công khi giá trị hiện tại VẪN khớp giá trị đã đọc;
        # key do writer khác thêm/đổi trong lúc sync được giữ nguyên (không xoá công việc mới).
        for plan_id, rec in succeeded.items():
            if data.get(plan_id) == rec:
                data.pop(plan_id, None)
        for plan_id, rec in failed.items():
            data[plan_id] = rec
        return data

    statefile.update_json(_pending_path(run_dir), fn, default={})
    print(f"reconcile: {len(succeeded)} ok, {len(failed)} còn pending")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("orca_task_id", nargs="?")
    ap.add_argument("--reconcile", action="store_true",
                    help="retry the KG sync for every plan id recorded in knowledge/sync_pending.json")
    a = ap.parse_args()
    if a.reconcile:
        sys.exit(reconcile(a.run_dir))
    if not a.orca_task_id:
        ap.error("cần <orca_task_id>, hoặc dùng --reconcile")
    settle(a.run_dir, a.orca_task_id)


if __name__ == "__main__":
    main()
