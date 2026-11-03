    server_tokens off;
    client_max_body_size 10m;

    proxy_http_version 1.1;
    proxy_set_header Connection "";
    # Ada proxy lain di depan nginx? Aktifkan real_ip agar $remote_addr = IP asli user.
    # set_real_ip_from <IP proxy>;
    # real_ip_header X-Forwarded-For;
    # real_ip_recursive on;

    # $http_host, bukan $host: membawa port, karena pemeriksaan Origin membandingkan alamat lengkap yang dilihat browser.
    proxy_set_header Host $http_host;
    # TIMPA, jangan tambah: X-Forwarded-For kiriman client tidak boleh sampai ke app (rate limit login).
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    # Request ID dibuat nginx, bukan diteruskan dari client.
    proxy_set_header X-Request-ID $request_id;
    proxy_read_timeout 30s;

    location /static/ {
        alias /srv/static/;
        expires $static_expires;
        access_log off;
    }

    # Health check bukan untuk publik: /readyz membuka koneksi DB baru tiap panggilan.
    # Pemeriksa di luar VM memakai /api/watchdog.
    location ~ ^/(healthz|readyz)$ {
        allow 127.0.0.1;
        deny all;
        proxy_pass http://app;
    }

    location = /login {
        limit_req zone=login burst=20 nodelay;
        limit_req_status 429;
        proxy_pass http://app;
    }

    # File backup bisa berukuran puluhan GB: tanpa batas ukuran, tanpa ditampung dulu di disk proxy.
    location /api/upload/ {
        client_max_body_size 0;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_pass http://app;
    }

    # Unduhan runner: file bisa puluhan GB, jadi tidak ditampung di disk proxy.
    location ~ ^/api/runner/jobs/[0-9]+/files/ {
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_pass http://app;
    }

    location ~ ^/containers/[^/]+/backup$ {
        limit_req zone=search burst=30 nodelay;
        limit_req_status 429;
        proxy_pass http://app;
    }

    location / {
        proxy_pass http://app;
    }
