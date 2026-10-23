#!/bin/sh
set -eu
# Reload changed certificates without giving any container a Docker socket.
(
    previous=''
    while sleep 60; do
        current=$(find /etc/letsencrypt/live -name fullchain.pem -exec sha256sum {} \; 2>/dev/null || true)
        if [ -n "$current" ] && [ "$current" != "$previous" ]; then
            nginx -t && nginx -s reload
            previous=$current
        fi
    done
) &
exec nginx -g 'daemon off;'
