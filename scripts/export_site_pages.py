"""Export static snapshots of task pages for the project page's task browser.

For each selected rule, fact location and variant, the task server is started
and the pages a literal agent passes through are saved exactly as served:
inbox, request email, item page, booking form, and the review page (after
submitting the people the request names). Links and buttons are made inert.

    python scripts/export_site_pages.py --suite data/suite --out docs/tasks
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: the running example and one rule per reasoning operation (paper Fig. 2)
RULES = {
    "hot_tub_pregnancy": ("Quantity", "Running example (compare)"),
    "button_battery": ("Look-up & binding", "Membership"),
    "roster_step_free": ("Look-up & binding", "Bind entity"),
    "altitude_copd": ("Quantity", "Compare"),
    "boat_load": ("Quantity", "Aggregate"),
    "alcohol_window": ("Quantity", "Date arithmetic"),
    "airbag_on_infant": ("State & version", "Project state"),
    "analgesic_substitution": ("State & version", "Resolve version"),
    "alarm_pad_rooms": ("State & version", "Assign & match"),
}
PLACEMENTS = {"profiles": "Site record", "thread": "Request email (quoted)", "request": "Request email (pasted)"}
ARMS = {"R": "Harm only on screen", "B": "Benign twin", "Ei": "Stated by the user",
        "E": "Stated by the requester", "Eq": "Requester's words, relayed"}
INERT = "<style>a,button,input,select,textarea,label{pointer-events:none!important}</style>"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPEN = urllib.request.build_opener(NoRedirect)


def fetch(port, host, path, data=None) -> str:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host},
                                 data=urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None)
    try:
        return OPEN.open(req, timeout=10).read().decode()
    except urllib.error.HTTPError as e:            # 303 after a POST
        return e.read().decode()


def snapshot(task_dir: Path, out: Path) -> dict:
    task = json.loads((task_dir / "task.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    ev = out / "_events.jsonl"
    srv = subprocess.Popen([sys.executable, str(task_dir / "server.py"), "--task", str(task_dir / "task.json"),
                            "--port", "0", "--events", str(ev)], stdout=subprocess.PIPE, text=True)
    try:
        port = int(srv.stdout.readline())
        mail, site = task["mail"]["host"], task["site"]["host"]
        item = task["urls"][0].rsplit("/", 1)[-1]
        pages = {"1_inbox": fetch(port, mail, "/"),
                 "2_email": fetch(port, mail, "/m/0"),
                 "3_item": fetch(port, site, f"/item/{item}"),
                 "4_form": fetch(port, site, f"/book/{item}")}
        form = {}
        field = next((f for f in task["site"]["form"] if f["kind"].startswith("profile_")), None)
        if field:
            form[field["name"]] = task["requested"]["names"]
        fetch(port, site, f"/book/{item}", form)
        pages["5_review"] = fetch(port, site, "/review")
    finally:
        srv.kill()
        ev.unlink(missing_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    for name, html in pages.items():
        (out / f"{name}.html").write_text(html.replace("</head>", INERT + "</head>", 1))
    return {"instruction": task["instruction"], "today": task["today"],
            "requester": task["mail"]["messages"][0]["from"],
            "urls": {"mail": f"http://{mail}/", "site": f"http://{site}/"},
            "risk": task["risk"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    suite, out = Path(a.suite), Path(a.out)
    from seam.rules import RULES as ALL
    by_id = {r["id"]: r for r in ALL}
    index = {"placements": PLACEMENTS, "arms": ARMS, "rules": []}
    for rid, (tier, op) in RULES.items():
        r = by_id[rid]
        entry = {"id": rid, "tier": tier, "operation": op, "family": r.get("family"), "tasks": {}}
        for pl in PLACEMENTS:
            for arm in ARMS:
                d = suite / f"lh_{rid}.{pl}__{arm}"
                entry["tasks"][f"{pl}/{arm}"] = snapshot(d, out / rid / pl / arm)
        index["rules"].append(entry)
        print("exported", rid)
    (out / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
