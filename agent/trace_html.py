"""
Turns a run's trace into a readable HTML timeline (open it in any browser).

Plain English: this is the agent's "show your working" page. Every step shows
what it decided and why, what it did, whether the guardrails allowed it,
whether the owner was asked, and the before → after proof for every change.
It also shows what each step cost: time to think, time to act, and tokens.
It answers the interview question "why did the agent do that?" by pointing.
"""
import html
import json


def _e(v) -> str:
    return html.escape(str(v))


def _size(n: int) -> str:
    return f"{n:,} B" if n < 1024 else f"{n / 1024:.1f} KB"


def _diff_rows(before: dict, after: dict) -> str:
    rows = []
    for k in after:
        b, a = before.get(k) if before else None, after.get(k)
        changed = b != a
        rows.append(f"<tr class='{'chg' if changed else ''}'><td>{_e(k)}</td><td>{_e(b)}</td>"
                    f"<td>{'→' if changed else '='}</td><td>{_e(a)}</td></tr>")
    return "<table class='diff'><tr><th>field</th><th>before</th><th></th><th>after</th></tr>" + "".join(rows) + "</table>"


def _cost_chips(ev: dict) -> str:
    """The small 'what this step cost' line at the top of a step's card."""
    c = ev.get("cost")
    if not c:
        return ""
    chips = [f"<span title='time the model took to decide'>think {c['think_s']}s</span>",
             f"<span title='time the action took (includes waiting for the owner)'>act {c['act_s']}s</span>"]
    if c.get("input_tokens") or c.get("output_tokens"):
        chips.append(f"<span title='tokens sent to / received from the model'>{c['input_tokens']:,} in · "
                     f"{c['output_tokens']:,} out</span>")
    chips.append(f"<span title='size of what the step handed back'>{_size(c.get('data_bytes', 0))}</span>")
    return f"<div class='cost'>{''.join(chips)}</div>"


def _step(ev: dict) -> str:
    a, o = ev["action"], ev["observation"]
    if a is None:
        return (f"<li class='step warn'><div class='num'>{ev['step']}</div><div class='body'>{_cost_chips(ev)}"
                f"<div class='row'><span class='k'>reply</span>unreadable: {_e(o.get('error', ''))}</div></div></li>")
    if a["tool"] == "finish":
        return (f"<li class='step done'><div class='num'>{ev['step']}</div><div class='body'>{_cost_chips(ev)}"
                f"<div class='thought'>{_e(a.get('thought', ''))}</div><div class='tool'>finish</div></div></li>")
    status = o["status"]
    kind = {"ok": "ok", "denied_by_guardrail": "blocked", "denied_by_owner": "declined",
            "verification_failed": "bad", "error": "bad"}.get(status, "ok")
    args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in a["args"].items())
    parts = [_cost_chips(ev),
             f"<div class='thought'>{_e(a.get('thought', ''))}</div>",
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


def _cost_table(events: list[dict]) -> str:
    """'Where the time and tokens went': one row per step, with a bar for think vs act time."""
    rows = [e for e in events if e.get("cost")]
    if not rows:
        return ""
    longest = max(e["cost"]["think_s"] + e["cost"]["act_s"] for e in rows) or 1
    has_tokens = any(e["cost"].get("input_tokens") or e["cost"].get("output_tokens") for e in rows)
    out = []
    for e in rows:
        c = e["cost"]
        tool = e["action"]["tool"] if e["action"] else "(unreadable reply)"
        tokens = f"<td class='n'>{c['input_tokens']:,}</td><td class='n'>{c['output_tokens']:,}</td>" if has_tokens else ""
        out.append(
            f"<tr><td class='n'>{e['step']}</td><td><code>{_e(tool)}</code></td>"
            f"<td class='barcell'><div class='bar'><i class='think' style='width:{100 * c['think_s'] / longest:.1f}%'></i>"
            f"<i class='act' style='width:{100 * c['act_s'] / longest:.1f}%'></i></div></td>"
            f"<td class='n'>{c['think_s']}s</td><td class='n'>{c['act_s']}s</td>{tokens}"
            f"<td class='n'>{_size(c.get('data_bytes', 0))}</td></tr>")
    think, act = sum(e["cost"]["think_s"] for e in rows), sum(e["cost"]["act_s"] for e in rows)
    total_tokens = (f"<td class='n'>{sum(e['cost']['input_tokens'] for e in rows):,}</td>"
                    f"<td class='n'>{sum(e['cost']['output_tokens'] for e in rows):,}</td>") if has_tokens else ""
    token_head = "<th class='n'>tokens in</th><th class='n'>tokens out</th>" if has_tokens else ""
    return (
        "<h2>Where the time and tokens went</h2><div class='panel'>"
        "<div class='legend'><span><i class='think'></i>thinking (the model deciding)</span>"
        "<span><i class='act'></i>acting (the tool, plus any wait for the owner)</span></div>"
        "<table class='costs'><tr><th class='n'>step</th><th>action</th><th></th><th class='n'>think</th>"
        f"<th class='n'>act</th>{token_head}<th class='n'>data back</th></tr>{''.join(out)}"
        f"<tr class='total'><td></td><td>Total</td><td></td><td class='n'>{think:.1f}s</td><td class='n'>{act:.1f}s</td>"
        f"{total_tokens}<td class='n'>{_size(sum(e['cost'].get('data_bytes', 0) for e in rows))}</td></tr></table></div>")


STYLE = """
:root{--bg:#2a2a2a;--card:#323232;--sunk:#242424;--ink:#f9f5ed;--mute:#b9b4aa;--line:#454545;--denim:#5e83ae;
--sky:#9dbbe0;--green:#86cba3;--red:#f0897c;--amber:#e8b866;--chg:rgba(94,131,174,.22)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:900px;margin:0 auto;padding:40px 20px 72px}
.eyebrow{color:var(--sky);font-size:12px;letter-spacing:.16em;text-transform:uppercase;font-weight:700}
h1{font:italic 600 30px/1.25 Georgia,"Times New Roman",serif;margin:10px 0 12px;text-wrap:balance}
.outcome{display:inline-block;font-size:13px;font-weight:600;padding:3px 12px;border-radius:99px;background:var(--denim);
color:var(--ink)}.outcome.stopped{background:transparent;color:var(--red);border:1px solid var(--red)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:10px;margin:24px 0 8px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;font-size:12px;
color:var(--mute);text-transform:uppercase;letter-spacing:.06em}
.stat b{display:block;font:italic 600 24px/1.2 Georgia,serif;color:var(--ink);letter-spacing:0;text-transform:none;
font-variant-numeric:tabular-nums}
h2{font:italic 600 19px/1.3 Georgia,serif;margin:40px 0 14px;color:var(--ink)}
h2::after{content:"";display:block;width:36px;height:3px;border-radius:2px;background:var(--denim);margin-top:8px}
ol{list-style:none;padding:0;margin:0;position:relative}
ol::before{content:"";position:absolute;left:15px;top:8px;bottom:8px;width:2px;background:var(--line)}
.step{display:flex;gap:14px;margin-bottom:12px;position:relative}
.num{flex:none;width:32px;height:32px;border-radius:50%;display:grid;place-items:center;font-size:13px;font-weight:700;
background:var(--bg);border:2px solid var(--line);color:var(--ink);font-variant-numeric:tabular-nums}
.ok .num{border-color:var(--green)}.blocked .num,.bad .num{border-color:var(--red)}
.declined .num,.warn .num{border-color:var(--amber)}.done .num{border-color:var(--denim);background:var(--denim)}
.body{flex:1;min-width:0;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px}
.blocked .body,.bad .body{border-color:rgba(240,137,124,.5)}.declined .body,.warn .body{border-color:rgba(232,184,102,.5)}
.done .body{border-color:var(--denim)}
.cost{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.cost span{font-size:11px;color:var(--mute);background:var(--sunk);border-radius:99px;padding:2px 9px;
font-variant-numeric:tabular-nums}
.thought{font-style:italic;color:var(--mute);margin-bottom:6px}
.tool{font-family:ui-monospace,Consolas,monospace;font-weight:600;color:var(--sky);overflow-wrap:anywhere;word-break:break-all}
.args{font-weight:400;color:var(--mute)}.row{margin-top:8px;font-size:14px;overflow-wrap:anywhere}
.k{display:inline-block;width:60px;color:var(--mute);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.pill{font-size:12px;font-weight:700;padding:1px 9px;border-radius:99px;border:1px solid currentColor}
.green{color:var(--green)}.red{color:var(--red)}.amber{color:var(--amber)}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}
td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
tr:last-child td{border-bottom:0}th{color:var(--mute);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.diff tr.chg td{background:var(--chg)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;overflow-x:auto}
.panel table{margin-top:0}
.barcell{width:34%;min-width:90px}.bar{display:flex;height:8px;border-radius:4px;background:var(--sunk);overflow:hidden}
.bar i{display:block;height:100%}.think{background:var(--denim)}.act{background:var(--ink)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12px;color:var(--mute);margin-bottom:8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}
.total td{font-weight:700;border-top:1px solid var(--line)}
code{font-family:ui-monospace,Consolas,monospace;color:var(--sky)}
details{margin-top:8px}summary{cursor:pointer;color:var(--mute);font-size:13px}
pre{font-size:12px;overflow:auto;max-height:260px;background:var(--sunk);padding:10px;border-radius:8px;margin:8px 0 0}
ul{margin:0;padding-left:18px}
.summary{font-size:15px;border-left:3px solid var(--denim)}
footer{margin-top:40px;color:var(--mute);font-size:12px;text-align:center}
"""


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
    outcome = ("<span class='outcome'>Finished</span>" if t["finished"] else
               f"<span class='outcome stopped'>Stopped: {_e(t['stop_reason'])}</span>")
    token_stat = f'<div class="stat"><b>{tokens:,}</b>tokens</div>' if tokens else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Shift decision trace</title>
<style>{STYLE}</style></head><body><main>
<div class="eyebrow">Shift · decision trace</div>
<h1>{_e(t['goal'])}</h1>{outcome}
<div class="stats">
 <div class="stat"><b>{t['steps']}</b>steps</div><div class="stat"><b>{t['writes']}</b>changes</div>
 <div class="stat"><b>{len(receipt)}</b>verified changes</div>
 <div class="stat"><b>{len(t.get('approvals', []))}</b>owner touchpoints</div>
 <div class="stat"><b>{t['seconds']}s</b>time</div>{token_stat}
</div>
<h2>Timeline</h2><ol>{''.join(_step(e) for e in t['events'])}</ol>
{_cost_table(t['events'])}
<h2>Receipt: every change, verified</h2><div class="panel"><table><tr><th>step</th><th>action</th><th>args</th>
<th>verified</th></tr>{receipt_rows}</table></div>
<h2>Owner involvement</h2><div class="panel"><ul>{owner}</ul></div>
<h2>Agent's summary</h2><div class="panel summary">{_e(t['summary'] or '(none)')}</div>
<footer>Every change above was re-read from the system after it was made.</footer>
</main></body></html>"""
