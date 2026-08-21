#!/bin/sh
# Choose between the HTTP-only and the TLS server config at container start.
#
# nginx refuses to start when an ssl_certificate path does not exist, so the
# TLS block cannot simply live in the default config and lie dormant. Deciding
# here keeps `docker compose up` working out of the box with no certificate,
# and makes turning TLS on a matter of mounting one directory -- no rebuild,
# no second image, no edited config.
#
# Run by nginx's own entrypoint, which sources /docker-entrypoint.d/*.sh in
# filename order before exec'ing nginx.
set -e

CERT_DIR=${TLS_CERT_DIR:-/etc/nginx/certs}
AVAILABLE=/etc/nginx/server-conf
TARGET=/etc/nginx/conf.d/default.conf

if [ -r "$CERT_DIR/fullchain.pem" ] && [ -r "$CERT_DIR/privkey.pem" ]; then
    echo "$0: certificate found in $CERT_DIR -- serving HTTPS on 443, redirecting 80"
    cp "$AVAILABLE/https.conf" "$TARGET"
else
    echo "$0: no certificate in $CERT_DIR -- serving plain HTTP on 80"
    echo "$0: HSTS stays off and the site is not encrypted; see README.md (TLS)"
    cp "$AVAILABLE/http.conf" "$TARGET"
fi
