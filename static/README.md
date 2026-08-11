# Publicly served marketing images

Everything here is served by Streamlit at `/app/static/<file>` (see
`[server] enableStaticServing` in .streamlit/config.toml) and reaches the
public internet at `worthmydegree.com/app/static/<file>` through the worker's
passthrough, edge-cached for an hour.

These are COPIES of `brand/` build output, committed so the deploy carries
them. After regenerating anything in `brand/`, re-copy it here in the same
commit — the two directories have no mechanical sync, only this note.

`feature-og-1200x630.png` is load-bearing beyond marketing: the worker's
`og:image` meta tag points at it, so it is the preview card every social link
to the site shows. Renaming it breaks that tag.
