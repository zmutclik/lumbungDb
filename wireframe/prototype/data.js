/* Data contoh LumbungDB. "Sekarang" = Jumat 30 Okt 2026 08:15 WIB. */
window.LDB = {
  servers: [
    {
      name: 'lab-db', host: 'db-klinis.rs.local', ip: '10.10.2.14', runner: 'runner-b', ver: '11.4.5', standby: { full: '2026-10-22_0200', until: '23 Okt 02:05', port: 33071 }, sched: { test: { on: true, day: 'Minggu', at: '03:30', full: true, dbs: null }, standby: { on: true, day: 'Harian', at: '01:00', binlog: '1 jam' } }, reg: '3 Sep 2026',
      status: 'active', dbs: ['lab_hasil'],
      dumps: [['lab_hasil', '5 jam lalu', 'ok', ''], ['_users', '5 jam lalu', 'ok', '']],
      full: '54 jam lalu', dump: '5 jam lalu', hb: '1 menit lalu', disk: 71,
      restore: { ok: false, date: '27 Okt' }, files: { full: 12, dump: 186, binlog: 940 },
      binlogGap: { no: '000790', day: '23 Okt', from: '22 Okt' },
      upload: { pending: 0, lastOk: '12 menit lalu', err: null },
      recent: [
        ['full', '2026-10-28_0200.xbstream.gz', '18,4 GB', '54 jam lalu'],
        ['dump', 'lab_hasil/lab_hasil-2026-10-30_0300.sql.gz', '412 MB', '5 jam lalu'],
        ['dump', '_users/_users-2026-10-30_0300.sql.gz', '48 KB', '5 jam lalu'],
        ['binlog', 'mysql-bin.000812.gz', '96 MB', '12 menit lalu']
      ],
      log: '2026-10-30 02:00:01 fullbackup start\n2026-10-30 02:00:03 mariadb-backup: Connecting to server\n!2026-10-30 02:41:18 [ERROR] xtrabackup: Error writing file\n!2026-10-30 02:41:18   \'/backup/fullbackup/tmp\' (Errcode: 28 No space left)\n!2026-10-30 02:41:19 fullbackup FAILED\n2026-10-30 03:00:01 databasebackup start\n2026-10-30 03:04:52 dump lab_hasil ok (412 MB)\n2026-10-30 03:05:10 dump _users ok\n2026-10-30 03:05:10 databasebackup done'
    },
    {
      name: 'farmasi-db', host: 'db-klinis.rs.local', ip: '10.10.2.14', runner: 'runner-b', ver: '11.4.5', standby: null, sched: { test: { on: true, day: 'Senin', at: '03:30', full: false, dbs: ['farmasi'] }, standby: { on: false, day: 'Harian', at: '01:00', binlog: '1 jam' } }, reg: '3 Sep 2026',
      status: 'active', dbs: ['farmasi', 'farmasi_stok'],
      dumps: [['farmasi', '8 jam lalu', 'ok', ''], ['farmasi_stok', '8 jam lalu', 'ok', ''], ['_users', '8 jam lalu', 'ok', '']],
      full: '8 jam lalu', dump: '8 jam lalu', hb: '14 menit lalu', disk: 55,
      restore: { ok: true, date: '26 Okt' }, files: { full: 30, dump: 92, binlog: 611 },
      upload: { pending: 0, lastOk: '21 menit lalu', err: null },
      recent: [
        ['full', '2026-10-30_0000.xbstream.gz', '7,9 GB', '8 jam lalu'],
        ['dump', 'farmasi/farmasi-2026-10-30_0000.sql.gz', '1,2 GB', '8 jam lalu'],
        ['dump', 'farmasi_stok/farmasi_stok-2026-10-30_0000.sql.gz', '220 MB', '8 jam lalu'],
        ['binlog', 'mysql-bin.000533.gz', '41 MB', '21 menit lalu']
      ],
      log: '2026-10-30 00:00:01 fullbackup start\n2026-10-30 00:21:40 fullbackup done (7,9 GB)\n2026-10-30 00:22:00 databasebackup start\n2026-10-30 00:31:12 dump farmasi ok (1,2 GB)\n2026-10-30 00:33:05 dump farmasi_stok ok (220 MB)\n2026-10-30 00:33:06 dump _users ok\n2026-10-30 00:33:06 databasebackup done'
    },
    {
      name: 'simrs-utama', host: 'db-utama.rs.local', ip: '10.10.1.10', runner: 'runner-b', runnerCad: 'runner-a', ver: '11.4.5', standby: { full: '2026-10-30_0200', until: '30 Okt 08:05', port: 33061 }, sched: { test: { on: true, day: 'Minggu', at: '03:30', full: true, dbs: null }, standby: { on: true, day: 'Harian', at: '03:00', binlog: '1 jam' } }, reg: '12 Mar 2026',
      status: 'active', dbs: ['simrs'],
      dumps: [['simrs', '5 jam lalu', 'ok', ''], ['_users', '5 jam lalu', 'ok', '']],
      full: '6 jam lalu', dump: '5 jam lalu', hb: '1 menit lalu', disk: 62,
      restore: { ok: true, date: '29 Okt' }, files: { full: 30, dump: 60, binlog: 2104 },
      upload: { pending: 0, lastOk: '10 menit lalu', err: null },
      recent: [
        ['full', '2026-10-30_0200.xbstream.gz', '52,7 GB', '6 jam lalu'],
        ['dump', 'simrs/simrs-2026-10-30_0230.sql.gz', '9,9 GB', '5 jam lalu'],
        ['dump', '_users/_users-2026-10-30_0230.sql.gz', '61 KB', '5 jam lalu'],
        ['binlog', 'mysql-bin.002104.gz', '104 MB', '10 menit lalu']
      ],
      log: '2026-10-30 02:00:01 fullbackup start\n2026-10-30 02:29:51 fullbackup done (52,7 GB)\n2026-10-30 02:30:00 databasebackup start\n2026-10-30 02:58:14 dump simrs ok (9,9 GB)\n2026-10-30 02:58:15 dump _users ok\n2026-10-30 02:58:15 databasebackup done'
    },
    {
      name: 'siak-db', host: 'db-utama.rs.local', ip: '10.10.1.10', runner: 'runner-a', ver: '11.4.5', standby: null, sched: { test: { on: true, day: 'Rabu', at: '03:30', full: true, dbs: null }, standby: { on: false, day: 'Harian', at: '01:00', binlog: '1 jam' } }, reg: '12 Mar 2026',
      status: 'active', dbs: ['siakdb'],
      dumps: [['siakdb', '7 jam lalu', 'ok', ''], ['_users', '31 jam lalu', 'warn', 'dump 30 Okt gagal terkirim · alert pada 36 jam']],
      full: '7 jam lalu', dump: '7 jam lalu', hb: '40 detik lalu', disk: 48,
      restore: { ok: true, date: '28 Okt' }, files: { full: 30, dump: 60, binlog: 331 },
      upload: { pending: 1, lastOk: '6 menit lalu', err: { code: 'HTTP 422', msg: 'file kosong', file: 'databases/_users/_users-2026-10-30_0100.sql.gz', since: '01:14', tries: 421 } },
      recent: [
        ['full', '2026-10-30_0100.xbstream.gz', '14,2 GB', '7 jam lalu'],
        ['dump', 'siakdb/siakdb-2026-10-30_0100.sql.gz', '3,1 GB', '7 jam lalu'],
        ['binlog', 'mysql-bin.000331.gz', '22 MB', '6 menit lalu']
      ],
      log: '2026-10-30 01:00:01 fullbackup start\n2026-10-30 01:09:30 fullbackup done (14,2 GB)\n2026-10-30 01:09:31 databasebackup start\n2026-10-30 01:12:02 dump siakdb ok (3,1 GB)\n!2026-10-30 01:12:02 dump _users gagal: file 0 byte\n2026-10-30 01:12:02 databasebackup done'
    },
    {
      name: 'kepegawaian-db', host: 'db-umum.rs.local', ip: '10.10.3.5', runner: 'runner-a', ver: '11.4.5', standby: null, sched: { test: { on: false, day: 'Minggu', at: '03:30', full: true, dbs: null }, standby: { on: false, day: 'Harian', at: '01:00', binlog: '1 jam' } }, reg: '29 Okt 2026',
      status: 'active', dbs: ['kepegawaian'],
      dumps: [['kepegawaian', '9 jam lalu', 'ok', ''], ['_users', '9 jam lalu', 'ok', '']],
      full: '9 jam lalu', dump: '9 jam lalu', hb: '2 menit lalu', disk: 30,
      restore: { ok: true, date: '30 Okt' }, files: { full: 2, dump: 4, binlog: 37 },
      upload: { pending: 0, lastOk: '30 menit lalu', err: null },
      recent: [
        ['full', '2026-10-29_2300.xbstream.gz', '2,3 GB', '9 jam lalu'],
        ['dump', 'kepegawaian/kepegawaian-2026-10-29_2330.sql.gz', '640 MB', '9 jam lalu'],
        ['binlog', 'mysql-bin.000037.gz', '8 MB', '30 menit lalu']
      ],
      log: '2026-10-29 23:00:01 fullbackup start\n2026-10-29 23:04:12 fullbackup done (2,3 GB)\n2026-10-29 23:30:01 databasebackup start\n2026-10-29 23:31:40 dump kepegawaian ok (640 MB)\n2026-10-29 23:31:40 dump _users ok\n2026-10-29 23:31:40 databasebackup done'
    },
    {
      name: 'keuangan-db', host: 'db-umum.rs.local', ip: '10.10.3.5', runner: 'runner-a', ver: '11.4.5', standby: null, sched: { test: { on: false, day: 'Minggu', at: '03:30', full: true, dbs: null }, standby: { on: false, day: 'Harian', at: '01:00', binlog: '1 jam' } }, reg: '12 Jun 2026',
      status: 'off', offSince: '21 Okt 2026', dbs: ['keuangan'],
      dumps: [['keuangan', '9 hari lalu', 'off', ''], ['_users', '9 hari lalu', 'off', '']],
      full: '9 hari lalu', dump: '9 hari lalu', hb: '21 Okt', disk: null,
      restore: null, files: { full: 26, dump: 26, binlog: 410 },
      recent: [], log: ''
    }
  ],

  /* Agen di dalam image zmutclik/mariadb:lumbungdb-* dan jadwal backup yang dilaporkan lewat heartbeat.
     sched.src: bawaan | env | lumbung. */
  agentInfo: {
    'lab-db': { tag: '11.4-a1', fullDur: '41 mnt', sched: { src: 'env', full: '0 2 * * *', dump: '0 3 * * *', binlog: '*/15 * * * *', ver: 0, applied: 0 } },
    'farmasi-db': { tag: '11.4-a1', fullDur: '21 mnt', sched: { src: 'lumbung', full: '0 0 * * *', dump: '22 0 * * *', binlog: '*/15 * * * *', ver: 2, applied: 2 } },
    'simrs-utama': { tag: '11.4-a1', fullDur: '30 mnt', sched: { src: 'lumbung', full: '0 2 * * *', dump: '30 2 * * *', binlog: '*/15 * * * *', ver: 3, applied: 3 } },
    'siak-db': { tag: '11.4-a1', fullDur: '9 mnt', sched: { src: 'lumbung', full: '0 1 * * *', dump: '10 1 * * *', binlog: '*/15 * * * *', ver: 1, applied: 1 } },
    'kepegawaian-db': { tag: '11.4-a1', fullDur: '4 mnt', sched: { src: 'lumbung', full: '0 23 * * *', dump: '30 23 * * *', binlog: '*/15 * * * *', ver: 1, applied: 1 } },
    'keuangan-db': { tag: '11.4-a1', fullDur: '—', sched: { src: 'env', full: '0 3 * * *', dump: '0 4 * * *', binlog: '*/15 * * * *', ver: 0, applied: 0 } }
  },

  /* Permintaan backup di luar jadwal, terbaru dulu. */
  backupRequests: [
    { id: 5, server: 'farmasi-db', kind: 'dump', db: 'farmasi_stok', at: '30 Okt 08:05', by: 'admin', status: 'Menunggu', note: 'Kedaluwarsa 08:20 bila agen tidak mengambilnya' },
    { id: 4, server: 'lab-db', kind: 'full', at: '30 Okt 07:40', by: 'admin', status: 'Gagal', dur: '12 mnt', note: 'Errcode 28: ruang /backup habis' },
    { id: 3, server: 'simrs-utama', kind: 'dump', db: 'simrs', at: '29 Okt 16:10', by: 'admin', status: 'Selesai', dur: '32 mnt', note: 'simrs/simrs-2026-10-29_1610.sql.gz · 9,9 GB' },
    { id: 2, server: 'farmasi-db', kind: 'full', at: '23 Okt 13:22', by: 'admin', status: 'Kedaluwarsa', note: 'Agen tidak mengambilnya dalam 15 menit' },
    { id: 1, server: 'siak-db', kind: 'binlog', at: '21 Okt 11:02', by: 'admin', status: 'Sudah berjalan', note: 'Arsip binlog dari cron sedang berjalan' }
  ],

  /* Riwayat perubahan jadwal dan permintaan backup (control_log). */
  controlLog: [
    { server: 'simrs-utama', at: '29 Okt 16:10', act: 'Minta backup', detail: 'Dump simrs', by: 'admin' },
    { server: 'simrs-utama', at: '12 Okt 10:20', act: 'Ubah jadwal', detail: 'Full: Tiap hari 00:37 → Tiap hari 02:00 · Dump: Tiap hari 04:12 → Tiap hari 02:30', by: 'admin' },
    { server: 'farmasi-db', at: '30 Okt 08:05', act: 'Minta backup', detail: 'Dump farmasi_stok', by: 'admin' },
    { server: 'farmasi-db', at: '3 Sep 10:12', act: 'Ubah jadwal', detail: 'Full: Tiap hari 01:43 → Tiap hari 00:00 · Dump: Tiap hari 04:18 → Tiap hari 00:22', by: 'admin' },
    { server: 'lab-db', at: '30 Okt 07:40', act: 'Minta backup', detail: 'Full', by: 'admin' },
    { server: 'kepegawaian-db', at: '29 Okt 15:30', act: 'Ubah jadwal', detail: 'Full: Tiap hari 00:27 → Tiap hari 23:00 · Dump: Tiap hari 03:44 → Tiap hari 23:30', by: 'admin' },
    { server: 'siak-db', at: '12 Mar 11:40', act: 'Ubah jadwal', detail: 'Dump: Tiap hari 03:51 → Tiap hari 01:10', by: 'admin' }
  ],

  /* Riwayat restore database standby: full terbaru + penerapan binlog, terbaru dulu. */
  standby: [
    { server: 'simrs-utama', runner: 'runner-a', when: '30 Okt 03:00', full: '2026-10-30_0200', until: '30 Okt 02:05', dur: '1 jam 12 mnt', ok: true },
    { server: 'simrs-utama', runner: 'runner-a', when: '29 Okt 03:00', full: '2026-10-29_0200', until: '29 Okt 02:05', dur: '1 jam 10 mnt', ok: true },
    { server: 'lab-db', runner: 'runner-b', when: '30 Okt 01:00', full: '2026-10-28_0200', until: '—', dur: '—', ok: false, skip: true, reason: 'Ruang scratch runner-b kurang saat mengekstrak full' },
    { server: 'lab-db', runner: 'runner-b', when: '23 Okt 01:00', full: '2026-10-22_0200', until: '23 Okt 02:05', dur: '24 mnt', ok: true }
  ],

  /* Tindakan restore yang dicatat: Restore sekarang, naikkan ke production, dan hapus database cadangan. */
  restoreLog: [],

  /* Sesi Restore manual, terbaru dulu. status: wait | run | fail | done | expired | revoked */
  manualRestores: [
    { id: 2, source: 'keuangan-db', tag: '11.4-a1', full: '2026-10-21_0300', fullSize: '6,8 GB', binlog: 'all', until: '21 Okt 08:05', name: 'keuangan-latih', port: 3307, created: '22 Okt 09:10', by: 'admin', status: 'expired', endAt: '22 Okt 13:10', note: 'Script tidak dijalankan sampai batas 4 jam.' },
    { id: 1, source: 'siak-db', tag: '11.4-a1', full: '2026-10-24_0100', fullSize: '14,2 GB', binlog: 'none', until: '24 Okt 01:00', name: 'siak-latih', port: 3307, created: '25 Okt 10:00', by: 'admin', status: 'revoked', endAt: '25 Okt 10:40', host: 'db-umum.rs.local', note: 'Ruang disk kurang. Dicabut admin, sisa unduhan dihapus script.' }
  ],

  /* Runner menjalankan uji restore dan database cadangan. state: ok | low (scratch hampir penuh) | dl (unduhan gagal) | dead | off | wait */
  runners: [
    { name: 'runner-a', host: 'vm-lumbung.rs.local', ip: '10.10.9.10', reg: '12 Mar 2026', ver: '1.0.0', def: true, state: 'ok', since: '', hb: '40 detik lalu', free: 62, freeTxt: '1,2 TB dari 2 TB', dlErr: '' },
    { name: 'runner-b', host: 'vm-dr.rs.local', ip: '10.10.9.20', reg: '3 Sep 2026', ver: '1.0.0', state: 'dl', since: '03:12', hb: '1 menit lalu', free: 71, freeTxt: '710 GB dari 1 TB', dlErr: 'Unduhan terputus berulang (connection reset)' }
  ],

  /* Snapshot laporan harian 30 Okt 07:00. Isi laporan tidak ikut berubah setelah terbit. */
  dailySnap: {
    rows: [
      ['lab-db', '52 jam lalu', '4 jam lalu', { ok: false, date: '27 Okt' }, 'bad', '5 masalah'],
      ['siak-db', '6 jam lalu', '6 jam lalu', { ok: true, date: '28 Okt' }, 'bad', '2 masalah'],
      ['simrs-utama', '4 jam lalu', '4 jam lalu', { ok: true, date: '29 Okt' }, 'bad', 'Koneksi hampir penuh'],
      ['farmasi-db', '7 jam lalu', '6 jam lalu', { ok: true, date: '26 Okt' }, 'ok', 'OK'],
      ['kepegawaian-db', '8 jam lalu', '7 jam lalu', { ok: true, date: '30 Okt' }, 'ok', 'OK']
    ],
    alerts: [['restore-lab', '27 Okt 03:42'], ['full-lab', '29 Okt 14:31'], ['binlog-lab', '23 Okt 08:35'], ['upload-siak', '30 Okt 03:14'], ['disk-lab', '30 Okt 07:00'], ['conn-simrs', '29 Okt 10:05'], ['sum-siak', '29 Okt 01:14'], ['cad-lab', '24 Okt 01:00'], ['runner-b-nas', '30 Okt 03:12', { cond: 'runner-b gagal mengunduh file backup', server: 'runner-b', kind: 'Runner', lvl: 'bad' }]],
    off: ['keuangan-db']
  },

  alerts: [
    { key: 'restore-lab', kind: 'Uji restore', cond: 'Uji restore gagal', server: 'lab-db', since: '27 Okt 03:42', notif: 'Terkirim' },
    { key: 'full-lab', kind: 'Backup', cond: 'Full backup > 36 jam', server: 'lab-db', since: 'kemarin 14:31 (18 jam)', notif: 'Terkirim · pengingat 02:31' },
    { key: 'binlog-lab', kind: 'Binlog', cond: 'Rantai binlog putus: 000790 hilang', server: 'lab-db', since: '23 Okt 08:35', notif: 'Terkirim' },
    { key: 'upload-siak', kind: 'Upload', cond: 'Upload gagal > 2 jam: dump _users (HTTP 422)', server: 'siak-db', since: '03:14 (5 jam)', notif: 'Terkirim' },
    { key: 'hb-farmasi', kind: 'Agen', cond: 'Agen tidak mengirim heartbeat > 10 menit', server: 'farmasi-db', since: '08:11 (4 menit)', notif: 'queue' },
    { key: 'sum-siak', kind: 'Integritas', cond: 'Checksum berbeda untuk file bernama sama', server: 'siak-db', since: '29 Okt 01:14', notif: 'Terkirim' },
    { key: 'disk-lab', kind: 'Kesehatan', lvl: 'bad', cond: 'Disk /backup diperkirakan penuh ±12 Nov', server: 'lab-db', since: '30 Okt 07:00', notif: 'Terkirim (laporan harian)' },
    { key: 'conn-simrs', kind: 'Kesehatan', lvl: 'bad', cond: 'Koneksi puncak > 90% max_connections', server: 'simrs-utama', since: 'kemarin 10:05', notif: 'Terkirim' },
    { key: 'cad-lab', kind: 'Cadangan', cond: 'Database cadangan tidak di-restore sejak 22 Okt', server: 'lab-db', since: '24 Okt 01:00', notif: 'Terkirim' }
  ],

  // [kondisi, server, mulai, pulih, jenis]
  recovered: [
    ['NAS/NFS tidak ter-mount', 'semua', '25 Okt 02:10', '25 Okt 02:48', 'NAS'],
    ['Agen tidak mengirim heartbeat > 10 menit', 'farmasi-db', '23 Okt 13:20', '23 Okt 13:41', 'Agen'],
    ['Dump siakdb > 36 jam', 'siak-db', '22 Okt 01:00', '22 Okt 13:12', 'Backup'],
    ['Uji restore gagal', 'farmasi-db', '19 Okt 03:51', '26 Okt 03:41', 'Uji restore'],
    ['Full backup > 36 jam', 'lab-db', '14 Sep 02:00', '14 Sep 19:30', 'Backup'],
    ['Agen tidak mengirim heartbeat > 10 menit', 'siak-db', '3 Sep 22:10', '3 Sep 22:48', 'Agen'],
    ['Runner tidak merespons', 'runner-a', '18 Okt 21:05', '18 Okt 21:40', 'Runner'],
    ['Koneksi puncak > 90% max_connections', 'simrs-utama', '28 Agu 10:02', '29 Agu 08:15', 'Kesehatan']
  ],

  restores: [
    // Riwayat uji, terbaru dulu. Satu giliran = full terbaru + dump terbaru tiap database + cek rantai binlog.
    // fail = jenis yang gagal; failDb = database yang dumpnya gagal; binlogGap = nomor yang hilang.
    // skip = uji tidak terlaksana karena masalah di sisi runner; backup-nya tidak dicap gagal.
    { server: 'lab-db', runner: 'runner-b', when: '30 Okt 03:30', full: '2026-10-28_0200', binlog: '—', dur: '—', ok: false, skip: true, reason: 'Unduhan dari LumbungDB terputus berulang (connection reset)' },
    { server: 'kepegawaian-db', runner: 'runner-a', when: '30 Okt 03:30', full: '2026-10-29_2300', binlog: '000001–000037', dur: '12 mnt', ok: true },
    { server: 'simrs-utama', runner: 'runner-b', when: '29 Okt 03:30', full: '2026-10-29_0200', binlog: '001388–002101', dur: '44 mnt', ok: true },
    { server: 'siak-db', runner: 'runner-a', when: '28 Okt 03:30', full: '2026-10-28_0100', binlog: '000118–000325', dur: '18 mnt', ok: true },
    { server: 'lab-db', runner: 'runner-b', when: '27 Okt 03:30', full: '2026-10-27_0200', binlog: '000786–000803', dur: '9 mnt', ok: false, fail: 'full', binlogGap: '000790' },
    { server: 'farmasi-db', runner: 'runner-b', when: '26 Okt 03:30', full: '2026-10-26_0000', binlog: '000301–000521', dur: '15 mnt', ok: true },
    { server: 'siak-db', runner: 'runner-a', when: '24 Okt 03:30', full: '2026-10-24_0100', binlog: '000118–000313', dur: '17 mnt', ok: true },
    { server: 'simrs-utama', runner: 'runner-b', when: '23 Okt 03:30', full: '2026-10-23_0200', binlog: '001388–002083', dur: '43 mnt', ok: true },
    { server: 'lab-db', runner: 'runner-b', when: '22 Okt 03:30', full: '2026-10-22_0200', binlog: '000641–000788', dur: '11 mnt', ok: true },
    { server: 'farmasi-db', runner: 'runner-b', when: '19 Okt 03:30', full: '2026-10-19_0000', binlog: '000301–000500', dur: '21 mnt', ok: false, fail: 'dump', failDb: 'farmasi_stok', dumpNote: 'ERROR 1064 (42000) at line 18422: unexpected end of input\nKemungkinan: dump 18 Okt terpotong saat ditulis' }
  ],

  retention: {
    hapus: [
      ['simrs-utama', 'full', '2026-09-29_0200.xbstream.gz', '51,2 GB', 'Harian > 30 hari'],
      ['simrs-utama', 'dump', 'simrs/simrs-2026-09-29_0230.sql.gz', '9,8 GB', 'Harian > 30 hari'],
      ['siak-db', 'binlog', 'mysql-bin.000201.gz … 000214.gz (14)', '310 MB', 'Binlog > 3 hari'],
      ['keuangan-db', 'full', '2026-09-20_0300.xbstream.gz', '6,8 GB', 'Harian > 30 hari'],
      ['farmasi-db', 'dump', 'farmasi/farmasi-2026-09-29_0000.sql.gz', '1,1 GB', 'Harian > 30 hari'],
      ['kepegawaian-db', '—', 'tidak ada', '—', 'Baru terdaftar 29 Okt']
    ],
    lindung: [
      ['lab-db', '2026-10-22_0200.xbstream.gz', 'Full terakhir yang <b>lolos uji restore</b>; tetap disimpan walau nanti melewati 30 hari'],
      ['keuangan-db', '2026-10-21_0300.xbstream.gz', 'Full terakhir container nonaktif; retensi tidak menghapus satu-satunya backup yang masih valid'],
      ['lab-db', 'mysql-bin.000786.gz … 000812.gz (000790 hilang)', 'Binlog sejak full terakhir yang <b>lolos uji restore</b>, walaupun sudah lebih dari 3 hari']
    ],
    log: [
      ['30 Okt 01:00', '398 file', '1,2 TB', 'ok', 'Selesai (uji coba, tidak ada yang dihapus)'],
      ['29 Okt 01:00', '405 file', '1,3 TB', 'ok', 'Selesai (uji coba, tidak ada yang dihapus)'],
      ['28 Okt 01:00', '0 file', '—', 'bad', 'Gagal: NAS tidak merespons (timeout)']
    ]
  },

  integrity: [
    { key: 'sum-siak', server: 'siak-db', file: 'databases/siakdb/siakdb-2026-10-29_0100.sql.gz', stored: '3,10 GB · 9f2c…a71e', storedAt: 'diterima 29 Okt 01:12', resent: '3,14 GB · 41bd…07c3', resentAt: 'disimpan sebagai versi 2 · 29 Okt 01:14', reviewed: null },
    { key: 'sum-simrs', server: 'simrs-utama', file: 'binlog/mysql-bin.002011.gz', stored: '98 MB · c0aa…1f90', storedAt: 'diterima 21 Okt 22:40', resent: '98 MB · 77e1…b2d4', resentAt: 'disimpan sebagai versi 2 · 21 Okt 22:42', reviewed: '22 Okt · admin' }
  ],

  notif: [
    { at: '08:11', msg: 'farmasi-db: agen tidak mengirim heartbeat 10 menit', tries: 1, ok: false },
    { at: '07:00', msg: 'Laporan harian 30 Okt: 6 temuan', tries: 1, ok: true },
    { at: '03:14', msg: 'siak-db: dump _users gagal terkirim 2 jam (HTTP 422)', tries: 1, ok: true },
    { at: '03:12', msg: 'runner-b: gagal mengunduh file backup', tries: 1, ok: true },
    { at: '02:31', msg: 'lab-db: full backup belum masuk 48 jam (pengingat)', tries: 1, ok: true },
        { at: '29 Okt 01:14', msg: 'siak-db: checksum berbeda untuk file bernama sama', tries: 1, ok: true },
    { at: '27 Okt 03:42', msg: 'lab-db: uji restore gagal', tries: 2, ok: true },
    { at: '24 Okt 01:00', msg: 'lab-db: database cadangan melewati jadwal restore', tries: 1, ok: true },
    { at: '23 Okt 08:35', msg: 'lab-db: rantai binlog putus, 000790 hilang', tries: 1, ok: true }
  ],

  /* Matriks kesehatan hari ini: [teks, tingkat] */
  matrix: {
    'simrs-utama': [['468/500 (94%)', 'bad'], ['99,2%', 'ok'], ['1.284 ↑', 'bad'], ['3', 'warn'], ['+6,1 GB', 'ok'], ['38% bebas', 'ok'], ['62%', 'ok'], ['normal', 'ok']],
    'lab-db': [['88/300', 'ok'], ['97,1% ↓', 'warn'], ['41', 'ok'], ['0', 'ok'], ['+1,2 GB', 'ok'], ['54% bebas', 'ok'], ['penuh ±12 Nov', 'bad'], ['−31%', 'bad']],
    'siak-db': [['142/300', 'ok'], ['99,6%', 'ok'], ['12', 'ok'], ['0', 'ok'], ['+0,4 GB', 'ok'], ['61% bebas', 'ok'], ['48%', 'ok'], ['normal', 'ok']],
    'farmasi-db': [['61/200', 'none'], ['99,1%', 'none'], ['7', 'none'], ['0', 'none'], ['+0,4 GB', 'none'], ['48% bebas', 'none'], ['55%', 'none'], ['normal', 'ok']],
    'kepegawaian-db': [['36/150', 'ok'], ['99,4%', 'ok'], ['12', 'ok'], ['0', 'ok'], ['+0,1 GB', 'ok'], ['70% bebas', 'ok'], ['30%', 'ok'], ['normal', 'ok']]
  },

  /* Parameter deret metrik per server (nilai hari ini + kemiringan per hari) */
  metrics: {
    'simrs-utama': { maxConn: 500, conn: [468, 9, 18], rejected: [37, 3, 4], bp: [99.2, 0, 0.15], slow: [1284, 40, 60], slowAvg: 702, deadlock: [3, 0, 1.5], dbSize: [412, 0.87], diskData: [62, 0.25], diskBackup: [62, 0.1], fullSize: [52.7, 51.8], tables: [['simrs.log_aktivitas', '96 GB', '+2,8 GB'], ['simrs.rekam_medis_detail', '71 GB', '+1,1 GB'], ['simrs.resep_item', '38 GB', '+0,6 GB']] },
    'lab-db': { maxConn: 300, conn: [88, 0.3, 9], rejected: [0, 0, 0], bp: [97.1, 0.15, 0.08], slow: [41, 0, 8], slowAvg: 44, deadlock: [0, 0, 0.4], dbSize: [38, 0.17], diskData: [46, 0.2], diskBackup: [71, 2.1], fullSize: [18.4, 26.6], fullDrop: true, tables: [['lab_hasil.hasil_item', '22 GB', '+0,7 GB'], ['lab_hasil.order_lab', '9 GB', '+0,3 GB']] },
    'siak-db': { maxConn: 300, conn: [142, 0.5, 12], rejected: [0, 0, 0], bp: [99.6, 0, 0.1], slow: [12, 0, 4], slowAvg: 13, deadlock: [0, 0, 0.3], dbSize: [21, 0.06], diskData: [39, 0.1], diskBackup: [48, 0.05], fullSize: [14.2, 14.0], tables: [['siakdb.transaksi', '8 GB', '+0,2 GB'], ['siakdb.jurnal', '5 GB', '+0,1 GB']] },
    'farmasi-db': { maxConn: 200, conn: [61, 0.2, 6], rejected: [0, 0, 0], bp: [99.1, 0, 0.1], slow: [7, 0, 3], slowAvg: 8, deadlock: [0, 0, 0.2], dbSize: [17, 0.05], diskData: [52, 0.1], diskBackup: [55, 0.1], fullSize: [7.9, 7.8], stale: true, tables: [['farmasi.mutasi_obat', '6 GB', '+0,1 GB']] },
    'kepegawaian-db': { maxConn: 150, conn: [36, 0.1, 4], rejected: [0, 0, 0], bp: [99.4, 0, 0.1], slow: [12, 0, 3], slowAvg: 11, deadlock: [0, 0, 0.2], dbSize: [5.4, 0.02], diskData: [30, 0.05], diskBackup: [30, 0.05], fullSize: [2.3, 2.3], partial: true, tables: [['kepegawaian.absensi', '2,1 GB', '+0,02 GB']] }
  },

  env: [
    ['AGENTS_FILE', '/config/agents.conf · 5 agen aktif · 1 dikomentari (#)'],
    ['RUNNERS_FILE', '/config/runners.conf · token runner, terpisah dari agen'],
    ['DATA_DIR', '/data (mount NFS dari server, rslave)'],
    ['MOUNT_MARKER', '.lumbung-mounted · ditemukan'],
    ['MOUNT_PROBE_TIMEOUT_SECONDS', '10 detik'],
    ['UPLOAD_MAX_CONCURRENT', '4'],
    ['DASHBOARD_USER', 'admin'],
    ['TRUSTED_PROXY_IPS', '10.10.9.10'],
    ['TELEGRAM_CHAT_ID', 'Grup IT RS (terisi)'],
    ['ALERT_HEARTBEAT_MINUTES', '10 menit'],
    ['ALERT_FULL_HOURS', '36 jam, bila agen belum melaporkan jadwal; selain itu 1,5× jarak jadwal'],
    ['ALERT_DUMP_HOURS', '36 jam per database, bila agen belum melaporkan jadwal; selain itu 1,5× jarak jadwal'],
    ['ALERT_USERS_HOURS', '36 jam, bila agen belum melaporkan jadwal; selain itu 1,5× jarak jadwal'],
    ['BACKUP_REQUEST_TTL_MINUTES', '15 menit'],
    ['ALERT_DB_EXPECTED_DAYS', '30 hari'],
    ['ALERT_DISK_FREE_PERCENT', '10%'],
    ['RENOTIFY_HOURS', '12 jam'],
    ['ALERT_BINLOG_GAP_MINUTES', '30 menit'],
    ['ALERT_UPLOAD_STALL_MINUTES', '120 menit'],
    ['RETENTION_DAILY_DAYS', '30'],
    ['RETENTION_MONTHLY', '12'],
    ['RETENTION_BINLOG_DAYS', '3'],
    ['RETENTION_DRY_RUN', 'true'],
    ['RESTORE_TEST_DEFAULT', 'Tiap Minggu 03:30 untuk container baru; diubah per container'],
    ['MANUAL_RESTORE_MAX_HOURS', '24 jam, batas waktu terpanjang satu sesi Restore manual termasuk perpanjangan'],
    ['RUNNER_DOWNLOAD_MAX_CONCURRENT', '2 unduhan, terpisah dari batas upload'],
    ['RUNNER_DOWNLOAD_MAX_MBPS', '500 Mbps per unduhan'],
    ['RUNNER_MAX_TOTAL', '2 tugas bersamaan · satu tugas per runner'],
    ['ALERT_RUNNER_HEARTBEAT_MINUTES', '10 menit'],
    ['ALERT_RUNNER_SCRATCH_FREE_PERCENT', '15%'],
    ['ALERT_CADANGAN_LATE', 'bila melewati 2× jadwal restore'],
    ['VERIFY_SCHEDULE', 'Minggu 04:00'],
    ['NOTIFY_RETRY', '5 menit, bertahap hingga 1 jam'],
    ['METRICS_LOAD_INTERVAL', 'sampel tiap 60 detik, disimpan per jam'],
    ['METRICS_SIZE_INTERVAL', '1 hari'],
    ['REPORT_DAILY_AT', '07:00'],
    ['REPORT_WEEKLY_AT', 'Senin 07:00'],
    ['AI_PROVIDER', 'claude (claude-sonnet-5-5)']
  ]
};
