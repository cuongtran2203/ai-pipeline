#!/usr/bin/env python3
"""Mark a plan task done after the coordinator verified `worker_done` (outcome succeeded + acceptance met).

Usage: settle_task.py <run_dir> <orca_task_id>     (task id = payload.taskId of the worker_done message)
Maps the Orca task id back to the plan id through task_map.json and appends it to done.json.
Syncs task completion and dependency edges (task depends_on task) into the knowledge graph.
All KG writes go through the validated API of scripts/kg.py (no direct JSONL append).
Do NOT call it for failed outcomes or artifacts that miss the acceptance line.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kg  # noqa: E402  (validated single write API for the knowledge graph)

if len(sys.argv) < 3:
    sys.exit("Usage: settle_task.py <run_dir> <orca_task_id>")

run_dir, task = sys.argv[1], sys.argv[2]
task_map_path = os.path.join(run_dir, "task_map.json")
if not os.path.exists(task_map_path):
    sys.exit(f"task_map.json not found in {run_dir}")

tm = json.load(open(task_map_path, encoding="utf-8"))
ids = [k for k, v in tm.items() if v == task]
if not ids:
    sys.exit(f"unknown orca task id {task} for {run_dir}")

plan_id = ids[0]
p = os.path.join(run_dir, "done.json")
done = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []
if plan_id not in done:
    done.append(plan_id)
json.dump(done, open(p, "w", encoding="utf-8"), indent=2)
print("done:", plan_id, done)


def sync_kg_task(run_dir, plan_id):
    """Write Task entity + depends_on/uses edges through kg.py's validated API."""
    try:
        plan_path = os.path.join(run_dir, "plan.json")
        if not os.path.exists(plan_path):
            return
        plan = json.load(open(plan_path, encoding="utf-8"))
        tasks_by_id = {t.get("id"): t for t in plan.get("tasks", [])}
        task_info = tasks_by_id.get(plan_id)
        if not task_info:
            return

        now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        task_node_id = f"task:{plan_id}"
        outputs = [o for o in (task_info.get("outputs") or []) if o and o != "none"]
        known = kg.read_entities(run_dir)

        # 1. Task entity. Outputs are recorded in properties: none of the 8 edge types expresses
        #    "task produces artifact", so we deliberately emit NO output edge (see FB README).
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

        # 2. depends_on: ensure dependency Task entities exist (from plan), then link.
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

        # 3. Inputs used: ensure the Artifact entity exists, then Task --uses--> Artifact.
        for inp in task_info.get("inputs") or []:
            if inp and inp != "none":
                art_id = kg.upsert_artifact_ref(run_dir, inp, created_at=now_str, kind="input")
                kg.add_edge_checked(run_dir, task_node_id, art_id, "uses",
                                    valid_from=now_str, recorded_at=now_str,
                                    source_ref=f"plan.json:{plan_id}")

    except Exception as ex:
        print(f"Warning: KG sync skipped in settle_task ({ex})", file=sys.stderr)


sync_kg_task(run_dir, plan_id)
