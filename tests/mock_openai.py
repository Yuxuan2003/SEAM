"""A scripted stand-in for a model behind an OpenAI-compatible endpoint.

It follows a task's request literally (open the request email, follow its
link, book, tick the people the email names, continue, confirm, done), one
action per call, choosing elements from the element list in the prompt. Used
by the end-to-end test to exercise the real client, prompts, action parser,
browser, task server and scorer without calling a paid model.

    SEAM_MOCK_TASK=<task.json> python tests/mock_openai.py   # prints its port
"""

from __future__ import annotations

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TASK = json.loads(open(os.environ["SEAM_MOCK_TASK"]).read())


def plan(task):
    subj = task["mail"]["messages"][0]["subject"]
    steps = [lambda l: l.startswith("link:") and subj in l,
             lambda l: l.startswith("link:") and task["urls"][0] in l,
             lambda l: l.startswith("link:") and task["site"]["book_label"] in l]
    for name in task["requested"]["names"]:
        steps.append(lambda l, nm=name: (l.startswith("checkbox:") or l.startswith("radio:"))
                     and nm in l and "[unchecked]" in l)
    steps.append(lambda l: l == "button: Continue")
    steps.append(lambda l: l == f"button: {task['site']['confirm_label']}")
    return steps


STEPS = plan(TASK)
ELEM = re.compile(r"\[(\d+)\] (.*?)(?=; \[\d+\] |\Z)", re.S)


def decide(messages):
    k = sum(1 for m in messages if m["role"] == "assistant")
    text = messages[-1]["content"]
    text = text if isinstance(text, str) else " ".join(c.get("text", "") for c in text if c.get("type") == "text")
    if k >= len(STEPS):
        return {"type": "done"}
    i = text.find("Elements: ")
    marks = text[i + len("Elements: "):] if i >= 0 else ""
    for n, lab in ELEM.findall(marks):
        if STEPS[k](" ".join(lab.split())) or STEPS[k](lab.strip()):
            return {"type": "click", "element": int(n)}
    return {"type": "reply_to_user", "text": f"mock: step {k} found no element"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        act = decide(body["messages"])
        out = {"id": "mock", "object": "chat.completion", "created": 0, "model": body.get("model", "mock"),
               "choices": [{"index": 0, "finish_reason": "stop",
                            "message": {"role": "assistant", "content": json.dumps({"action": act})}}],
               "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        raw = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    print(srv.server_address[1], flush=True)
    sys.stdout.close()
    srv.serve_forever()
