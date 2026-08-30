# Railway (or any Docker host) image for the app.
# Streamlit Community Cloud IGNORES this file -- it keeps building from
# requirements.txt + runtime.txt as before, so the legacy
# studentloanroi.streamlit.app deployment is unaffected.
#
# Python is pinned to 3.13 to match runtime.txt: production drifted onto
# 3.14 once and segfaulted on every page load (see requirements.txt).
FROM python:3.13-slim

WORKDIR /app

# Layer-cache the dependency install; requirements are exact pins.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run as an unprivileged user. The process handles untrusted input (query
# params, a free-text survey, PDF generation from visitor strings) and start.sh
# writes the secrets file into the working directory at boot, so a bug in any
# dependency would otherwise execute as root with every credential readable.
# The user owns /app, which is all it needs: the secrets file lands there,
# Streamlit's ~/.streamlit and matplotlib's cache resolve under HOME=/app, and
# $PORT on Railway is unprivileged. Created after the pip install, which still
# runs as root.
RUN useradd --system --uid 1000 --home-dir /app --shell /usr/sbin/nologin app
ENV HOME=/app

COPY --chown=app:app . .

# Secrets are injected at runtime by start.sh from the STREAMLIT_SECRETS_TOML
# environment variable -- .dockerignore keeps any local .streamlit/secrets.toml
# out of the image, so the image itself contains no credentials.
RUN chmod +x start.sh

USER app

EXPOSE 8501
CMD ["./start.sh"]
