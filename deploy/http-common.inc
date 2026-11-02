# Rate limit login di proxy (lapis pertama); tabel login_failures di app adalah lapis kedua.
map $request_method $login_limit_key {
    POST    $binary_remote_addr;
    default "";
}
limit_req_zone $login_limit_key zone=login:10m rate=30r/m;

# URL dari static_url() membawa ?v=<hash isi file>, jadi aman di-cache selamanya.
map $arg_v $static_expires {
    ""      1h;
    default max;
}

upstream app {
    server web:8000;
    keepalive 32;
    # WAJIB < --timeout-keep-alive uvicorn (75 s di Dockerfile), kalau tidak POST sesekali gagal 502.
    keepalive_timeout 60s;
}

# Pencarian riwayat file menyentuh tabel backups yang besar.
limit_req_zone $binary_remote_addr zone=search:10m rate=120r/m;
