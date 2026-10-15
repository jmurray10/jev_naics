#!/bin/sh
# Runs at container start (nginx's /docker-entrypoint.d, before the conf template is rendered).
# NAICS_API_ORIGIN is the API's origin, scheme://host[:port], no path (JEV-56). It is written to
# js/config.js (where the page calls) and, by nginx's template step, into the CSP connect-src
# (what the browser allows), so the two always match. Anything else stops the container.
set -eu
origin="${NAICS_API_ORIGIN:-}"
# grep -E matches line by line even under -q, so a value whose first line alone looks like a valid
# origin would pass the check below no matter what the rest of it said. Reject any newline or
# carriage return first, so the regex that follows always sees (and must match) the whole value.
if [ "$(printf '%s' "$origin" | tr -d '\n\r')" != "$origin" ]; then
  echo "NAICS_API_ORIGIN must not contain a newline or carriage return" >&2
  exit 1
fi
if ! printf '%s' "$origin" | grep -Eq '^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'; then
  echo "NAICS_API_ORIGIN must be scheme://host[:port] with no path, e.g. https://naics.example.com" >&2
  exit 1
fi
# Optional same-origin proxy (JEV-59): with NAICS_API_UPSTREAM (the API as the web container reaches it,
# scheme://host[:port]) and NAICS_API_KEY (one of the API's service keys), nginx forwards /v1/, /docs and
# /openapi.json to the API and adds the key itself, so the key never reaches the browser. Both values are
# written into nginx configuration, so each must match a strict pattern or the container stops.
upstream="${NAICS_API_UPSTREAM:-}"
key="${NAICS_API_KEY:-}"
snippets="${NAICS_NGINX_SNIPPETS:-/etc/nginx/naics}"
if [ -n "$upstream" ]; then
  if [ "$(printf '%s' "$upstream$key" | tr -d '\n\r')" != "$upstream$key" ]; then
    echo "NAICS_API_UPSTREAM and NAICS_API_KEY must not contain a newline or carriage return" >&2
    exit 1
  fi
  if ! printf '%s' "$upstream" | grep -Eq '^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'; then
    echo "NAICS_API_UPSTREAM must be scheme://host[:port] with no path, e.g. http://api:8095" >&2
    exit 1
  fi
  if ! printf '%s' "$key" | grep -Eq '^[A-Za-z0-9_-]{32,}$'; then
    echo "NAICS_API_KEY must be one service key: at least 32 characters of A-Z a-z 0-9 _ - (SECURITY.md)" >&2
    exit 1
  fi
fi
root="${NAICS_WEB_ROOT:-/usr/share/nginx/html}"
printf '%s\n%s\n' \
  "// Written at container start from NAICS_API_ORIGIN (frontend/docker/15-naics-api-origin.sh)." \
  "window.JEV_NAICS_API = \"$origin\";" > "$root/js/config.js"
mkdir -p "$snippets"
rm -f "$snippets/api-proxy.conf"
if [ -n "$upstream" ]; then
  {
    for loc in "location /v1/" "location = /docs" "location = /openapi.json"; do
      printf '%s {\n  proxy_pass %s;\n  proxy_set_header Authorization "Bearer %s";\n  proxy_set_header X-Request-ID $request_id;\n}\n' \
        "$loc" "$upstream" "$key"
    done
  } > "$snippets/api-proxy.conf"
fi
