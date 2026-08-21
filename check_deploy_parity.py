#!/usr/bin/env python3
"""Guard: the two live deployments are configured the same way.

    python3 check_deploy_parity.py        (exit 1 on a violation)

THE APP RUNS ON TWO HOSTS AND THEY READ DIFFERENT FILES.

  - Railway serves worthmydegree.com from the Dockerfile. `.dockerignore`
    excludes `.streamlit/`, so config.toml NEVER REACHES THE CONTAINER and
    every setting has to be passed as a CLI flag by start.sh.
  - Streamlit Community Cloud serves the legacy studentloanroi.streamlit.app.
    It ignores start.sh entirely and reads .streamlit/config.toml.

start.sh says so itself: "Keep the two in step or the deploys diverge
silently." Nothing enforced it until this file, and it had already drifted.

WHY THIS EXISTS, precisely. On 2026-08-21 worthmydegree.com went down with a
403. Part of the attempted fix added enableCORS/enableXsrfProtection to
config.toml, it was merged, the app was rebooted, and it changed NOTHING --
because the live host does not read that file. The change was then recorded as
"had no effect", which sent the diagnosis somewhere else entirely. A setting
edited in the wrong file looks exactly like a setting that did not work.

THE RULE IS ONE-DIRECTIONAL, and the direction matters:

  Every setting in config.toml MUST appear in start.sh with the same value.

The reverse is deliberately not required. start.sh legitimately carries flags
that have no business in config.toml -- the port and bind address come from the
host, `headless` is meaningless on Community Cloud, and gatherUsageStats is a
container concern. Those live in CONTAINER_ONLY below. Anything else appearing
in start.sh and not in config.toml is still reported, because it means the
legacy host is running without a setting the container has.

WHAT THIS CANNOT CHECK: whether either host is actually running the committed
code. Railway redeploys on a merge to main and Community Cloud on its own
schedule, so a green check here says the two files agree, not that the two
servers do.
"""
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / ".streamlit" / "config.toml"
START = ROOT / "start.sh"
DOCKERIGNORE = ROOT / ".dockerignore"

# Flags that belong to the container and have no config.toml counterpart.
# Each is here because the OTHER host either sets it itself or cannot use it.
CONTAINER_ONLY = {
    "server.port": "the host assigns it via $PORT",
    "server.address": "bind address is a container concern",
    "server.headless": "Community Cloud is already headless",
    "browser.gatherUsageStats": "set once per platform, not per app",
}


def parse_start_flags() -> dict:
    """The --section.key value pairs start.sh passes to `streamlit run`."""
    text = START.read_text()
    flags = {}
    # --server.enableStaticServing true   /   --client.toolbarMode minimal
    for key, value in re.findall(r'--([a-zA-Z]+\.[a-zA-Z]+)\s+"?([^"\s\\]+)"?', text):
        flags[key] = value.strip('"')
    return flags


def parse_config_settings() -> dict:
    """The section.key values in .streamlit/config.toml, flattened."""
    data = tomllib.loads(CONFIG.read_text())
    out = {}
    for section, body in data.items():
        if isinstance(body, dict):
            for key, value in body.items():
                out[f"{section}.{key}"] = value
    return out


def normalize(value) -> str:
    """One spelling for a value however the two files write it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().strip('"').lower()


def main() -> int:
    problems = []

    if not CONFIG.exists():
        problems.append("  .streamlit/config.toml is missing.")
    if not START.exists():
        problems.append("  start.sh is missing, so the container has no config"
                        " at all.")
    if problems:
        print("deploy parity: cannot check\n")
        print("\n".join(problems))
        return 1

    config = parse_config_settings()
    flags = parse_start_flags()

    # THE LOAD-BEARING DIRECTION. A setting here and not there is a setting the
    # legacy host honours and the LIVE host silently ignores.
    for key, value in sorted(config.items()):
        if key not in flags:
            problems.append(
                f"  {key} is set in config.toml and NOT passed by start.sh, so "
                f"Community Cloud honours it and the Railway container -- which "
                f"is what worthmydegree.com serves -- does not. Add "
                f"`--{key} {normalize(value)}` to start.sh. This is the exact "
                f"shape of the 2026-08-21 enableCORS mistake.")
        elif normalize(flags[key]) != normalize(value):
            problems.append(
                f"  {key} disagrees: config.toml says {normalize(value)!r}, "
                f"start.sh passes {normalize(flags[key])!r}. The two hosts are "
                f"running different apps.")

    for key in sorted(flags):
        if key in config or key in CONTAINER_ONLY:
            continue
        problems.append(
            f"  {key} is passed by start.sh and absent from config.toml, so the "
            f"legacy host runs without it. Either add it to config.toml or, if "
            f"it is genuinely container-only, list it in CONTAINER_ONLY with a "
            f"reason.")

    # The premise the whole guard rests on. If .streamlit/ ever stops being
    # excluded, config.toml reaches the container and start.sh's flags become
    # the redundant half instead -- worth knowing before debugging either.
    # COMMENTS STRIPPED FIRST. Checking for the bare string matched a
    # commented-out "#.streamlit/" too, so the negative control that disables
    # the exclusion passed -- the guard read its own documentation as
    # compliance, the flaw this repo already records against check_share_
    # coverage's loan-amount search.
    ignored = [ln.strip() for ln in DOCKERIGNORE.read_text().splitlines()
               if ln.strip() and not ln.strip().startswith("#")] \
        if DOCKERIGNORE.exists() else []
    if DOCKERIGNORE.exists() and ".streamlit/" not in ignored:
        problems.append(
            "  .dockerignore no longer excludes .streamlit/, so the container "
            "may now read config.toml directly. That is not necessarily wrong, "
            "but start.sh's flags and this guard both assume the opposite.")

    if problems:
        print(f"deploy parity: {len(problems)} problem(s)\n")
        for p in problems:
            print(p + "\n")
        print("  Two hosts, two config files, one app. Railway reads start.sh's\n"
              "  flags because .dockerignore keeps config.toml out of the image;\n"
              "  Community Cloud reads config.toml and never runs start.sh.")
        return 1

    shared = sorted(set(config) & set(flags))
    print(f"deploy parity OK: {len(shared)} shared setting(s) agree "
          f"({', '.join(shared)}), "
          f"{len(CONTAINER_ONLY)} container-only flag(s) exempt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
