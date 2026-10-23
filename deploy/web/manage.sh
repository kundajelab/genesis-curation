#!/usr/bin/env bash
# Operate only this Compose project. All persistent state stays below WEB_ROOT.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
: "${WEB_ROOT:?Set WEB_ROOT to a private absolute directory under your Genesis workspace}"
: "${PUBLIC_IP:?Set PUBLIC_IP to the server public IP address}"
export WEB_ROOT PUBLIC_IP
export WEB_ORIGIN="${WEB_ORIGIN:-https://${PUBLIC_IP}:8443}"
export CURATOR_UID="$(id -u)" CURATOR_GID="$(id -g)"
python3 -c 'import ipaddress,sys; ip=ipaddress.ip_address(sys.argv[1]); assert ip.version == 4, "This template supports IPv4"' "$PUBLIC_IP"
[[ "$WEB_ROOT" = /* && "$WEB_ROOT" != / ]] || { echo 'WEB_ROOT must be an absolute private directory' >&2; exit 1; }
compose=(docker compose -f "$here/compose.yaml")
http_config() {
    cat <<'NGINX'
events { worker_connections 1024; }
http {
  access_log off;
  server {
    listen 80;
    server_name _;
    location /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { return 404; }
  }
NGINX
}
case "${1:-status}" in
  prepare)
    umask 077
    mkdir -p "$WEB_ROOT"/{data,config,tmp,certificates,challenges,certbot-work,certbot-logs}
    if [[ ! -f "$WEB_ROOT/nginx.conf" ]]; then
      { http_config; echo '}'; } > "$WEB_ROOT/nginx.conf"
    fi
    chmod 755 "$WEB_ROOT/challenges"
    [[ -f "$WEB_ROOT/config/providers.json" ]] || cp "$here/providers.example.json" "$WEB_ROOT/config/providers.json"
    "${compose[@]}" build app
    ;;
  init)
    "${compose[@]}" run --rm --no-deps app init --root /data
    ;;
  certificate)
    account=(--register-unsafely-without-email)
    if [[ -n "${ACME_EMAIL:-}" ]]; then account=(--email "$ACME_EMAIL"); fi
    "${compose[@]}" up -d nginx
    "${compose[@]}" run --rm --no-deps --entrypoint certbot certbot certonly \
      --non-interactive --agree-tos "${account[@]}" --preferred-profile shortlived \
      --webroot --webroot-path /var/www/acme --ip-address "$PUBLIC_IP" --cert-name genesis-web
    {
      http_config
      cat <<'NGINX'
  server {
    listen 8443 ssl;
    server_name _;
    ssl_certificate /etc/letsencrypt/live/genesis-web/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/genesis-web/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 3m;
    location / {
      proxy_pass http://127.0.0.1:18765;
      proxy_set_header Host $http_host;
      proxy_set_header X-Forwarded-Proto https;
      proxy_set_header X-Forwarded-For $remote_addr;
      proxy_read_timeout 30s;
    }
  }
}
NGINX
    } > "$WEB_ROOT/nginx.conf"
    "${compose[@]}" exec nginx nginx -t
    "${compose[@]}" exec nginx nginx -s reload
    ;;
  http-start)
    export WEB_ORIGIN="http://${PUBLIC_IP}"
    cat > "$WEB_ROOT/nginx.conf" <<'NGINX'
events { worker_connections 1024; }
http {
  access_log off;
  server {
    listen 80;
    server_name _;
    client_max_body_size 3m;
    location / {
      proxy_pass http://127.0.0.1:18765;
      proxy_set_header Host $http_host;
      proxy_set_header X-Forwarded-For $remote_addr;
      proxy_read_timeout 30s;
    }
  }
}
NGINX
    "${compose[@]}" -f "$here/http-test.yaml" up -d app worker nginx
    ;;
  start)
    "${compose[@]}" up -d app worker nginx certbot
    ;;
  renew)
    "${compose[@]}" run --rm --no-deps --entrypoint certbot certbot renew --non-interactive
    "${compose[@]}" exec nginx nginx -t
    "${compose[@]}" exec nginx nginx -s reload
    ;;
  backup)
    : "${BACKUP_DIR:?Set BACKUP_DIR to a new private backup directory}"
    [[ ! -e "$BACKUP_DIR" ]] || { echo 'Backup directory already exists' >&2; exit 1; }
    umask 077
    mkdir -p "$BACKUP_DIR"
    "${compose[@]}" stop app worker
    trap '"${compose[@]}" start app worker' EXIT
    cp -a "$WEB_ROOT/data" "$WEB_ROOT/config" "$BACKUP_DIR/"
    cp "$WEB_ROOT/nginx.conf" "$BACKUP_DIR/"
    ;;
  stop) "${compose[@]}" stop ;;
  status) "${compose[@]}" ps ;;
  *) echo 'Usage: manage.sh prepare|init|certificate|start|http-start|renew|backup|stop|status' >&2; exit 2 ;;
esac
