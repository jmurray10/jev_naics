# Build context is the repo root, so COPY frontend/... resolves:
#   docker build -f frontend/Dockerfile -t jev-naics-web .
# One variable configures the image per environment (JEV-56, DEPLOY.md): NAICS_API_ORIGIN, the API's
# origin. At start it is written to js/config.js and rendered into the CSP connect-src.
FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/templates/default.conf.template
COPY frontend/docker/15-naics-api-origin.sh /docker-entrypoint.d/15-naics-api-origin.sh
RUN sed -i 's/\r$//' /docker-entrypoint.d/15-naics-api-origin.sh \
    && chmod 0755 /docker-entrypoint.d/15-naics-api-origin.sh
COPY frontend/index.html /usr/share/nginx/html/index.html
COPY frontend/benchmark.html /usr/share/nginx/html/benchmark.html
COPY frontend/css /usr/share/nginx/html/css
COPY frontend/js /usr/share/nginx/html/js
# Local default: the Compose API. Production sets the deployed API's origin.
ENV NAICS_API_ORIGIN=http://127.0.0.1:8095
