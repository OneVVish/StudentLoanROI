#!/usr/bin/env python3
"""Daily traffic digest: landings by page, cold vs internal, and by source.

    python3 analyze_traffic.py                 # yesterday, plus 7-day windows
    python3 analyze_traffic.py --asof 2026-08-02   # re-run a missed day
    python3 analyze_traffic.py --days 14 -o traffic.csv

Local-only, like analyze_survey.py and analyze_model.py -- never imported by
app.py. Needs .streamlit/secrets.toml for the Supabase connection.

WHY THIS SHARES app.py's ARITHMETIC. Every figure here comes from
`traffic_windows`, the same function the admin dashboard's trend panels call.
Writing the windows twice would drift, and the place that surfaces is a number
in a digest disagreeing with the number on screen -- with no way to tell which
is wrong. Same reasoning as the chart twins in CLAUDE.md, applied before the
divergence exists rather than after. This reaches it by exec'ing app.py's
section 1-2 prefix, the pattern analyze_model.py and all five check_*.py guards
already use.

WHAT "A DAY" MEANS. Buckets are calendar days in TRAFFIC_REPORT_TZ
(America/Los_Angeles), named in the header of every run. Not UTC: timestamps
are correct absolute instants, but bucketing them by UTC date files a 20:00
Pacific visit under the FOLLOWING day, which for a US audience misplaces
roughly every evening session.

WHAT IT DOES NOT REPORT. Conversion -- surveys, PDFs, shares -- is
analyze_survey.py's job. Landings answer "did anyone come"; that script answers
"did it matter", and the two questions have different denominators and
different caveats.
"""
import argparse
import ast
import sys
from datetime import datetime, timedelta

import pandas as pd

APP = "app.py"


def load_app_namespace():
    """app.py's sections 1-2, without the UI. See the module docstring."""
    src = open(APP).read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "trafficdigest"}
    exec(compile(prefix, APP, "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), APP, "exec"), ns)
    return ns


def fetch_usage_logs():
    """usage_logs from Supabase, via the same client analyze_survey.py uses."""
    try:
        import analyze_survey
    except Exception as exc:                      # pragma: no cover
        sys.exit(f"Could not import analyze_survey for its Supabase client: {exc}")
    client = analyze_survey.load_supabase_client()
    return analyze_survey.fetch_table(client, "usage_logs")


def pct(now: int, before: int) -> str:
    """Percent change, or a dash when there is no baseline to compare against.

    A jump from zero is not "+infinity%" and not "+100%" -- it is a first
    observation, and saying so is more useful than a number that looks like a
    trend.
    """
    if before == 0:
        return "     --" if now == 0 else "   new "
    return f"{(now - before) / before * 100:+6.0f}%"


def render(w: dict, days: int) -> str:
    """The digest, as text. Pure so it can be tested without a database."""
    pages = ["calculator"] + [k for k in w["daily"].columns
                              if k not in ("calculator", "total")]
    win, out = w["windows"], []
    asof = w["asof"]
    yesterday = asof - timedelta(days=1)

    out.append(f"TRAFFIC DIGEST -- {yesterday:%a %-d %b %Y}")
    out.append(f"days are calendar days in {w['tz']}; run as of {asof}")
    out.append("")

    if not win:
        out.append("No landings recorded yet.")
        return "\n".join(out)

    y, p = win["yesterday"], win["prior_day"]
    out.append(f"  Yesterday       {y['total']:>5} landings   "
               f"({y['total'] - p['total']:+d} vs prior day, {pct(y['total'], p['total'])})")
    for key in pages:
        if key in y:
            out.append(f"    {key:<14}{y[key]:>5}")
    out.append("")

    l7, p7 = win["last7"], win["prior7"]
    out.append(f"  {days}-day total     {l7['total']:>5}   "
               f"(vs {p7['total']} prior {days}d, {pct(l7['total'], p7['total'])})")
    if w["best_day"]:
        d, n = w["best_day"]
        out.append(f"  Best day        {d:%a %-d %b} ({n})")
    out.append("")

    if w["cold"]:
        out.append("  Tool traffic (all time)")
        for c in w["cold"]:
            out.append(f"    {c['Tool']:<32}{c['Landings']:>5}   "
                       f"{c['Internal']} internal / {c['Cold']} cold")
        out.append("")

    if not w["by_src"].empty:
        out.append("  Landings by source (all time)")
        for _, row in w["by_src"].iterrows():
            out.append(f"    {str(row['Source']):<32}{int(row['Landings']):>5}")
        out.append("")

    out.append("  'Cold' is a floor: a visitor who copies a link out of their")
    out.append("  address bar passes ?from= to whoever they send it to, and that")
    out.append("  recipient counts as internal. It can only understate cold.")
    return "\n".join(out)


def load_email_config():
    """The [email] block from .streamlit/secrets.toml, or None if absent.

    Read straight from the file rather than through Streamlit, since this runs
    outside a Streamlit runtime. The app password never leaves this function's
    return value: it is passed to smtplib and is never printed, logged, or
    included in an error message -- an SMTP failure reports the exception type
    and the server's reply, both of which are safe.
    """
    try:
        import tomllib
        with open(".streamlit/secrets.toml", "rb") as fh:
            cfg = tomllib.load(fh).get("email")
    except Exception:
        return None
    if not cfg:
        return None
    missing = [k for k in ("username", "app_password", "to") if not cfg.get(k)]
    if missing:
        print(f"  [email] section is present but missing {missing}; not sending.",
              file=sys.stderr)
        return None
    return cfg


def send_digest_email(body: str, subject: str, cfg: dict) -> None:
    """Send the digest over SMTP+STARTTLS. Raises on failure, deliberately.

    A digest that silently fails to send is worse than one that never existed:
    you would be waiting on a mail that is not coming, and reading no news as
    good news. The caller lets the exception reach the runner, which marks the
    whole run FAILED.
    """
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = cfg["username"]
    msg["To"] = cfg["to"]
    msg["Subject"] = subject
    msg.set_content(body)

    host = cfg.get("smtp_host", "smtp.gmail.com")
    port = int(cfg.get("smtp_port", 587))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(cfg["username"], cfg["app_password"])
        smtp.send_message(msg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7,
                    help="length of the rolling window (default 7)")
    ap.add_argument("--tz", default=None,
                    help="reporting timezone (default TRAFFIC_REPORT_TZ from app.py)")
    ap.add_argument("--asof", default=None,
                    help="run as if today were this YYYY-MM-DD; makes a run "
                         "reproducible and lets a missed day be recovered")
    ap.add_argument("--email", action="store_true",
                    help="also email the digest, using the [email] block in "
                         ".streamlit/secrets.toml. Skips with a note if that "
                         "block is absent; FAILS if it is present and the send "
                         "does not succeed.")
    ap.add_argument("-o", "--output", default=None,
                    help="also write the per-day landings table to this CSV")
    args = ap.parse_args()

    ns = load_app_namespace()
    tz = args.tz or ns["TRAFFIC_REPORT_TZ"]
    asof = (datetime.strptime(args.asof, "%Y-%m-%d").date()
            if args.asof else pd.Timestamp.now(tz=tz).date())

    print(f"Fetching usage_logs ...", file=sys.stderr)
    usage = fetch_usage_logs()
    print(f"  {len(usage):,} rows", file=sys.stderr)

    # days is passed to the arithmetic, not just to the label. It used to go
    # only to render(), which printed "14-day total" over traffic_windows'
    # hardcoded 7-day sums -- a wrong number presented with confidence.
    w = ns["traffic_windows"](usage, tz=tz, asof=asof, days=args.days)
    digest = render(w, args.days)
    print(digest)

    if args.email:
        cfg = load_email_config()
        if cfg is None:
            print("  No [email] block in .streamlit/secrets.toml; not sending.",
                  file=sys.stderr)
        else:
            yesterday = asof - timedelta(days=1)
            subject = (f"StudentLoanROI traffic — {yesterday:%a %-d %b} — "
                       f"{w['windows'].get('yesterday', {}).get('total', 0)} landings")
            send_digest_email(digest, subject, cfg)
            print(f"  Emailed to {cfg['to']}", file=sys.stderr)

    if args.output and not w["daily"].empty:
        w["daily"].to_csv(args.output)
        print(f"\nWrote per-day landings to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
