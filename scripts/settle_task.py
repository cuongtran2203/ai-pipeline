#!/usr/bin/env python3
"""Mark a plan task done after the coordinator verified `worker_done` (outcome succeeded + acceptance met).

Usage: settle_task.py <run_dir> <orca_task_id>     (task id = payload.taskId of the worker_done message)
Maps the Orca task id back to the plan id through task_map.json and appends it to done.json.
Syncs task completion and dependency edges (task depends_on task) into knowledge graph.
Do NOT call it for failed outcomes or artifacts that miss the acceptance line.
"""
import datetime as dt
import json
import os
import sys

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


# Sync Task entity and depends_on edges to Knowledge Graph
def sync_kg_task(run_dir, plan_id):
    try:
        plan_path = os.path.join(run_dir, "plan.json")
        if not os.path.exists(plan_path):
            return
        plan = json.load(open(plan_path, encoding="utf-8"))
        task_info = None
        for t in plan.get("tasks", []):
            if t.get("id") == plan_id:
                task_info = t
                break
        if not task_info:
            return

        kd = os.path.join(os.path.abspath(run_dir), "knowledge")
        os.makedirs(kd, exist_ok=True)
        ep = os.path.join(kd, "entities.jsonl")
        edp = os.path.join(kd, "edges.jsonl")

        now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        task_node_id = f"task:{plan_id}"

        # 1. Task entity
        task_ent = {
            "id": task_node_id,
            "type": "Task",
            "title": task_info.get("title", plan_id),
            "body": task_info.get("change", ""),
            "properties": {
                "role": task_info.get("role"),
                "phase": task_info.get("phase"),
                "kind": task_info.get("kind", "worker"),
                "status": "done",
            },
            "created_at": now_str,
        }
        with open(ep, "a", encoding="utf-8") as f:
            f.write(json.dumps(task_ent, ensure_ascii=False) + "\n")

        # 2. Edges: task depends_on task (strictly depends_on, NOT causal)
        new_edges = []
        for dep in task_info.get("deps", []):
            dep_node_id = f"task:{dep}"
            new_edges.append({
                "source": task_node_id,
                "target": dep_node_id,
                "type": "depends_on",
                "valid_from": now_str,
                "valid_to": None,
                "recorded_at": now_str,
                "source_ref": f"plan.json:{plan_id}",
                "confidence": 1.0,
            })

        # Inputs used
        for inp in task_info.get("inputs", []):
            if inp and inp != "none":
                new_edges.append({
                    "source": task_node_id,
                    "target": f"artifact:{inp}",
                    "type": "uses",
                    "valid_from": now_str,
                    "valid_to": None,
                    "recorded_at": now_str,
                    "source_ref": f"plan.json:{plan_id}",
                    "confidence": 1.0,
                })

        # Outputs
        for out in task_info.get("outputs", []):
            if out and out != "none":
                new_edges.append({
                    "source": f"artifact:{out}",
                    "target": task_node_id,
                    "type": "evidenced_by",
                    "valid_from": now_str,
                    "valid_to": None,
                    "recorded_at": now_str,
                    "source_ref": f"plan.json:{plan_id}",
                    "confidence": 1.0,
                })

        # Deduplicate and append edges
        seen = set()
        if os.path.exists(edp):
            with open(edp, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            obj = json.loads(line)
                            seen.add((obj.get("source"), obj.get("target"), obj.get("type")))
                        except ValueError:
                            pass

        with open(edp, "a", encoding="utf-8") as f:
            for ed in new_edges:
                key = (ed["source"], ed["target"], ed["type"])
                if key not in seen:
                    seen.add(key)
                    f.write(json.dumps(ed, ensure_ascii=False) + "\n")

    except Exception as ex:
        print(f"Warning: KG sync skipped in settle_task ({ex})", file=sys.stderr)


sync_kg_task(run_dir, plan_id)
