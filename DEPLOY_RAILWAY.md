# Deploying to Railway

The repo carries everything Railway needs: `Dockerfile` (Python 3.13 +
the exact pins from requirements.txt), `start.sh` (writes Streamlit secrets
from an env var at boot), `.dockerignore` (keeps secrets and local research
files out of the image), and `railway.json` (health check + restart policy).
Streamlit Community Cloud ignores all four, so the legacy
studentloanroi.streamlit.app keeps working unchanged.

## One-time setup (~10 minutes, all in the browser)

1. **Create the project.** railway.com → New Project → Deploy from GitHub
   repo → `OneVVish/StudentLoanROI`, branch `main`. Railway detects the
   Dockerfile via railway.json and builds. First build takes a few minutes.

2. **Set the secrets variable.** In the service → Variables → New Variable:
   - Name: `STREAMLIT_SECRETS_TOML`
   - Value: paste the ENTIRE contents of your local
     `.streamlit/secrets.toml`, verbatim (multi-line is fine).
   Redeploy when prompted. Without this the app runs but Supabase logging
   and school lookup are dead — start.sh warns in the logs.

3. **Generate the URL.** Settings → Networking → Generate Domain. You get
   `something.up.railway.app`. Verify:
   - open `https://<domain>/?test=1` → calculator renders, test-mode banner shows
   - `/?tool=repayment&test=1` → repayment tool renders
   - a share link round-trips (build one, open it in a private window)
   - `/_stcore/health` returns `ok`

4. **Custom domain (recommended).** Settings → Networking → Custom Domain →
   e.g. `app.loancal.info`. Add the CNAME Railway shows at your DNS
   provider. All FUTURE images/posts/emails should use this domain; the old
   streamlit.app URL stays alive for everything already printed.

5. **Right-size it.** Service → Settings → set limits around 2 vCPU / 2 GB
   to start. Usage-based billing lands ~$10–15/month at this size; a Reddit
   spike costs cents more, not an outage.

## Operating notes

- **Deploys track `main`**: every merged PR redeploys Railway AND Community
  Cloud together — one workflow, two hosts, no drift.
- **Rotating secrets** = edit the variable, redeploy. The image never
  contains credentials (.dockerignore excludes `.streamlit/`).
- **Both hosts write to the SAME production Supabase.** `?test=1` discipline
  applies on the Railway URL exactly as it always has locally.
- **Traffic attribution across hosts**: `src` tags work identically. If you
  ever want host-level split, give future materials the custom domain and
  the digest's per-src counts already tell you which links (old vs new) drove
  each visit.
- **Rollback**: Railway keeps previous deploys — one click to restore, or
  `git revert` on main which rolls back both hosts.
