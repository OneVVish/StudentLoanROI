#!/bin/sh
# Container entrypoint: materialize Streamlit secrets from the environment,
# then run the app on the host-provided port.
#
# st.secrets only reads .streamlit/secrets.toml (not environment variables),
# so the deploy platform stores the file's CONTENTS in one env var --
# STREAMLIT_SECRETS_TOML, pasted verbatim from the local secrets.toml --
# and this script writes it out at boot. The image never contains secrets;
# rotating them is editing one variable and restarting.
set -eu

if [ -n "${STREAMLIT_SECRETS_TOML:-}" ]; then
    mkdir -p .streamlit
    printf '%s' "$STREAMLIT_SECRETS_TOML" > .streamlit/secrets.toml
    echo "start.sh: wrote .streamlit/secrets.toml from STREAMLIT_SECRETS_TOML"
else
    echo "start.sh: WARNING: STREAMLIT_SECRETS_TOML is not set -- Supabase logging" \
         "and the College Scorecard API will be unavailable" >&2
fi

# Config flags are passed HERE as well as living in .streamlit/config.toml,
# because .dockerignore excludes the whole .streamlit/ directory (so the image
# never carries a stale secrets file) -- which also means config.toml never
# reaches the container. Community Cloud ignores this script and reads the
# toml; the container ignores the toml and reads these flags. Keep the two in
# step or the deploys diverge silently.
exec streamlit run app.py \
    --server.port "${PORT:-8501}" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.enableStaticServing true \
    --client.toolbarMode minimal \
    --client.showErrorDetails type \
    --browser.gatherUsageStats false
