"""
Turns a run's trace into a readable HTML timeline (open it in any browser).

Plain English: this is the agent's "show your working" page. Every step shows
what it decided and why, what it did, whether the guardrails allowed it,
whether the owner was asked, and the before → after proof for every change.
It answers the interview question "why did the agent do that?" by pointing.
"""
import html
import json


def _e(v) -> str:
    return html.escape(str(v))


def _diff_rows(before: dict, after: dict) -> str:
    rows = []
    for k in after:
        b, a = before.get(k) if before else None, after.get(k)
        changed = b != a
        rows.append(f"<tr class='{'chg' if changed else ''}'><td>{_e(k)}</td><td>{_e(b)}</td>"
                    f"<td>{'→' if changed else '='}</td><td>{_e(a)}</td></tr>")
    return "<table class='diff'><tr><th>field</th><th>before</th><th></th><th>after</th></tr>" + "".join(rows) + "</table>"


def _step(ev: dict) -> str:
    a, o = ev["action"], ev["observation"]
    if a is None:
        return (f"<li class='step warn'><div class='num'>{ev['step']}</div><div class='body'>"
                f"<div class='row'><span class='k'>reply</span>unreadable: {_e(o.get('error', ''))}</div></div></li>")
    if a["tool"] == "finish":
        return (f"<li class='step done'><div class='num'>{ev['step']}</div><div class='body'>"
                f"<div class='thought'>{_e(a.get('thought', ''))}</div><div class='tool'>finish</div></div></li>")
    status = o["status"]
    kind = {"ok": "ok", "denied_by_guardrail": "blocked", "denied_by_owner": "declined",
            "verification_failed": "bad", "error": "bad"}.get(status, "ok")
    args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in a["args"].items())
    parts = [f"<div class='thought'>{_e(a.get('thought', ''))}</div>",
             f"<div class='tool'>{_e(a['tool'])}<span class='args'>({_e(args)})</span></div>"]
    if status == "denied_by_guardrail":
        parts.append(f"<div class='row'><span class='k'>guard</span><span class='pill red'>blocked</span> {_e(o['reason'])}</div>")
    else:
        parts.append("<div class='row'><span class='k'>guard</span><span class='pill green'>allowed</span></div>")
    if o.get("owner"):
        cls = {"approved": "green", "answered": "green"}.get(o["owner"], "amber")
        answer = ""
        if o["owner"] == "answered" and isinstance(o.get("result"), dict):
            answer = " " + _e(" · ".join(str(v) for v in o["result"].values())[:300])
        parts.append(f"<div class='row'><span class='k'>owner</span><span class='pill {cls}'>{_e(o['owner'])}</span>"
                     f"{answer}</div>")
    if o.get("transient_retry"):
        parts.append("<div class='row'><span class='k'>retry</span>temporary outage, retried once and it went through</div>")
    if status == "error":
        parts.append(f"<div class='row'><span class='k'>error</span>{_e(o['error'])}</div>")
    if "verified" in o:
        pill = "<span class='pill green'>verified</span>" if o["verified"] else "<span class='pill red'>not verified</span>"
        note = " · first write didn't save, caught and retried" if o.get("auto_retried") else ""
        parts.append(f"<div class='row'><span class='k'>verify</span>{pill} {_e(o.get('check', ''))}{_e(note)}</div>")
        parts.append(_diff_rows(o["evidence"]["before"], o["evidence"]["after"]))
    elif status == "ok" and "result" in o:
        preview = json.dumps(o["result"], ensure_ascii=False, default=str, indent=1)
        parts.append(f"<details><summary>observed</summary><pre>{_e(preview[:4000])}</pre></details>")
    return (f"<li class='step {kind}'><div class='num'>{ev['step']}</div>"
            f"<div class='body'>{''.join(parts)}</div></li>")


def render(t: dict) -> str:
    usage = t.get("usage") or {}
    tokens = (usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0)
    receipt = [e for e in t["events"] if e["observation"].get("evidence")]
    pill = {True: "<span class='pill green'>verified</span>", False: "<span class='pill red'>not verified</span>"}
    receipt_rows = "".join(
        f"<tr><td>{e['step']}</td><td><code>{_e(e['action']['tool'])}</code></td>"
        f"<td>{_e(', '.join(f'{k}={v}' for k, v in e['action']['args'].items() if k not in ('body', 'question')))}</td>"
        f"<td>{pill[bool(e['observation']['verified'])]}</td></tr>" for e in receipt) or \
        "<tr><td colspan='4'>No changes made.</td></tr>"
    owner = "".join(
        f"<li>Asked: {_e(r['question'].splitlines()[0])} → <b>{_e(r['choice'] or 'none of these')}</b></li>"
        if r["tool"] == "ask_owner" else
        f"<li>Approve <code>{_e(r['tool'])}</code>? → <b>{'yes' if r['approved'] else 'no'}</b></li>"
        for r in t.get("approvals", [])) or "<li>Not needed.</li>"
    outcome = "Finished" if t["finished"] else f"Stopped: {t['stop_reason']}"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Shift decision trace</title>
<style>
:root{{--bg:#faf8f5;--card:#fff;--ink:#1d1b18;--mute:#6b665e;--line:#e7e2da;--green:#1f7a4d;--red:#b42318;
--amber:#a15c07;--accent:#9a4d1c;--chg:#fff4d6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151412;--card:#1f1d1a;--ink:#eeeae4;--mute:#a39d93;--line:#34302b;
--green:#5cc48d;--red:#f27a6e;--amber:#e3a548;--accent:#e08a52;--chg:#3a3120}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:860px;margin:0 auto;padding:32px 16px 64px}}
.eyebrow{{color:var(--accent);font-size:12px;letter-spacing:.12em;text-transform:uppercase;font-weight:700}}
h1{{font-size:22px;margin:6px 0 4px}}.meta{{color:var(--mute);font-size:13px}}
.stats{{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 28px}}.stat{{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:8px 12px;font-size:13px}}.stat b{{display:block;font-size:17px}}
h2{{font-size:15px;margin:28px 0 10px}}ol{{list-style:none;padding:0;margin:0}}
.step{{display:flex;gap:12px;margin-bottom:10px}}.num{{flex:none;width:28px;height:28px;border-radius:50%;
display:grid;place-items:center;font-size:12px;font-weight:700;background:var(--line)}}
.body{{flex:1;min-width:0;background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);
border-radius:10px;padding:10px 14px}}.ok .body{{border-left-color:var(--green)}}.blocked .body,.bad .body{{border-left-color:var(--red)}}
.declined .body,.warn .body{{border-left-color:var(--amber)}}.done .body{{border-left-color:var(--accent)}}
.thought{{font-style:italic;color:var(--mute);margin-bottom:4px}}.tool{{font-family:ui-monospace,monospace;font-weight:600;
overflow-wrap:anywhere;word-break:break-all}}.args{{font-weight:400;color:var(--mute)}}.row{{margin-top:6px;font-size:14px;overflow-wrap:anywhere}}
.k{{display:inline-block;width:56px;color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.06em}}
.pill{{font-size:12px;font-weight:700;padding:1px 8px;border-radius:99px;border:1px solid currentColor}}
.green{{color:var(--green)}}.red{{color:var(--red)}}.amber{{color:var(--amber)}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}}td,th{{text-align:left;padding:4px 8px;
border-bottom:1px solid var(--line);overflow-wrap:anywhere}}th{{color:var(--mute);font-weight:600}}
.diff tr.chg td{{background:var(--chg)}}.panel{{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:10px 14px}}details{{margin-top:6px}}summary{{cursor:pointer;color:var(--mute);font-size:13px}}
pre{{font-size:12px;overflow:auto;max-height:260px;background:var(--bg);padding:8px;border-radius:6px}}
ul{{margin:0;padding-left:18px}}
</style></head><body><main>
<div class="eyebrow">Shift · decision trace</div>
<h1>{_e(t['goal'])}</h1><div class="meta">{_e(outcome)}</div>
<div class="stats">
 <div class="stat"><b>{t['steps']}</b>steps</div><div class="stat"><b>{t['writes']}</b>changes</div>
 <div class="stat"><b>{len(receipt)}</b>verified changes</div>
 <div class="stat"><b>{len(t.get('approvals', []))}</b>owner touchpoints</div>
 <div class="stat"><b>{t['seconds']}s</b>time</div>{f'<div class="stat"><b>{tokens:,}</b>tokens</div>' if tokens else ''}
</div>
<h2>Timeline</h2><ol>{''.join(_step(e) for e in t['events'])}</ol>
<h2>Receipt: every change, verified</h2><div class="panel"><table><tr><th>step</th><th>action</th><th>args</th>
<th>verified</th></tr>{receipt_rows}</table></div>
<h2>Owner involvement</h2><div class="panel"><ul>{owner}</ul></div>
<h2>Agent's summary</h2><div class="panel">{_e(t['summary'] or '(none)')}</div>
</main></body></html>"""
