ARG MARIADB_VERSION=12.3
FROM mariadb:${MARIADB_VERSION}
# Tag image, mis. 12.3-a1: dilaporkan agen lewat heartbeat. Bukan MARIADB_VERSION: nilai itu ditimpa ENV image dasar.
ARG IMAGE_TAG=dev
RUN apt update && apt -y upgrade \
    && apt-get install -y tzdata cron curl nano iputils-ping traceroute python3 sudo age \
    && ln -snf /usr/share/zoneinfo/Asia/Jakarta /etc/localtime && echo "Asia/Jakarta" >/etc/timezone && dpkg-reconfigure -f noninteractive tzdata \
    && mkdir -p /backup/databases /backup/fullbackup /backup/binlog /backup/log && chmod 755 /backup/databases /backup/fullbackup /backup/binlog /backup/log \
    && echo "[mysqld]\nevent_scheduler=ON" >/etc/mysql/conf.d/custom_scheduler.cnf \
    && useradd --uid 9971 --no-create-home --shell /usr/sbin/nologin lumbung \
    && useradd --uid 9972 --no-create-home --shell /usr/sbin/nologin lumbung_tuning \
    &&  apt-get clean && rm -rf /var/lib/apt/lists/*
COPY --chmod=644 scripts/binlog.cnf /etc/mysql/conf.d/binlog.cnf
COPY --chmod=644 scripts/init-backup-user.sh /docker-entrypoint-initdb.d/
COPY --chmod=755 scripts/backup/ /opt/backup/
COPY --chmod=755 scripts/tuning.sh /opt/tuning.sh
# Agen dan helper milik root. Agen berjalan sebagai lumbung; satu-satunya jalur ke root adalah helper.
# Folder dibuat dulu: COPY --chmod ikut menerapkan 644 ke folder yang dibuatnya, sehingga agen tidak bisa masuk.
RUN install -d -m 755 /opt/lumbung
COPY --chmod=644 agent/agent.py agent/schedule.py agent/metrics.py agent/tuning.py scripts/tuning-account.sql /opt/lumbung/
COPY --chmod=755 agent/lumbung-helper /usr/local/sbin/lumbung-helper
COPY --chmod=755 agent/lumbung-agent /usr/local/bin/lumbung-agent
COPY --chmod=440 agent/lumbung.sudoers /etc/sudoers.d/lumbung
RUN echo "$IMAGE_TAG" >/opt/lumbung/VERSION
RUN rm /etc/ssl/openssl.cnf
COPY scripts/openssl.cnf /etc/ssl/openssl.cnf
COPY scripts/docker-entrypoint.sh /usr/local/bin/custom-entrypoint.sh
RUN chmod +x /usr/local/bin/custom-entrypoint.sh
VOLUME /backup
ENTRYPOINT ["custom-entrypoint.sh"]
CMD ["mariadbd"]
