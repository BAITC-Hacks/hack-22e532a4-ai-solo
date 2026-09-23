#!/bin/sh
# Only act for this project's certificate; leave other lineages alone.
set -eu
if [ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/baqbaq.world ]; then
    /usr/sbin/nginx -t
    /bin/systemctl reload nginx
fi
