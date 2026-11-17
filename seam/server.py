#!/usr/bin/env python3
"""Long-horizon task server: a webmail client and one service site, from one
task file. Standard library only -- it runs inside the OSWorld guest, which
has python3 and no pip and no network.

Two virtual hosts on one port, told apart by the Host header (the guest maps
both names to 127.0.0.1 in /etc/hosts; locally Chromium's host-resolver-rules
does the same):

  mail host  /              inbox
             /m/<i>         one message, with a Reply button
             /m/<i>/reply   reply form; POST sends
             /sent          after sending
  site host  /              catalogue
             /item/<id>     item page (the object fact)
             /book/<id>     booking form (the binding; saved profiles if any)
             /review        summary with Confirm / Edit / Cancel
             /done          after confirming
             /profiles      saved profiles

Every request is appended to events.jsonl: page views (exposure), the form
submission, the confirmation (the outcome) and the reply. The outcome is a
file read, not a judge.

    python3 server.py --task task.json --port 80 --events events.jsonl
"""

from __future__ import annotations

import argparse
import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOCK = threading.Lock()
E = html.escape

CSS = """
*{box-sizing:border-box}body{margin:0;font:15px/1.5 -apple-system,"Segoe UI",Roboto,Inter,Arial,sans-serif;color:#1f2328;background:#f6f7f9}
a{color:inherit}.top{display:flex;align-items:center;gap:14px;padding:0 28px;height:60px;background:#fff;border-bottom:1px solid #e3e6ea}
.logo{width:30px;height:30px;border-radius:7px;color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center}
.brand{font-weight:700;font-size:18px}.nav{display:flex;gap:6px;margin-left:24px}.nav a{padding:6px 12px;border-radius:7px;text-decoration:none;color:#57606a}
.nav a.on{background:#f0f2f5;color:#1f2328;font-weight:600}.who{margin-left:auto;color:#57606a;font-size:14px}
.main{max-width:980px;margin:26px auto;padding:0 24px}h1{font-size:24px;margin:0 0 6px}.sub{color:#57606a;margin:0 0 18px}
.card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:18px 20px;margin-bottom:16px}
.card h3{margin:0 0 10px;font-size:16px}.row{display:flex;align-items:center;gap:14px;padding:12px 0;border-top:1px solid #f0f2f4}
.row:first-of-type{border-top:0}.grow{flex:1}.mut{color:#57606a;font-size:14px}.price{font-weight:600}
.btn{display:inline-block;border:1px solid #d0d7de;background:#fff;border-radius:8px;padding:8px 16px;font:inherit;cursor:pointer;text-decoration:none;color:#1f2328}
.btn.p{color:#fff;border-color:transparent}.fact{background:#f6f8fa;border:1px solid #e3e6ea;border-radius:8px;padding:10px 14px;margin:10px 0}
label.f{display:block;font-weight:600;margin:14px 0 6px}input[type=text],select,textarea{font:inherit;padding:8px 10px;border:1px solid #d0d7de;border-radius:7px;width:100%;max-width:420px;background:#fff}
textarea{max-width:none;height:140px}.opt{display:flex;gap:10px;align-items:flex-start;padding:8px 0}.opt input{margin-top:4px}
.note{color:#1f2328;font-size:14px;text-align:right;max-width:55%}.kv{display:grid;grid-template-columns:180px 1fr;gap:6px 14px}.kv div:nth-child(odd){color:#57606a}
.mail{display:grid;grid-template-columns:200px 1fr;gap:20px}.folders a{display:block;padding:7px 12px;border-radius:7px;text-decoration:none;color:#57606a}
.folders a.on{background:#e8eefc;color:#1f2328;font-weight:600}.msg{display:grid;grid-template-columns:180px 1fr 70px;gap:12px;padding:12px 14px;border-top:1px solid #f0f2f4;text-decoration:none;color:inherit}
.msg:first-child{border-top:0}.msg.unread{font-weight:600}.msg:hover{background:#f6f8fa}.body p{margin:0 0 12px}
"""


def page(title, brand, color, nav, on, who, body):
    navh = "".join(f'<a href="{h}" class="{"on" if k == on else ""}">{E(k)}</a>' for k, h in nav)
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{E(title)} — {E(brand)}</title>'
            f'<style>{CSS}.btn.p{{background:{color}}}</style></head><body>'
            f'<div class="top"><div class="logo" style="background:{color}">{E(brand[0])}</div>'
            f'<div class="brand">{E(brand)}</div><div class="nav">{navh}</div>'
            f'<div class="who">{E(who)}</div></div><div class="main">{body}</div></body></html>')


def preview(lines, n=70):
    """Inbox preview: the first line that is not a greeting, cut at a word.
    `body[0][:60]` showed every request as "Hi Alex," and cut the filler
    mails mid-number ("06:00 to 08:0"), which every external reviewer read as
    a broken page (QC 2026-11-02)."""
    txt = next((l for l in lines if l.strip() and not (l.rstrip().endswith(",")
                and len(l.split()) <= 3)), lines[0] if lines else "")
    txt = " ".join(txt.split())
    if len(txt) <= n:
        return txt
    return txt[:n].rsplit(" ", 1)[0].rstrip(",;:·—-") + "…"


def whoami(task_id):
    import hashlib
    return hashlib.sha1(("lh:" + task_id).encode()).hexdigest()[:12]


def priced(p):
    return bool(p) and p.strip() not in ("—", "-", "–")


class App(BaseHTTPRequestHandler):
    task: dict
    events: str
    t0: float
    pending: dict = {}
    sent: list = []

    def log_message(self, *a):
        pass

    def _rec(self, **kw):
        kw["t"] = round(time.time() - self.t0, 3)
        with LOCK, open(self.events, "a") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")

    def _send(self, body: str, code=200):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _go(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def _form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return {k: v for k, v in parse_qs(self.rfile.read(n).decode()).items()}

    def _host(self):
        return (self.headers.get("Host") or "").split(":")[0]

    # ---------------------------------------------------------------- mail
    def _mail(self, path, method):
        t = self.task
        m = t["mail"]
        nav = []            # the folder list is the navigation; a second copy in the bar read as a glitch
        who = t["user"]["name"]
        msgs = m["messages"]
        if path == "/" and method == "GET":
            rows = "".join(
                f'<a class="msg{" unread" if x.get("unread") else ""}" href="/m/{i}">'
                f'<div>{E(x["from"])}</div><div>{E(x["subject"])}'
                f'<span class="mut"> — {E(preview(x["body"]))}</span></div>'
                f'<div class="mut">{E(x["when"])}</div></a>' for i, x in enumerate(msgs))
            body = (f'<div class="mail"><div class="folders"><a class="on" href="/">Inbox</a>'
                    f'<a href="/sent">Sent</a></div><div><h1>Inbox</h1>'
                    f'<div class="card" style="padding:4px 6px">{rows}</div></div></div>')
            return self._send(page("Inbox", m["brand"], m["color"], nav, "Inbox", who, body))
        parts = path.strip("/").split("/")
        if parts[0] == "m" and len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) < len(msgs):
            i = int(parts[1])
            x = msgs[i]
            if len(parts) == 2 and method == "GET":
                paras = "".join(f"<p>{self._linkify(p)}</p>" for p in x["body"])
                body = (f'<div class="mail"><div class="folders"><a href="/">Inbox</a>'
                        f'<a href="/sent">Sent</a></div><div><div class="card"><h1 style="font-size:20px">'
                        f'{E(x["subject"])}</h1><div class="mut">From {E(x["from"])} &lt;{E(x["addr"])}&gt;'
                        f' · To {E(t["user"]["name"])} · {E(x["when"])}</div>'
                        f'<div class="body" style="margin-top:16px">{paras}</div>'
                        f'<a class="btn p" href="/m/{i}/reply">Reply</a> '
                        f'<a class="btn" href="/">Back to inbox</a></div></div></div>')
                return self._send(page(x["subject"], m["brand"], m["color"], nav, "Inbox", who, body))
            if len(parts) == 3 and parts[2] == "reply":
                if method == "GET":
                    body = (f'<div class="card"><h1 style="font-size:20px">Reply</h1>'
                            f'<div class="mut">To {E(x["from"])} &lt;{E(x["addr"])}&gt;</div>'
                            f'<form method="post"><label class="f">Subject</label>'
                            f'<input type="text" name="subject" value="Re: {E(x["subject"])}">'
                            f'<label class="f">Message</label><textarea name="text"></textarea>'
                            f'<div style="margin-top:14px"><button class="btn p" type="submit">Send</button> '
                            f'<a class="btn" href="/m/{i}">Discard</a></div></form></div>')
                    return self._send(page("Reply", m["brand"], m["color"], nav, "Inbox", who, body))
                f = self._form()
                rec = {"to": x["addr"], "who": x["from"], "subject": (f.get("subject") or [""])[0],
                       "text": (f.get("text") or [""])[0], "when": "just now"}
                App.sent.append(rec)
                self._rec(event="reply", **{k: rec[k] for k in ("to", "subject", "text")})
                return self._go("/sent?ok=1")
        if path == "/sent":
            # an empty Sent folder after sending made the agent resend the
            # reply until the step limit (dry run, 2026-11-01)
            ok = "ok=1" in (urlparse(self.path).query or "")
            rows = "".join(
                f'<div class="msg"><div>To: {E(r["who"])}</div><div>{E(r["subject"])}'
                f'<span class="mut"> — {E(preview([r["text"]]))}</span></div><div class="mut">{E(r["when"])}</div></div>'
                for r in reversed(App.sent)) or '<div class="mut" style="padding:12px">No sent messages.</div>'
            note = ('<div class="card" style="border-color:#a7d7b8;background:#eefaf2">'
                    'Message sent.</div>' if ok else "")
            body = ('<div class="mail"><div class="folders"><a href="/">Inbox</a>'
                    '<a class="on" href="/sent">Sent</a></div><div><h1>Sent</h1>'
                    f'{note}<div class="card" style="padding:4px 6px">{rows}</div></div></div>')
            return self._send(page("Sent", m["brand"], m["color"], nav, "Sent", who, body))
        return self._send(page("Not found", m["brand"], m["color"], nav, "", who,
                               '<div class="card">Page not found.</div>'), 404)

    def _linkify(self, s):
        out = E(s)
        for u in self.task["urls"]:
            out = out.replace(E(u), f'<a href="{E(u)}" style="color:#0969da">{E(u)}</a>')
        return out

    # ---------------------------------------------------------------- site
    def _site(self, path, method):
        t = self.task
        s = t["site"]
        nav = [(s["nav"][0], "/"), (s["nav"][1], "/profiles")]
        who = t["user"]["name"]
        cat = {c["id"]: c for c in s["catalog"]}

        def P(title, on, body):
            return self._send(page(title, s["brand"], s["color"], nav, on, who, body))

        if path == "/" and method == "GET":
            rows = "".join(
                f'<div class="row"><div class="grow"><div style="font-weight:600">{E(c["name"])}</div>'
                f'<div class="mut">{E(c["summary"])}</div></div><div class="price">{E(c["price"]) if priced(c["price"]) else ""}</div>'
                f'<a class="btn" href="/item/{c["id"]}">View</a></div>' for c in s["catalog"])
            return P(s["nav"][0], s["nav"][0],
                     f'<h1>{E(s["home_title"])}</h1><p class="sub">{E(s["home_sub"])}</p>'
                     f'<div class="card">{rows}</div>')
        if path == "/profiles":
            return P(s["profiles_title"], s["nav"][1],
                     f'<h1>{E(s["profiles_title"])}</h1><div class="card">{self._profiles()}</div>')
        parts = path.strip("/").split("/")
        if parts[0] == "item" and len(parts) == 2 and parts[1] in cat:
            c = cat[parts[1]]
            return P(c["name"], s["nav"][0],
                     f'<div class="mut"><a href="/">{E(s["nav"][0])}</a> › {E(c["name"])}</div>'
                     f'<h1 style="margin-top:8px">{E(c["name"])}</h1><div class="card">'
                     + (f'<div class="price" style="font-size:18px">{E(c["price"])}</div>' if priced(c["price"]) else "")
                     + f'<p>{E(c["summary"])}</p>'
                     + (f'<div class="kv" style="margin:6px 0 16px"><div>Details</div><div>{E(c["fact"])}</div></div>' if c["fact"] else "")
                     + (f'<a class="btn p" href="/book/{c["id"]}">{E(s["book_label"])}</a> '
                        if c.get("action", True) else "")
                     + f'<a class="btn" href="/">Back</a></div>')
        if parts[0] == "book" and len(parts) == 2 and parts[1] in cat:
            c = cat[parts[1]]
            if method == "POST":
                f = self._form()
                vals = {}
                for fl in s["form"]:
                    v = f.get(fl["name"], [])
                    vals[fl["name"]] = v if fl["kind"] in ("profile_checkbox", "checkbox") else (v[0] if v else "")
                App.pending = {"item": c["id"], "fields": vals}
                self._rec(event="form", item=c["id"], fields=vals)
                return self._go("/review")
            return P(s["book_label"], s["nav"][0],
                     f'<div class="mut"><a href="/">{E(s["nav"][0])}</a> › '
                     f'<a href="/item/{c["id"]}">{E(c["name"])}</a> › {E(s["book_label"])}</div>'
                     f'<h1 style="margin-top:8px">{E(s["book_label"])}: {E(c["name"])}</h1>'
                     f'<div class="card"><form method="post">{self._fields()}'
                     f'<div style="margin-top:18px"><button class="btn p" type="submit">Continue</button> '
                     f'<a class="btn" href="/item/{c["id"]}">Back</a></div></form></div>'
                     + (f'<div class="card"><h3>{E(s["profiles_title"])}</h3>{self._profiles()}'
                        + ("".join(f'<div class="row"><div class="grow">{E(n)}</div></div>' for n in s.get("notes") or []))
                        + '</div>' if s.get("profiles") else ""))
        if path == "/review" and App.pending:
            c = cat[App.pending["item"]]
            lab = {fl["name"]: fl["label"] for fl in s["form"]}
            kv = "".join(f'<div>{E(lab[k])}</div><div>{E(", ".join(v) if isinstance(v, list) else v) or "—"}</div>'
                         for k, v in App.pending["fields"].items())
            return P("Review", s["nav"][0],
                     f'<h1>Review and confirm</h1><div class="card"><div class="kv">'
                     f'<div>Item</div><div>{E(c["name"])}</div>'
                     + (f'<div>Details</div><div>{E(c["fact"])}</div>' if c["fact"] else "")
                     + (f'<div>Price</div><div>{E(c["price"])}</div>' if priced(c["price"]) else "")
                     + f'{kv}'
                     + "".join(f'<div>Note</div><div>{E(x)}</div>' for x in s.get("details") or [])
                     + '</div></div>'
                     f'<form method="post" action="/confirm" style="display:inline">'
                     f'<button class="btn p" type="submit">{E(s["confirm_label"])}</button></form> '
                     f'<a class="btn" href="/book/{c["id"]}">Edit</a> '
                     f'<form method="post" action="/cancel" style="display:inline">'
                     f'<button class="btn" type="submit">Cancel</button></form>')
        if path == "/confirm" and method == "POST" and App.pending:
            self._rec(event="confirm", **App.pending)
            App.pending = {}
            return self._go("/done")
        if path == "/cancel" and method == "POST":
            self._rec(event="cancel", **(App.pending or {}))
            App.pending = {}
            return self._go("/")
        if path == "/done":
            return P("Done", s["nav"][0], f'<div class="card"><h1>{E(s["done_title"])}</h1>'
                     f'<p class="mut">Reference {E(s["ref"])}. A copy has been sent to '
                     f'{E(t["user"]["email"])}.</p><a class="btn" href="/">Back to {E(s["brand"])}</a></div>')
        return P("Not found", "", '<div class="card">Page not found.</div>')

    def _profiles(self):
        ps = self.task["site"].get("profiles") or []
        # where the notes live elsewhere (an email), the cards carry no notes
        # column at all, so the site never says "No notes" against the email
        show = self.task["site"].get("show_notes", True)
        return "".join(
            f'<div class="row"><div class="grow"><div style="font-weight:600">{E(p["name"])}</div>'
            f'<div class="mut">{E(p["role"])}</div></div>'
            + (f'<div class="{"note" if p["note"] else "mut"}">{E(p["note"] or "No notes")}</div>' if show else "")
            + '</div>'
            for p in ps) or '<div class="mut">No saved profiles.</div>'

    def _fields(self):
        s = self.task["site"]
        names = [p["name"] for p in s.get("profiles") or []]
        out = []
        for fl in s["form"]:
            k = fl["kind"]
            out.append(f'<label class="f">{E(fl["label"])}</label>')
            if k == "text":
                out.append(f'<input type="text" name="{fl["name"]}">')
            elif k == "select":
                out.append(f'<select name="{fl["name"]}"><option value="">Choose…</option>'
                           + "".join(f'<option>{E(o)}</option>' for o in fl["options"]) + "</select>")
            elif k == "profile_select":
                out.append(f'<select name="{fl["name"]}"><option value="">Choose…</option>'
                           + "".join(f'<option>{E(n)}</option>' for n in names) + "</select>")
            elif k in ("radio", "checkbox"):
                out.append("".join(f'<label class="opt"><input type="{k}" name="{fl["name"]}" '
                                   f'value="{E(o)}"><span>{E(o)}</span></label>' for o in fl["options"]))
            elif k in ("profile_radio", "profile_checkbox"):
                typ = "radio" if k == "profile_radio" else "checkbox"
                out.append("".join(f'<label class="opt"><input type="{typ}" name="{fl["name"]}" '
                                   f'value="{E(n)}"><span>{E(n)}</span></label>' for n in names))
        return "".join(out) or '<p class="mut">No details needed. Continue to review the change.</p>'

    # -------------------------------------------------------------- routing
    def _route(self, method):
        u = urlparse(self.path)
        host = self._host()
        path = u.path.rstrip("/") or "/"
        if path == "/whoami":          # identity check, not part of the task
            # a hash, not the id: the id carries the arm, and the page is
            # reachable from the agent's browser
            return self._send(self.task.get("token") or whoami(self.task["id"]))
        if method == "GET":
            self._rec(event="page", host=host, path=path)
        if host == self.task["mail"]["host"]:
            return self._mail(path, method)
        return self._site(path, method)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--events", default="events.jsonl")
    a = ap.parse_args()
    App.task = json.load(open(a.task))
    App.events = a.events
    App.t0 = time.time()
    open(a.events, "w").close()
    # --port 0 lets the OS pick, and the real port is reported on stdout. The
    # caller cannot pick a free port and bind it later: two episodes picked the
    # same port and one browser was served the OTHER task's site (2026-11-01).
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), App)
    print(srv.server_address[1], flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
