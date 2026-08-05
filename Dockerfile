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

COPY . .

# Secrets are injected at runtime by start.sh from the STREAMLIT_SECRETS_TOML
# environment variable -- .dockerignore keeps any local .streamlit/secrets.toml
# out of the image, so the image itself contains no credentials.
RUN chmod +x start.sh

EXPOSE 8501
CMD ["./start.sh"]
