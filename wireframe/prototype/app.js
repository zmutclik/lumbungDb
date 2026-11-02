/* LumbungDB prototype — router hash, state di memori, semua data contoh. */
(function () {
  'use strict';
  var D = window.LDB;

  // ---------- State ----------
  var S = {
    servers: D.servers,
    runners: D.runners,
    runnerSim: 'normal', // normal | ok | dead | disk | none (Pengaturan › Simulasi prototype)
    runWiz: null,
    restoreLog: D.restoreLog,
    standby: D.standby,
    standbyRun: null,
    alerts: D.alerts,
    resolved: {},
    notif: D.notif,
    notifDown: true,
    integrity: D.integrity,
    restores: D.restores,
    restoreRun: null,
    restoreSel: null,
    sim: 'normal', // normal | sehat | kosong (Pengaturan › Simulasi prototype)
    aiDown: false,
    confRO: false,
    noLogin: false,
    rescan: null,
    retTab: 'hapus',
    wizard: null,
    riw: { server: '', kind: '', q: '', page: 0 },
    hRange: 14,
    tlRange: 7,
    lapTab: 'h',
    alertF: { status: '', server: '', kind: '' },
    nas: { state: 'ok', since: '' }, // ok | down (tidak ter-mount) | hang (tidak merespons) | low (ruang < 10%)
    clock: new Date(2026, 9, 30, 8, 15, 0),
    lastRefresh: new Date(2026, 9, 30, 8, 15, 0),
    countdown: 60,
    reqs: D.backupRequests,
    ctrlLog: D.controlLog,
    manualFiles: [], // file hasil permintaan backup selama sesi prototype
    restoreSim: 'ok', // ok | fail (Pengaturan › Simulasi prototype)
    hostSim: 'db-baru.rs.local', // server yang dibaca agen container baru
    runnerHostSim: 'vm-baru.rs.local', // server yang dibaca runner baru
    reqSim: 'ok', // ok | fail | busy | disk | restart (Pengaturan › Simulasi prototype)
    agentReject: false,
    rm: D.manualRestores, // sesi Restore manual
    rmForm: null,
    rmSim: 'ok' // ok | disk | check | expire (Pengaturan › Simulasi prototype)
  };
  D.servers.forEach(function (s) {
    var a = D.agentInfo[s.name];
    s.agent = { tag: a.tag };
    s.fullDur = a.fullDur;
    s.bsched = a.sched;
    s.runnerCad = s.runnerCad || s.runner; // runner cadangan boleh berbeda dari runner uji
  });

  var BASE = JSON.stringify({ servers: D.servers, runners: D.runners, restoreLog: D.restoreLog, standby: D.standby, metrics: D.metrics, matrix: D.matrix, restores: D.restores, integrity: D.integrity, notif: D.notif, reqs: D.backupRequests, ctrlLog: D.controlLog, rm: D.manualRestores });
  var SHORT = {
    'restore-lab': 'Uji restore gagal',
    'full-lab': 'Full backup terlambat',
    'hb-farmasi': 'Agen tidak merespons',
    'binlog-lab': 'Rantai binlog putus',
    'upload-siak': 'Upload gagal',
    'sum-siak': 'Checksum perlu ditinjau',
    'disk-lab': 'Disk /backup hampir penuh',
    'conn-simrs': 'Koneksi hampir penuh',
    'cad-lab': 'Database cadangan terlalu tua'
  };
  var HARD = { 'Backup': 1, 'Agen': 1, 'Uji restore': 1, 'NAS': 1, 'Binlog': 1, 'Upload': 1, 'Runner': 1, 'Jadwal': 1, 'Permintaan': 1 };
  function alertLvl(a) { return a.lvl || (HARD[a.kind] ? 'bad' : 'warn'); }
  function canWrite() { return !S.noLogin; }

  // ---------- Util ----------
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function pill(kind, text) { return '<span class="pill pill-' + kind + '">' + text + '</span>'; }
  function hhmm(d) { return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
  function nowLabel() { return hhmm(S.clock); }
  function num(n, dec) {
    return Number(n).toLocaleString('id-ID', { minimumFractionDigits: dec || 0, maximumFractionDigits: dec || 0 });
  }
  function findServer(name) { return S.servers.filter(function (s) { return s.name === name; })[0]; }
  function findRunner(name) { return S.runners.filter(function (r) { return r.name === name; })[0]; }
  function runnerOf(s) { return findRunner(s.runner); }
  function cadRunnerOf(s) { return findRunner(s.runnerCad); }
  // Runner cadangan baru bertugas bila database cadangan disiapkan.
  function usesCad(s) { return !!(s.standby || s.sched && s.sched.standby.on); }
  function handlesCad(s, name) { return s.runnerCad === name && usesCad(s); }
  function containersOf(name) { return S.servers.filter(function (s) { return s.runner === name || handlesCad(s, name); }); }
  function activeServers() { return S.servers.filter(function (s) { return s.status !== 'off'; }); }
  function monitored() { return S.servers.filter(function (s) { return s.status === 'active'; }); }

  function seeded(str) {
    var h = 1779033703;
    for (var i = 0; i < str.length; i++) { h = Math.imul(h ^ str.charCodeAt(i), 3432918353); h = h << 13 | h >>> 19; }
    return function () {
      h = Math.imul(h ^ h >>> 16, 2246822507); h = Math.imul(h ^ h >>> 13, 3266489909);
      return ((h ^= h >>> 16) >>> 0) / 4294967296;
    };
  }
  // Deret n nilai yang berakhir tepat di `end`, naik `slope` per titik, dengan derau.
  function gen(key, n, end, slope, noise, lo, hi) {
    var r = seeded(key + ':' + n), out = [];
    for (var i = 0; i < n; i++) {
      var v = i === n - 1 ? end : end - slope * (n - 1 - i) + noise * (r() - 0.5) * 2;
      if (lo != null) v = Math.max(lo, v);
      if (hi != null) v = Math.min(hi, v);
      out.push(v);
    }
    return out;
  }
  function dayLabels(n) {
    var out = [];
    for (var i = 0; i < n; i++) {
      var d = new Date(2026, 9, 30 - (n - 1 - i));
      out.push(n > 31 ? d.getDate() + '/' + (d.getMonth() + 1) : String(d.getDate()));
    }
    return out;
  }
  function addDays(days) {
    var d = new Date(2026, 9, 30 + days);
    var m = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'][d.getMonth()];
    return d.getDate() + ' ' + m + (d.getFullYear() !== 2026 ? ' ' + d.getFullYear() : '');
  }
  function genToken() {
    var chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789', out = 'lmb_';
    var buf = new Uint32Array(32);
    (window.crypto || window.msCrypto).getRandomValues(buf);
    for (var i = 0; i < 32; i++) out += chars[buf[i] % chars.length];
    return out;
  }

  // ---------- Alert & status ----------
  function activeAlerts() {
    var list = S.alerts.filter(function (a) {
      var s = findServer(a.server);
      // Selama NAS tidak ter-mount semua upload ditolak 503; satu penyebab, satu alert.
      if (a.kind === 'Upload' && nasDown()) return false;
      return !S.resolved[a.key] && s && s.status !== 'off';
    }).map(function (a) {
      var c = Object.assign({}, a);
      if (c.notif === 'queue') c.notif = S.notifDown ? '<b>Tertunda</b> · coba lagi 08:20' : 'Terkirim ' + nowLabel();
      return c;
    });
    S.servers.forEach(function (s) {
      if (s.status === 'wait-hb') list.unshift({ key: 'new-' + s.name, kind: 'Agen', cond: 'Belum ada heartbeat dari agen', server: s.name, since: s.regAt || 'baru saja', notif: 'Terkirim', fresh: 1 });
      if (s.status === 'wait-backup') list.unshift({ key: 'new-' + s.name, kind: 'Backup', cond: 'Belum pernah mengirim backup', server: s.name, since: s.regAt || 'baru saja', notif: 'Terkirim', fresh: 1 });
      if (s.promoted && s.status !== 'off' && !runnerOf(s)) list.unshift({ key: 'promoted-runner-' + s.name, kind: 'Runner', short: 'Belum punya runner', cond: 'Production tanpa runner di server lain: belum diuji dan tidak punya cadangan', server: s.name, since: s.regAt || 'baru saja', notif: 'Terkirim' });
    });
    // Alert NAS berlaku untuk semua container, jadi tidak terikat ke satu container.
    if (nasDown()) list.unshift({ key: 'nas-down', kind: 'NAS', cond: NAS_COND[S.nas.state], server: 'semua', since: S.nas.since, notif: 'Terkirim' });
    runnerAlerts().forEach(function (x) { list.unshift(x); });
    backupControlAlerts().forEach(function (x) { list.unshift(x); });
    if (S.nas.state === 'low') list.unshift({ key: 'nas-low', kind: 'NAS', cond: NAS_COND.low, server: 'semua', since: S.nas.since, notif: 'Terkirim' });
    return list;
  }
  var NAS_COND = { down: 'NAS/NFS tidak ter-mount', hang: 'NAS tidak merespons', low: 'Ruang NAS < 10%' };
  // Keduanya menahan upload, uji restore, dan retensi; bedanya hanya penyebab yang ditampilkan.
  function nasDown() { return S.nas.state === 'down' || S.nas.state === 'hang'; }
  function nasBanner() {
    if (S.nas.state === 'hang') return '<div class="banner banner-bad no-print"><b>NAS tidak merespons sejak ' + S.nas.since + '.</b> Pemeriksaan NAS melewati batas 10 detik. Upload baru ditolak (503) dan dicoba lagi otomatis; backup baru tetap aman di server masing-masing. Uji restore dan retensi ditunda.<p>Periksa kondisi NAS dan jaringan ke NAS. Alert dan dashboard tetap berjalan.</p></div>';
    if (nasDown()) return '<div class="banner banner-bad no-print"><b>NAS tidak ter-mount sejak ' + S.nas.since + '.</b> Upload dari semua agen ditolak (503) dan dicoba lagi otomatis; backup baru tetap aman di server masing-masing. Uji restore dan retensi ditunda.<p>Periksa mount NFS di VM LumbungDB dan pastikan file penanda <code>.lumbung-mounted</code> ada.</p></div>';
    if (S.nas.state === 'low') return '<div class="banner banner-bad no-print"><b>Ruang NAS tinggal 8%</b> (0,6 TB dari 8 TB) sejak ' + S.nas.since + '. Upload masih diterima, tetapi akan gagal bila ruang habis. <a href="#/laporan" data-act="open-lap" data-v="ret">Lihat retensi ›</a></div>';
    return '';
  }
  function nasTile() {
    var st = S.nas.state, body;
    if (st === 'hang') body = '<b>Tidak merespons</b><span class="muted">sejak ' + S.nas.since + ' · ruang tidak diketahui</span>';
    else if (st === 'down') body = '<b>Tidak ter-mount</b><span class="muted">sejak ' + S.nas.since + ' · ruang tidak diketahui</span>';
    else if (st === 'low') body = '<b>8% bebas</b><div class="bar"><i style="width:92%"></i></div><span class="muted">0,6 TB dari 8 TB</span>';
    else body = '<b>26% bebas</b><div class="bar"><i style="width:74%"></i></div><span class="muted">2,1 TB dari 8 TB</span>';
    return '<a class="tile' + (st === 'ok' ? '' : ' bad') + '" href="#/laporan" data-act="open-lap" data-v="ret"><small>Ruang NAS</small>' + body + '</a>';
  }
  function srvLink(a) {
    if (a.kind === 'Runner' && findRunner(a.server)) return '<a href="#/runner/' + encodeURIComponent(a.server) + '">' + esc(a.server) + '</a>';
    return findServer(a.server) ? '<a href="#/container/' + encodeURIComponent(a.server) + (ALERT_TAB[a.kind] || '') + '">' + esc(a.server) + '</a>' : esc(a.server);
  }
  // Tampilan pengiriman agen, dari kolom antrean dan error di heartbeat.
  function uploadView(s) {
    var u = s.upload;
    if (!u || s.status === 'off') return { val: '—', lvl: '', sub: s.status === 'off' ? 'container nonaktif' : 'belum ada data' };
    if (nasDown()) return { val: 'Ditahan', lvl: 'bad', sub: NAS_COND[S.nas.state] + ' · upload ditolak 503' };
    if (u.err) return { val: u.pending + ' file', lvl: 'bad', sub: u.err.code + ' sejak ' + u.err.since };
    return { val: u.pending + ' file', lvl: '', sub: u.lastOk ? 'upload terakhir ' + u.lastOk : 'belum ada upload berhasil' };
  }
  function binlogView(s) {
    if (s.binlogGap) return { val: 'Putus', lvl: 'bad', sub: s.binlogGap.no + ' hilang · ' + s.binlogGap.day };
    return s.files.binlog ? { val: 'Lengkap', lvl: '', sub: s.files.binlog + ' file tersimpan' } : { val: '—', lvl: '', sub: 'belum ada binlog' };
  }
  function alertsFor(name) { return activeAlerts().filter(function (a) { return a.server === name; }); }
  function srvLevel(s) {
    if (s.status === 'off') return 'off';
    if (s.status === 'wait-hb') return 'wait';
    var al = alertsFor(s.name);
    if (s.status === 'active' && !s.files.full && !al.length) return 'wait';
    if (s.status === 'wait-backup' || al.some(function (a) { return alertLvl(a) === 'bad'; })) return 'bad';
    if (al.length) return 'warn';
    return 'ok';
  }
  function srvPill(s) {
    if (s.status === 'off') return pill('off', 'Nonaktif');
    if (s.status === 'wait-hb') return pill('wait', 'Menunggu heartbeat pertama');
    if (s.status === 'wait-backup') return pill('bad', 'Belum ada backup');
    var al = alertsFor(s.name), lvl = srvLevel(s);
    if (lvl === 'wait') return pill('wait', 'Menunggu full pertama');
    if (!al.length) return pill('ok', 'OK');
    if (al.length === 1) return pill(lvl, SHORT[al[0].key] || al[0].short || al[0].cond);
    return pill(lvl, al.length + ' masalah');
  }
  var LV_ORDER = { wait: 0, bad: 0, warn: 1, ok: 2, off: 3 };
  function sortByLevel(list) {
    return list.slice().sort(function (a, b) { return LV_ORDER[srvLevel(a)] - LV_ORDER[srvLevel(b)]; });
  }
  function pendingNotif() { return S.notif.filter(function (n) { return !n.ok; }).length; }

  // ---------- Grafik SVG ----------
  function chart(o) {
    var W = 600, H = o.h || 180, L = 50, R = 14, T = 14, B = 24;
    var n = o.labels.length;
    var all = [];
    o.series.forEach(function (s) { s.values.forEach(function (v) { if (v != null) all.push(v); }); });
    if (o.thr != null) all.push(o.thr);
    var lo = o.min != null ? o.min : Math.min.apply(null, all);
    var hi = o.max != null ? o.max : Math.max.apply(null, all);
    if (hi === lo) { hi += 1; lo -= 1; }
    var pad = (hi - lo) * 0.1;
    if (o.min == null) lo = Math.max(o.floor != null ? o.floor : -Infinity, lo - pad);
    if (o.max == null) hi += pad;
    var fmt = o.fmt || function (v) { return num(v); };
    function x(i) { return L + (n === 1 ? 0 : i * (W - L - R) / (n - 1)); }
    function y(v) { return T + (1 - (v - lo) / (hi - lo)) * (H - T - B); }
    var out = '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(o.title || 'grafik') + '">';
    for (var k = 0; k <= 4; k++) {
      var v = lo + k * (hi - lo) / 4, yy = y(v).toFixed(1);
      out += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + yy + '" y2="' + yy + '"/>';
      out += '<text x="' + (L - 6) + '" y="' + (+yy + 3.5) + '" text-anchor="end">' + esc(fmt(v)) + '</text>';
    }
    var step = Math.max(1, Math.round((n - 1) / 6));
    for (var i = 0; i < n; i++) {
      if (i % step === 0 || i === n - 1) {
        if (i !== n - 1 && n - 1 - i < step / 2) continue;
        out += '<text x="' + x(i).toFixed(1) + '" y="' + (H - 6) + '" text-anchor="middle">' + esc(o.labels[i]) + '</text>';
      }
    }
    if (o.split != null) {
      var sx = x(o.split).toFixed(1);
      out += '<line x1="' + sx + '" x2="' + sx + '" y1="' + T + '" y2="' + (H - B) + '" stroke="var(--line-2)"/>';
      out += '<text class="fc-label" x="' + (+sx + 5) + '" y="' + (T + 10) + '">perkiraan →</text>';
    }
    if (o.thr != null) {
      var ty = y(o.thr).toFixed(1);
      out += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + ty + '" y2="' + ty + '" stroke="var(--c-thr)" stroke-width="1.2" stroke-dasharray="5 4"/>';
      out += '<text class="thr-label" x="' + (W - R) + '" y="' + (+ty - 5) + '" text-anchor="end">' + esc(o.thrLabel || '') + '</text>';
    }
    o.series.forEach(function (s) {
      if (s.bars) {
        var bw = Math.max(3, (W - L - R) / n * 0.6);
        s.values.forEach(function (v, i) {
          if (v == null) return;
          var top = y(Math.max(v, lo)), base = y(Math.max(lo, 0));
          var col = s.colorAt ? s.colorAt(v, i) : s.color;
          out += '<rect x="' + (x(i) - bw / 2).toFixed(1) + '" y="' + top.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(1, base - top).toFixed(1) + '" fill="' + col + '" rx="1.5"><title>' + esc(o.labels[i] + ': ' + fmt(v)) + '</title></rect>';
        });
        return;
      }
      var pts = [], segs = [];
      s.values.forEach(function (v, i) {
        if (v == null) { if (pts.length) segs.push(pts); pts = []; return; }
        pts.push(x(i).toFixed(1) + ',' + y(v).toFixed(1));
      });
      if (pts.length) segs.push(pts);
      segs.forEach(function (p) {
        out += '<polyline points="' + p.join(' ') + '" fill="none" stroke="' + s.color + '" stroke-width="' + (s.width || 2) + '" stroke-linejoin="round"' + (s.dash ? ' stroke-dasharray="6 4"' : '') + '/>';
      });
      if (s.dots !== false) {
        for (var j = s.values.length - 1; j >= 0; j--) {
          if (s.values[j] != null && !s.dash) {
            out += '<circle cx="' + x(j).toFixed(1) + '" cy="' + y(s.values[j]).toFixed(1) + '" r="3.5" fill="' + s.color + '"><title>' + esc(o.labels[j] + ': ' + fmt(s.values[j])) + '</title></circle>';
            break;
          }
        }
      }
    });
    return out + '</svg>';
  }
  function spark(values, color) {
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    if (hi === lo) hi = lo + 1;
    var pts = values.map(function (v, i) {
      return (i * 120 / (values.length - 1)).toFixed(1) + ',' + (26 - (v - lo) / (hi - lo) * 24).toFixed(1);
    });
    return '<svg class="spark" viewBox="0 0 120 28" width="120" height="28" aria-hidden="true"><polyline points="' + pts.join(' ') + '" fill="none" stroke="' + (color || C.main) + '" stroke-width="1.6"/></svg>';
  }
  // Warna grafik lewat token CSS supaya ikut tema terang/gelap tanpa render ulang
  var C = { main: 'var(--c-main)', alt: 'var(--c-alt)', bad: 'var(--c-bad)', warn: 'var(--c-warn)' };

  // ---------- Navigasi ----------
  function route() {
    var h = location.hash.replace(/^#\/?/, '');
    return h ? h.split('/').map(decodeURIComponent) : [];
  }
  function navLink(href, label, extra, on) {
    return '<a class="nav-link' + (on ? ' on' : '') + '" href="' + href + '"' + (on ? ' aria-current="page"' : '') + '>' + label + (extra || '') + '</a>';
  }
  function count(n) { return n ? '<span class="nav-count">' + n + '</span>' : ''; }
  function renderNav() {
    var p = route()[0] || '';
    var h = '';
    h += navLink('#/', 'Dashboard', count(activeAlerts().length), p === '');
    h += navLink('#/container', 'Container', '', p === 'container');
    h += navLink('#/runner', 'Runner', S.restoreRun || S.standbyRun ? '<span class="spin"></span>' : count(runnerAlerts().length), p === 'runner');
    h += navLink('#/laporan', 'Laporan', count(pendingNotif()), p === 'laporan');
    h += navLink('#/pengaturan', 'Pengaturan', '', p === 'pengaturan');
    document.getElementById('nav').innerHTML = h;
  }
  function crumbs(list) {
    // Halaman tingkat atas sudah ditandai oleh menu; breadcrumb hanya untuk halaman turunan
    document.getElementById('crumbs').innerHTML = list.length < 2 ? '' : list.map(function (c, i) {
      if (i === list.length - 1) return '<b>' + c + '</b>';
      return typeof c === 'string' ? '<span>' + c + '</span>' : '<a href="' + c[1] + '">' + c[0] + '</a>';
    }).join(' <span class="muted">›</span> ');
  }
  function tbl(head, rows, foot) {
    return '<div class="tbl-wrap"><table class="t"><thead><tr>' + head.map(function (h) {
      return typeof h === 'string' ? '<th>' + h + '</th>' : '<th class="' + h[1] + '">' + h[0] + '</th>';
    }).join('') + '</tr></thead><tbody>' + rows.join('') + '</tbody></table>' + (foot ? '<div class="tbl-foot">' + foot + '</div>' : '') + '</div>';
  }
  function pageHead(title, sub, actions) {
    return '<div class="page-head"><div><h1>' + title + '</h1>' + (sub ? '<div class="sub">' + sub + '</div>' : '') + '</div>' + (actions ? '<div class="actions">' + actions + '</div>' : '') + '</div>';
  }
  function section(title, extra) {
    return '<div class="section-title"><h2>' + title + '</h2>' + (extra || '') + '</div>';
  }

  // ---------- Halaman: Dashboard ----------
  var ALERT_TAB = { 'Runner': '/restore', 'Uji restore': '/restore', 'Cadangan': '/pulih', 'Kesehatan': '/kesehatan', 'Backup': '/backup', 'Integritas': '/backup', 'Binlog': '/backup', 'Upload': '/backup', 'Jadwal': '/backup', 'Permintaan': '/backup' };
  function addServerBtn() {
    return canWrite() ? '<a class="btn btn-pri" href="#/container/tambah">+ Tambah container</a>' : '<span class="btn btn-pri disabled" aria-disabled="true" title="Butuh login dashboard">+ Tambah container</span>';
  }
  function latestRestores() {
    var seen = {};
    return S.restores.filter(function (r) { if (r.skip || seen[r.server]) return false; seen[r.server] = 1; return true; });
  }
  function dashReportBox() {
    var sehat = S.sim === 'sehat', count = sehat ? 0 : 6;
    var h = section('Laporan harian', '<span class="muted small">terbit 07:00 · ' + count + ' temuan</span><a class="small" href="#/laporan" style="margin-left:auto">Buka laporan ›</a>');
    if (sehat) return h + '<div class="card card-pad">Tidak ada temuan pada laporan pagi ini.</div>';
    var angka = '<p style="margin:8px 0 0">6 temuan: 4 perlu tindakan segera, 2 perlu diperhatikan.</p>';
    if (S.aiDown) return h + '<div class="aifail">Ringkasan AI tidak tersedia hari ini. Angka di laporan tetap lengkap.' + angka + '</div>';
    return h + '<div class="aibox"><div class="ailabel"><b>Dibuat AI</b> · 07:01 · ringkasan, bukan dasar alert</div><p>Empat hal butuh tindakan hari ini: <b>disk /backup lab-db</b> penuh sekitar 12 November dan kemungkinan jadi penyebab full backup gagal; <b>rantai binlog lab-db</b> putus di 000790; <b>dump _users siak-db</b> gagal terkirim; <b>beban simrs-utama</b> naik lima hari berturut-turut karena satu query di <code>log_aktivitas</code>.</p></div>';
  }
  function pageDashboard() {
    crumbs(['Dashboard']);
    var head = pageHead('Dashboard', 'Jumat, 30 Oktober 2026 · semua waktu WIB');
    if (!S.servers.length) {
      return head + '<div class="tiles tiles-3">' + nasTile() + '</div><div class="card" style="margin-top:14px"><div class="empty"><b>Belum ada container terdaftar</b><p>Tambahkan container pertama untuk mulai mengumpulkan backup. Alert tidak dinilai sampai ada container.</p>' + addServerBtn() + '</div></div>';
    }
    var act = activeServers(), bad = act.filter(function (s) { return srvLevel(s) !== 'ok'; }).length;
    var al = activeAlerts(), pn = pendingNotif();
    var rs = latestRestores(), lolos = rs.filter(function (r) { return r.ok; }).length;
    var gagal = rs.filter(function (r) { return !r.ok; }).map(function (r) { return r.server; });
    var h = head;
    if (pn) h += '<div class="banner banner-bad"><b>Telegram tidak terjangkau sejak 08:11</b> — ' + pn + ' notifikasi menunggu di antrean. <a href="#/laporan/notif" data-act="open-lap" data-v="notif">Lihat antrean ›</a></div>';
    h += '<div class="tiles">';
    h += nasTile();
    h += '<a class="tile' + (bad ? ' bad' : '') + '" href="#/container"><small>Container bermasalah</small><b>' + bad + ' / ' + act.length + '</b><span class="muted">' + (act.length - bad) + ' sehat</span></a>';
    h += '<a class="tile' + (gagal.length ? ' bad' : '') + '" href="#/laporan/rst" data-act="open-lap" data-v="rst"><small>Uji restore terakhir</small><b>' + lolos + ' / ' + rs.length + ' lolos</b><span class="muted">' + (gagal.length ? esc(gagal.join(', ')) + ' gagal' : 'semua container lolos') + '</span></a>';
    h += '<div class="tile' + (al.length ? ' bad' : '') + '"><small>Alert aktif</small><b>' + al.length + '</b><span class="muted">' + (pn ? pn + ' notifikasi tertunda' : 'semua notifikasi terkirim') + '</span></div>';
    h += '</div>';

    h += linimasaSection();

    h += '<div class="dash-2col"><div>';
    h += section('Alert aktif', '<span class="muted small">aturan biasa, bukan AI · dinilai tiap 60 detik · pengingat tiap 12 jam sampai pulih</span><a class="small" href="#/laporan/alert" data-act="open-lap" data-v="alert" style="margin-left:auto">Semua alert ›</a>');
    var arows = al.map(function (a) {
      var lv = alertLvl(a);
      var tag = a.fresh ? ' ' + pill('wait', 'container baru') : '';
      return '<tr class="row-' + lv + '"><td><b>' + a.cond + '</b>' + tag + '</td><td>' + srvLink(a) + '</td><td>' + a.since + '</td></tr>';
    });
    if (!arows.length) arows = ['<tr><td colspan="3"><div class="empty"><b>Tidak ada alert aktif</b>Semua kondisi normal.</div></td></tr>'];
    h += tbl(['Kondisi', 'Sumber', 'Sejak'], arows);
    h += '</div>';
    h += '<div>' + dashReportBox() + '</div>';
    return h + '</div>';
  }

  // ---------- Halaman: Server (daftar container per server) ----------
  // Daftar server disusun dari heartbeat agen. Container yang belum pernah mengirim heartbeat belum punya server.
  var NO_HOST = 'Belum terhubung';
  function hostGroups() {
    var g = {}, order = [];
    S.servers.forEach(function (s) {
      var k = s.host === '—' ? NO_HOST : s.host;
      if (!g[k]) { g[k] = { host: k, ip: s.ip, list: [] }; order.push(k); }
      g[k].list.push(s);
    });
    function worst(x) { return Math.min.apply(null, x.list.map(function (s) { return LV_ORDER[srvLevel(s)]; })); }
    return order.map(function (k) { return g[k]; }).sort(function (x, y) { return worst(x) - worst(y); });
  }
  function runnerCell(s) {
    var r = runnerOf(s), c = cadRunnerOf(s);
    var link = function (x) { return '<a href="#/runner/' + encodeURIComponent(x.name) + '">' + esc(x.name) + '</a>'; };
    if (!r && !c) return '<span class="muted">Belum ada runner</span>';
    if (!usesCad(s) || !c) return r ? link(r) : '<span class="muted">Belum ada runner</span>';
    if (r === c) return link(r);
    return 'Uji: ' + (r ? link(r) : '—') + '<span style="display:block">Cadangan: ' + link(c) + '</span>';
  }
  function pageServerList() {
    crumbs(['Container']);
    var groups = hostGroups(), hostN = groups.filter(function (g) { return g.host !== NO_HOST; }).length;
    var h = pageHead('Container <span class="muted" style="font-weight:400">(' + S.servers.length + ' container · ' + hostN + ' server)</span>', 'Satu container MariaDB = satu agen dan satu token.', addServerBtn());
    if (!S.servers.length) return h + '<div class="card"><div class="empty"><b>Belum ada container terdaftar</b>Tambahkan container pertama lewat tombol di atas.</div></div>';
    var rows = [];
    groups.forEach(function (g) {
      var bad = g.list.filter(function (s) { return srvLevel(s) === 'bad' || srvLevel(s) === 'warn'; }).length;
      rows.push('<tr class="grp-row"><td colspan="5"><b>' + esc(g.host) + '</b>' + (g.ip && g.ip !== '—' ? ' <span class="muted">· ' + g.ip + '</span>' : '') + ' <span class="muted">· ' + g.list.length + ' container' + (bad ? ' · ' + bad + ' bermasalah' : '') + '</span></td></tr>');
      sortByLevel(g.list).forEach(function (s) {
        var stat = srvPill(s);
        if ((s.status === 'wait-hb' || s.status === 'wait-backup') && S.wizard && S.wizard.server === s && !S.wizard.finished) stat += ' <a class="small" href="#/container/tambah">Lanjutkan verifikasi ›</a>';
        rows.push('<tr class="row-' + srvLevel(s) + '"><td><a href="#/container/' + encodeURIComponent(s.name) + '"><b>' + esc(s.name) + '</b></a>' + (s.promoted ? ' <span class="muted small">' + (s.promoted.manual ? 'dari restore manual ' : 'dari cadangan ') + esc(s.promoted.from) + '</span>' : '') + '</td><td>' + runnerCell(s) + '</td><td>' + s.reg + '</td><td>' + s.hb + '</td><td>' + stat + '</td></tr>');
      });
    });
    h += tbl(['Container', 'Runner', 'Didaftarkan', 'Heartbeat', 'Status'], rows);
    h += '<p class="small muted" style="margin-top:8px">Token tidak ditampilkan di aplikasi. Token hanya muncul sekali saat container ditambahkan.</p>';
    return h;
  }

  // ---------- Halaman: Detail container ----------
  var SRV_TABS = { ringkasan: 'Ringkasan', backup: 'Backup & log', kesehatan: 'Kesehatan', restore: 'Uji restore', pulih: 'Cadangan', manual: 'Restore manual' };
  function segLinks(items, cur, base) {
    return '<div class="seg">' + Object.keys(items).map(function (k) {
      return '<button data-act="go" data-href="' + base + k + '" class="' + (k === cur ? 'on' : '') + '">' + items[k] + '</button>';
    }).join('') + '</div>';
  }
  function pageServerDetail(name, tab, sub) {
    var s = findServer(name);
    if (!s) { crumbs([['Container', '#/container'], 'Tidak ditemukan']); return '<div class="empty"><b>Container “' + esc(name) + '” tidak ada</b><a href="#/container">Kembali ke daftar container</a></div>'; }
    if (tab === 'file' || tab === 'kesehatan' && sub === 'backup') tab = 'backup';
    tab = SRV_TABS[tab] ? tab : 'ringkasan';
    crumbs([['Container', '#/container'], esc(s.host === '—' ? NO_HOST : s.host), esc(s.name)]);
    var acts = s.status === 'off' || !canWrite() ? '' : '<details class="row-menu"><summary aria-label="Aksi untuk ' + esc(s.name) + '">⋮</summary><div class="menu-pop"><button class="danger" data-act="nonaktifkan" data-srv="' + esc(s.name) + '">Nonaktifkan</button></div></details>';
    var h = pageHead(esc(s.name) + ' &nbsp;' + srvPill(s), esc(s.host) + (s.ip !== '—' ? ' · ' + s.ip : '') + ' · didaftarkan ' + s.reg, acts);

    if (s.status === 'off') h += '<div class="banner banner-warn"><b>Nonaktif sejak ' + s.offSince + '.</b> Baris <code>' + esc(s.name) + '</code> dikomentari (<code>#</code>) di <code>config/agents.conf</code>, jadi agen ditolak (401), alert untuk container ini berhenti. File di NAS tidak dihapus dan tetap ikut aturan retensi.</div>';
    if (s.status === 'wait-hb' || s.status === 'wait-backup') {
      h += '<div class="banner banner-info"><b>' + (s.status === 'wait-hb' ? 'Menunggu heartbeat pertama.' : 'Agen terhubung, menunggu backup pertama.') + '</b> Container ini tampil sebagai alert sampai backup pertamanya masuk.' +
        (S.wizard && S.wizard.server === s && !S.wizard.finished ? ' <a href="#/container/tambah">Buka langkah verifikasi ›</a>' : '') + '</div>';
    }
    var al = alertsFor(s.name);
    if (s.promoted) h += '<div class="banner banner-warn"><b>' + (s.promoted.manual ? 'Dipulihkan lewat Restore manual dari ' : 'Dinaikkan ke production dari container cadangan ') + esc(s.promoted.from) + ', data sampai ' + esc(s.promoted.until) + '.</b> Admin membuka port ' + s.promoted.port + ' untuk IP aplikasi, mengarahkan aplikasi ke ' + esc(s.host) + ':' + s.promoted.port + ', dan memastikan ' + esc(s.promoted.from) + ' tidak lagi menerima tulisan.</div>';
    // Sesi yang masih aktif punya batas waktu, jadi jumlahnya terlihat dari tab mana pun.
    var nRm = S.rm.filter(function (x) { return x.source === s.name && rmActive(x); }).length;
    h += '<div class="filters">' + segLinks(Object.assign({}, SRV_TABS, { manual: SRV_TABS.manual + (nRm ? ' (' + nRm + ')' : '') }), tab, '#/container/' + encodeURIComponent(s.name) + '/') + '</div>';
    if (tab === 'ringkasan') return h + summaryTab(s, al);
    if (tab === 'backup') h += backupTab(s);
    if (tab === 'kesehatan') h += healthTab(s.name);
    if (tab === 'restore') h += testTab(s);
    if (tab === 'pulih') h += restoreTab(s);
    if (tab === 'manual') h += manualTab(s, sub);
    return h;
  }
  function srvAlertTable(s, al) {
    if (s.status !== 'active' || !al.length) return '';
    return section('Alert aktif (' + al.length + ')') + tbl(['Kondisi', 'Jenis', 'Sejak'], al.map(function (a) {
      return '<tr class="row-' + alertLvl(a) + '"><td><b>' + a.cond + '</b></td><td>' + a.kind + '</td><td>' + a.since + '</td></tr>';
    }));
  }

  // Ringkasan AI per server, dari laporan harian 07:01. Server tanpa catatan tidak diberi kotak AI.
  var SRV_AI = {
    'lab-db': '<p><b>Full backup tidak masuk sejak 29 Okt</b> karena disk /backup penuh sesaat (Errcode 28), dan uji restore 27 Okt ikut gagal. Disk /backup naik 2,1% per hari dan penuh sekitar <b>12 Nov</b>. Binlog <b>000790 hilang</b> sejak 23 Okt, jadi point-in-time dari full terakhir yang lolos (22 Okt) berhenti sebelum nomor itu. Dump harian masih lolos, jadi restore per database tetap bisa.</p><p><b>Langkah:</b> tambah ruang /backup atau aktifkan retensi, lalu jalankan ulang full backup dan uji restore.</p>',
    'simrs-utama': '<p>Backup dan uji restore normal. Masalahnya di <b>beban</b>: koneksi puncak 94% dari <code>max_connections</code> dan 37 koneksi ditolak kemarin, bersamaan dengan lonjakan slow query sekitar 10:00 dari satu query di <code>log_aktivitas</code>.</p><p><b>Langkah:</b> tambahkan index yang disarankan di laporan harian sebelum menaikkan <code>max_connections</code>.</p>',
    'siak-db': '<p>Full dan dump <code>siakdb</code> normal. <b>Dump _users 30 Okt gagal terkirim</b> karena file kosong (HTTP 422), jadi dump itu tidak ada di NAS. Agen juga mengirim ulang dump 29 Okt dengan checksum berbeda; kedua versi tersimpan di NAS.</p><p><b>Langkah:</b> periksa log backup, jalankan ulang dump _users, lalu tinjau checksum di tab Backup &amp; log.</p>',
    'kepegawaian-db': '<p>Baru terdaftar 29 Okt; backup pertama dan uji restore lolos. Daftar query terberat belum ada karena <code>performance_schema</code> di container ini nonaktif.</p>'
  };
  function tile(href, title, val, lvl, sub, sp) {
    return '<a class="tile ' + (lvl || '') + '" href="' + href + '"><small>' + title + '</small><b>' + val + '</b><span class="muted">' + (sub || '') + '</span>' +
      (sp ? spark(sp, lvl === 'bad' ? C.bad : lvl === 'warn' ? C.warn : C.main) : '') + '</a>';
  }
  function summaryTab(s, al) {
    var base = '#/container/' + encodeURIComponent(s.name) + '/', m = D.metrics[s.name], live = s.status === 'active';
    var has = function (k) { return al.some(function (a) { return a.kind === k; }); };
    var h = '';
    h += srvAlertTable(s, al);
    // Fakta dulu, tafsiran AI sesudahnya. Teksnya dari laporan 07:01, jadi bisa tertinggal dari kondisi saat ini.
    if (live && SRV_AI[s.name] && S.sim !== 'sehat') {
      h += '<div style="margin-top:14px">' + (S.aiDown ? '<div class="aifail">Ringkasan AI tidak tersedia hari ini. Alert dan angka di halaman ini tetap lengkap.</div>' : '<div class="aibox"><b>Ringkasan laporan 07:00</b>' + aiLabel() + SRV_AI[s.name] + '</div>') + '</div>';
    }

    h += section('Status backup', '<a class="small" href="' + base + 'backup">Backup &amp; log ›</a>');
    var bv = binlogView(s), uv = uploadView(s);
    h += '<div class="tiles tiles-3">' +
      tile(base + 'backup', 'Full terakhir', s.files.full ? s.full : '—', has('Backup') ? 'bad' : '', s.files.full + ' file tersimpan') +
      tile(base + 'backup', 'Dump terlama', s.files.dump ? s.dump : '—', '', 'dari ' + s.dbs.length + ' database · umur sejak dibuat') +
      tile(base + 'backup', 'Rantai binlog', bv.val, bv.lvl, bv.sub) +
      tile(base + 'backup', 'Antrean kirim', uv.val, uv.lvl, has('Agen') ? 'data terakhir sebelum agen berhenti' : uv.sub) +
      tile(base + 'backup', 'Heartbeat agen', s.hb, has('Agen') ? 'bad' : '', has('Agen') ? 'agen tidak merespons' : 'batas 10 menit') +
      tile(base + 'restore', 'Uji restore', s.restore ? (s.restore.ok ? 'Lolos' : 'Gagal') : '—', s.restore && !s.restore.ok ? 'bad' : '', s.restore ? s.restore.date : 'belum pernah') +
      '</div>';

    h += section('Kesehatan · 14 hari', (m && m.stale ? '<span class="muted small">data berhenti 08:01 · </span>' : '') + '<a class="small" href="' + base + 'kesehatan">Semua metrik ›</a>');
    if (!live || !m) {
      h += '<div class="card"><div class="empty"><b>Belum ada metrik</b>Metrik pertama masuk dalam 1 jam setelah user monitor dibuat; ukuran tabel besok pagi.</div></div>';
    } else {
      var cp = Math.round(m.conn[0] / m.maxConn * 100), up = m.slow[0] > m.slowAvg * 1.5, f = forecastFull(m.diskBackup[0], m.diskBackup[1]);
      var drop = Math.round((1 - m.fullSize[0] / m.fullSize[1]) * 100);
      h += '<div class="tiles">' +
        tile(base + 'kesehatan/beban', 'Koneksi puncak', m.conn[0] + ' / ' + m.maxConn, cp >= 90 ? 'bad' : '', cp + '% dari max_connections', gen(s.name + 'conn', 14, m.conn[0], m.conn[1], m.conn[2], 0, m.maxConn)) +
        tile(base + 'kesehatan/performa', 'Slow query/hari', num(m.slow[0]), up ? 'warn' : '', 'rata-rata 30 hari ' + num(m.slowAvg), gen(s.name + 'slow', 14, m.slow[0], m.slow[1], m.slow[2], 0)) +
        tile(base + 'kesehatan/disk', 'Disk /backup', m.diskBackup[0] + '%', f.days <= 30 ? 'bad' : '', 'penuh ' + f.label, gen(s.name + 'Disk /backup', 14, m.diskBackup[0], m.diskBackup[1], 0.4, 0, 100)) +
        tile(base + 'backup', 'Ukuran full backup', num(m.fullSize[0], 1) + ' GB', drop > 20 ? 'warn' : '', drop > 20 ? '−' + drop + '% dari rata-rata' : 'sesuai rata-rata', m.fullDrop ? [26.1, 26.4, 26.8, 26.5, 27, 18.4] : gen(s.name + 'fs', 14, m.fullSize[1], m.fullSize[1] * 0.002, m.fullSize[1] * 0.02, 0)) +
        '</div>';
    }

    h += section('Info container');
    h += '<div class="kv">' +
      '<div><small>Database</small>' + (s.dbs.length ? s.dbs.map(esc).join(', ') : '—') + '</div>' +
      '<div><small>Ukuran data</small>' + (m ? num(m.dbSize[0], 1) + ' GB · +' + num(m.dbSize[1] * 7, 1) + ' GB/minggu' : '—') + '</div>' +
      '<div><small>Folder NAS</small><code>/lumbungdb/' + esc(s.name) + '/</code></div>' +
      '<div><small>Server</small>' + esc(s.host === '—' ? NO_HOST : s.host) + '</div>' +
      '<div><small>Versi MariaDB</small>' + esc(s.ver || '—') + '</div>' +
      '<div><small>Runner uji</small>' + (runnerOf(s) ? '<a href="' + base + 'restore">' + esc(s.runner) + '</a>' : '<span class="muted">Belum ada runner</span>') + '</div>' +
      '<div><small>Runner cadangan</small>' + (cadRunnerOf(s) ? '<a href="' + base + 'pulih">' + esc(s.runnerCad) + '</a>' : '<span class="muted">Belum ada runner</span>') + '</div>' +
      '<div><small>Database cadangan</small><a href="' + base + 'pulih">' + (s.standby ? 'data sampai ' + esc(s.standby.until) : s.sched && s.sched.standby.on ? 'Menunggu restore pertama' : 'Tidak disiapkan') + '</a></div>' +
      '<div><small>Agen</small>' + agentLabel(s) + '</div>' +
      '<div><small>Jadwal full</small><a href="' + base + 'backup">' + schedShort(s, 'full') + '</a></div>' +
      '<div><small>Jadwal dump</small><a href="' + base + 'backup">' + schedShort(s, 'dump') + '</a></div>' +
      '<div><small>Arsip binlog</small><a href="' + base + 'backup">' + schedShort(s, 'binlog') + '</a></div>' +
      '</div>';
    return h;
  }

  function uploadSection(s) {
    if (!s.upload || s.status === 'off') return '';
    var u = s.upload, v = uploadView(s), rows = [];
    rows.push('<tr' + (v.lvl ? ' class="row-bad"' : '') + '><td>Antrean kirim</td><td>' + v.val + (nasDown() ? ' · ' + v.sub : '') + '</td></tr>');
    rows.push('<tr><td>Upload terakhir berhasil</td><td>' + (u.lastOk || '—') + '</td></tr>');
    rows.push(u.err ? '<tr class="row-bad"><td>Error terakhir</td><td><b>' + u.err.code + ' · ' + u.err.msg + '</b><br><span class="mono">' + esc(u.err.file) + '</span><br><span class="muted small">gagal sejak ' + u.err.since + ' · ' + u.err.tries + ' percobaan · file lain tetap dikirim</span></td></tr>' : '<tr><td>Error terakhir</td><td>Tidak ada</td></tr>');
    return section('Pengiriman agen', '<span class="muted small">dari heartbeat · alert bila satu file gagal terkirim lebih dari 2 jam</span>') + tbl(['Pemeriksaan', 'Hasil'], rows);
  }
  // Tiap database dinilai sendiri; _users berisi akun, jadi tidak dihitung sebagai database.
  function dumpSection(s) {
    if (!s.dumps || !s.dumps.length) return '';
    var ST = { ok: pill('ok', 'Normal'), warn: pill('warn', 'Perlu diperhatikan'), bad: pill('bad', 'Terlambat'), off: pill('off', 'Container nonaktif') };
    var dumpLimit = thresholdText(s, 'dump');
    return section('Dump per database', '<span class="muted small">umur dihitung dari waktu pembuatan backup · ' + (dumpLimit === 'Tidak dinilai' ? 'dump dimatikan, umur tidak dinilai' : 'alert bila satu database lebih dari ' + dumpLimit) + '</span>') +
      tbl(['Database', 'Umur dump terakhir', 'Status'], s.dumps.map(function (d) {
        var name = d[0] === '_users' ? '<code>_users</code> <span class="muted small">akun dan hak akses</span>' : esc(d[0]);
        return '<tr' + (d[2] === 'warn' || d[2] === 'bad' ? ' class="row-' + d[2] + '"' : '') + '><td>' + name + '</td><td>' + esc(d[1]) + '</td><td>' + ST[d[2]] + (d[3] ? ' <span class="muted small">' + esc(d[3]) + '</span>' : '') + '</td></tr>';
      }));
  }
  function backupTab(s) {
    var m = D.metrics[s.name];
    var h = '<div class="kv kv-3">' +
      '<div><small>File diterima</small>' + s.files.full + ' full · ' + s.files.dump + ' dump · ' + s.files.binlog + ' binlog</div>' +
      '<div><small>Disk /backup</small>' + (s.disk == null ? '—' : s.disk + '% terpakai') + '</div>' +
      '<div><small>Folder NAS</small><code>/lumbungdb/' + esc(s.name) + '/</code></div>' +
      '</div>';
    h += backupScheduleSection(s) + backupRequestSection(s);
    if (s.status === 'active' && m) {
      var checks = '<div>' + tbl(['Pemeriksaan', 'Hasil'], [
        '<tr><td>Umur full terakhir</td><td>' + s.full + '</td></tr>',
        '<tr class="row-' + (s.binlogGap ? 'bad' : 'ok') + '"><td>Rantai binlog</td><td>' + (s.binlogGap ? '<b>1 nomor hilang: <code>' + s.binlogGap.no + '</code> (' + s.binlogGap.day + ')</b><br><span class="muted small">Point-in-time dari full terakhir yang lolos uji (' + s.binlogGap.from + ') berhenti sebelum nomor ini.</span>' : 'Urutan lengkap sejak full terakhir yang lolos uji') + '</td></tr>',
        '<tr><td>Uji restore terakhir</td><td>' + (s.restore ? (s.restore.ok ? 'Lolos' : '<b style="color:var(--bad)">Gagal</b>') + ' · ' + s.restore.date : '—') + '</td></tr>'
      ]) + '</div>';
      var n = S.hRange, labels = dayLabels(n);
      var fs = gen(s.name + 'fs', n, m.fullSize[1], m.fullSize[1] * 0.002, m.fullSize[1] * 0.02, 0);
      // Hari tanpa file full tidak punya batang; batang terakhir sama dengan angka di judul kartu.
      if (m.fullDrop) { fs[n - 1] = null; fs[n - 2] = null; fs[n - 3] = m.fullSize[0]; } else fs[n - 1] = m.fullSize[0];
      var avgF = []; for (var j = 0; j < n; j++) avgF.push(m.fullSize[1]);
      var drop = Math.round((1 - m.fullSize[0] / m.fullSize[1]) * 100);
      h += section('Pemeriksaan backup', rangeSelect());
      h += '<div class="grid2">' + mcard('Ukuran full terakhir', num(m.fullSize[0], 1) + ' GB', drop > 20 ? 'bad' : '', drop > 20 ? '−' + drop + '% dari rata-rata ' + num(m.fullSize[1], 1) + ' GB' : 'sesuai rata-rata',
        chart({ title: 'ukuran full backup', labels: labels, floor: 0, min: 0, fmt: function (v) { return num(v, v < 10 ? 1 : 0) + ' GB'; }, series: [{ values: fs, bars: 1, color: C.main, colorAt: function (v) { return v < m.fullSize[1] * 0.8 ? C.bad : C.main; } }, { values: avgF, color: C.alt, width: 1.2, dots: false }] })) + checks + '</div>';
    }
    h += dumpSection(s);
    h += uploadSection(s);
    h += riwSection(s);
    var integ = S.integrity.filter(function (it) { return it.server === s.name; });
    if (integ.length) {
      h += section('Checksum berbeda untuk nama yang sama', '<span class="muted small">file di NAS tidak pernah ditimpa; kedua versi disimpan</span>');
      h += tbl(['File', 'Versi pertama', 'Versi baru', 'Tindakan'], integ.map(function (it) {
        return '<tr class="row-' + (it.reviewed ? 'ok' : 'warn') + '"><td class="mono">' + it.file + '</td><td>' + it.stored + '<br><span class="muted small">' + it.storedAt + '</span></td><td>' + it.resent + '<br><span class="muted small">' + it.resentAt + '</span></td>' +
          '<td><label class="check"><input type="checkbox" data-in="integ" data-i="' + S.integrity.indexOf(it) + '"' + (it.reviewed ? ' checked' : '') + (canWrite() ? '' : ' disabled') + '> ' + (it.reviewed ? 'Ditinjau ' + it.reviewed : 'Sudah ditinjau') + '</label></td></tr>';
      }));
    }
    if (s.log) {
      h += '<details class="box"' + (alertsFor(s.name).some(function (a) { return a.key === 'full-lab' || a.kind === 'Upload'; }) ? ' open' : '') + '><summary>20 baris terakhir log backup (dari heartbeat)</summary><pre class="log">' +
        s.log.split('\n').map(function (l) { return l[0] === '!' ? '<span class="err">' + esc(l.slice(1)) + '</span>' : esc(l); }).join('\n') + '</pre></details>';
    }
    return h;
  }
  // Satu giliran uji: full terbaru, dump terbaru tiap database, lalu cek rantai binlog (tanpa replay).
  // Item { g } adalah judul kelompok; item lain langkah yang dihitung.
  var FULL_FAIL_AT = 2; // langkah --prepare
  function restoreSteps(s, full, binlog, scope) {
    var sc = scope || { full: true, dbs: null }, dbs = sc.dbs || s.dbs, st = [];
    if (sc.full) st.push({ g: 'Full · ' + full });
    (sc.full ? ['Unduh file dari LumbungDB lewat HTTPS', 'Cek SHA256 cocok dengan metadata', 'Ekstrak xbstream + mariadb-backup --prepare', 'Jalankan container sementara dari hasil prepare', 'Database terbuka, hitung tabel', 'Tabel utama terbaca (SELECT COUNT)'] : [])
      .forEach(function (t) { st.push({ k: 'full', t: t }); });
    st.push({ g: 'Dump terbaru · ' + dbs.length + ' database + _users' });
    dbs.forEach(function (db) {
      st.push({ k: 'dump', db: db, imp: 1, t: 'Unduh, cek SHA256, impor ' + db + '.sql.gz ke container kosong' });
      st.push({ k: 'dump', db: db, t: db + ': jumlah tabel sama dengan hasil full, tabel utama terbaca', tf: db + ': tabel utama terbaca (pembanding jumlah tabel dari full tidak tersedia)' });
    });
    st.push({ k: 'dump', t: 'Unduh, cek SHA256, impor _users.sql.gz ke container kosong, akun terbaca' });
    st.push({ g: 'Binlog · ' + binlog });
    st.push({ k: 'binlog', gapStep: 1, t: 'Nomor file berurutan sejak full terakhir yang lolos uji' });
    st.push({ k: 'binlog', t: 'Checksum tiap file cocok' });
    st.push({ g: 'Penutup' });
    st.push({ k: 'end', t: 'Hapus container sementara dan data ekstrak' });
    return st;
  }
  // cur = langkah yang sedang berjalan (Infinity bila selesai). o: failFull, failNote, gap, failDb, dumpNote.
  // Satu jenis yang gagal tidak menghentikan jenis lain, supaya tetap diketahui jalur restore yang masih bisa dipakai.
  function stepsHtml(steps, cur, o) {
    var i = -1;
    return '<ul class="steps-list">' + steps.map(function (st) {
      if (st.g) return '<li class="grp">' + esc(st.g) + '</li>';
      i++;
      var cls, t = o.failFull && st.tf ? st.tf : st.t, extra = '';
      if (o.failFull && st.k === 'full' && i === FULL_FAIL_AT) { cls = 'bad'; t = 'mariadb-backup --prepare gagal'; extra = o.failNote ? '<pre>' + o.failNote + '</pre>' : ''; }
      else if (o.failFull && st.k === 'full' && i > FULL_FAIL_AT) { cls = 'skip'; }
      else if (o.failDb && st.db === o.failDb && st.imp) { cls = 'bad'; t = 'Impor ' + st.db + '.sql.gz gagal'; extra = o.dumpNote ? '<pre>' + esc(o.dumpNote) + '</pre>' : ''; }
      else if (o.failDb && st.db === o.failDb) { cls = 'skip'; }
      else if (o.gap && st.gapStep && i < cur) { cls = 'bad'; t = 'Nomor file melompat: ' + o.gap + ' hilang'; }
      else cls = i < cur ? 'ok' : i === cur ? 'run' : 'todo';
      return '<li class="' + cls + '">' + esc(t) + extra + '</li>';
    }).join('') + '</ul>';
  }
  function kindPills(r, nd) {
    if (r.scope && r.scope.dbs) nd = r.scope.dbs.length;
    var full = r.scope && !r.scope.full ? '<span class="muted">tidak diuji</span>' : r.fail === 'full' ? pill('bad', 'Gagal') : pill('ok', 'Lolos');
    var dump = r.fail === 'dump' ? pill('bad', (nd - 1) + ' / ' + nd + ' lolos') : pill('ok', nd + ' / ' + nd + ' lolos');
    var bin = r.binlogGap ? pill('bad', 'Rantai putus · ' + r.binlogGap) : pill('ok', 'Rantai lengkap');
    return { full: full, dump: dump, bin: bin };
  }
  function restoreTab(s) {
    return cadSection(s) + restoreLogSection(s);
  }
  function testTab(s) {
    return schedSection(s) + testSection(s);
  }
  function canTest(s) {
    return !S.restoreRun && !S.standbyRun && s.status === 'active' && (s.files.full || s.files.dump) && !nasDown() && canWrite() && runnerFor(s, runnerOf(s));
  }
  function testSection(s) {
    var run = S.restoreRun, list = S.restores.filter(function (r) { return r.server === s.name; }), rn = runnerOf(s);
    var h = '<div style="margin-top:14px"></div>';
    if (run && run.server === s.name) {
      h += '<div class="card card-pad" style="margin-bottom:14px"><b>Sedang berjalan</b>' +
        stepsHtml(run.steps, run.step, { failFull: run.fail && run.step >= FULL_FAIL_AT, gap: run.gap }) + '</div>';
    } else if (run) {
      h += '<p class="small muted">Uji restore ' + esc(run.server) + ' sedang berjalan di ' + esc(run.runner) + '. Tiap runner mengerjakan satu tugas; prototype mensimulasikan satu uji sekaligus.</p>';
    }
    if (!list.length) return h + '<div class="card"><div class="empty"><b>Belum pernah diuji</b>Uji berjalan mengikuti jadwal di atas setelah backup pertamanya diterima.</div></div>';
    // Uji yang tidak terlaksana tidak punya langkah; yang ditampilkan adalah uji terakhir yang benar-benar berjalan.
    var skipped = list[0].skip ? list[0] : null;
    if (skipped) h += '<div class="banner banner-warn"><b>Uji ' + esc(skipped.when) + ' tidak terlaksana di ' + esc(skipped.runner) + '.</b> ' + esc(skipped.reason) + '. Masalahnya di sisi runner, jadi backup tidak dicap gagal. Retensi juga tidak menghitungnya sebagai lolos.</div>';
    var firstRun = 0; while (firstRun < list.length && list[firstRun].skip) firstRun++;
    if (firstRun >= list.length) return h + '<div class="card"><div class="empty"><b>Belum ada uji yang selesai</b>Uji berikutnya berjalan setelah masalah runner selesai.</div></div>';
    var idx = S.restoreSel && S.restoreSel.server === s.name ? S.restoreSel.i : firstRun, last = list[idx] && !list[idx].skip ? list[idx] : list[firstRun];
    var failFull = last.fail === 'full', nd = s.dbs.length, kp = kindPills(last, nd);
    var note = failFull && s.name === 'lab-db' && last.when === '27 Okt 03:30' ?
      '[ERROR] InnoDB: Read redo log up to LSN=8812734412\n[ERROR] mariadb-backup: xtrabackup_logfile is incomplete\nKemungkinan: full backup 27 Okt terpotong saat disk /backup penuh' : '';
    h += section(last === list[firstRun] ? 'Percobaan terakhir' : 'Percobaan ' + esc(last.when));
    h += '<div class="card card-pad"><div style="margin-bottom:8px">' + esc(last.when) + ' · ' + esc(last.dur) + (last.runner ? ' · ' + esc(last.runner) : '') + ' &nbsp;' + (last.ok ? pill('ok', 'Lolos') : pill('bad', 'Gagal')) + '</div>' +
      '<div class="restore-kinds"><span>Full ' + kp.full + '</span><span>Dump ' + kp.dump + '</span><span>Binlog ' + kp.bin + '</span></div>' +
      stepsHtml(restoreSteps(s, last.full, last.binlog, last.scope), Infinity, { failFull: failFull, failNote: note, gap: last.binlogGap, failDb: last.failDb, dumpNote: last.dumpNote }) + '</div>';
    if (failFull) h += '<p class="small" style="margin-top:8px">Dump terbaru lolos, jadi database masih bisa dipulihkan manual dari dump selama full belum pulih. Retensi menyimpan full terakhir yang lolos uji.</p>';
    if (last.fail === 'dump') h += '<p class="small" style="margin-top:8px">Full dan binlog lolos, jadi seluruh container masih bisa dipulihkan. Database <code>' + esc(last.failDb) + '</code> belum bisa dipulihkan dari dump terbaru.</p>';
    if (last.binlogGap) h += '<p class="small" style="margin-top:8px">Binlog ' + last.binlogGap + ' hilang, jadi point-in-time dari full terakhir yang lolos uji berhenti sebelum nomor itu. Alert pulih bila file itu masuk atau ada full yang lebih baru lolos uji.</p>';
    h += section('Riwayat uji');
    h += tbl(['Waktu', 'Full backup', 'Full', 'Dump', 'Binlog', ['Durasi', 'num'], ''], list.map(function (r, i) {
      if (r.skip) return skipRow(r, esc(r.when), '<td class="mono">' + esc(r.full) + '</td>', 3, '');
      var p = kindPills(r, nd), on = r === last;
      return '<tr class="row-' + (r.ok ? 'ok' : 'bad') + '"><td>' + esc(r.when) + '</td><td class="mono">' + esc(r.full) + '</td><td>' + p.full + '</td><td>' + p.dump + '</td><td>' + p.bin + '</td><td class="num">' + esc(r.dur) + '</td><td class="num">' +
        (on ? '<span class="muted small">ditampilkan</span>' : '<button class="btn btn-sm" data-act="restore-sel" data-srv="' + esc(s.name) + '" data-i="' + i + '">Lihat</button>') + '</td></tr>';
    }));
    h += '<p class="small muted" style="margin-top:8px">Jumlah tabel dan baris disimpan sebagai angka saja, tanpa isi tabel.</p>';
    return h;
  }
  function restoreAllTab() {
    if (!S.restores.length) return '<div class="card"><div class="empty"><b>Belum ada uji restore</b>Uji berjalan mengikuti jadwal tiap container.</div></div>';
    return tbl(['Waktu', 'Container', 'Runner', 'Full', 'Dump', 'Binlog', ['Durasi', 'num']], S.restores.map(function (r) {
      var s = findServer(r.server), p = kindPills(r, s ? s.dbs.length : 1), link = '<td><a href="#/container/' + encodeURIComponent(r.server) + '/restore">' + esc(r.server) + '</a></td><td>' + esc(r.runner || '—') + '</td>';
      if (r.skip) return skipRow(r, esc(r.when), link, 3);
      return '<tr class="row-' + (r.ok ? 'ok' : 'bad') + '"><td>' + esc(r.when) + '</td>' + link + '<td>' + p.full + '</td><td>' + p.dump + '</td><td>' + p.bin + '</td><td class="num">' + esc(r.dur) + '</td></tr>';
    })) + '<p class="small muted" style="margin-top:8px">Mengikuti jadwal tiap container. Bila waktunya bersamaan, tugas antre karena satu runner mengerjakan satu tugas.</p>';
  }

  // ---------- Jadwal dan permintaan backup ----------
  // Cron di container yang menjalankan backup. Dashboard menampilkan jadwal yang dilaporkan agen, bukan nilai yang diminta.
  var BS_KINDS = [['full', 'Full'], ['dump', 'Dump'], ['binlog', 'Arsip binlog']];
  var DAY_NAMES = ['Minggu', 'Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu'];
  var MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
  var SRC_LABEL = { bawaan: 'Bawaan', env: 'Dari env container' };
  var REQ_PILL = { 'Menunggu': 'wait', 'Berjalan': 'wait', 'Mengirim': 'wait', 'Selesai': 'ok', 'Gagal': 'bad', 'Tidak selesai': 'bad', 'Ditolak': 'warn', 'Kedaluwarsa': 'off', 'Sudah berjalan': 'off', 'Dibatalkan': 'off' };
  var REQ_OPEN = { 'Menunggu': 1, 'Berjalan': 1, 'Mengirim': 1 };

  function p2(n) { return String(n).padStart(2, '0'); }
  // Agen yang berhenti tidak mengambil jadwal atau permintaan; keduanya menunggu sampai agen kembali.
  function agentAlive(s) {
    return (s.status === 'active' || s.status === 'wait-backup') && !S.alerts.some(function (a) { return a.kind === 'Agen' && a.server === s.name && !S.resolved[a.key]; });
  }
  function agentLabel(s) {
    if (!s.agent) return '—';
    return 'Di dalam image · <code>' + esc(s.agent.tag) + '</code>';
  }

  function cronField(f, min, max) {
    var set = {};
    var ok = f.split(',').every(function (part) {
      var m = /^(\*|\d+(?:-\d+)?)(?:\/(\d+))?$/.exec(part);
      if (!m) return false;
      var lo, hi, st = m[2] ? +m[2] : 1;
      if (m[1] === '*') { lo = min; hi = max; }
      else { var r = m[1].split('-'); lo = +r[0]; hi = r[1] !== undefined ? +r[1] : (m[2] ? max : lo); }
      if (lo < min || hi > max || lo > hi || st < 1) return false;
      for (var i = lo; i <= hi; i += st) set[i] = 1;
      return true;
    });
    return ok ? set : null;
  }
  var CRON_CACHE = {};
  // Waktu jalan selama 62 hari, cukup untuk menilai jadwal bulanan. Kosong berarti cron tidak valid atau tidak pernah jalan.
  function cronRuns(c) {
    if (CRON_CACHE[c]) return CRON_CACHE[c];
    var p = String(c).trim().split(/\s+/), runs = [];
    var parts = p.length === 5 && /^[0-9*\/,\- ]+$/.test(c) ? [cronField(p[0], 0, 59), cronField(p[1], 0, 23), cronField(p[2], 1, 31), cronField(p[3], 1, 12), cronField(p[4], 0, 7)] : [null];
    if (parts.every(Boolean)) {
      var domAny = p[2] === '*', dowAny = p[4] === '*', d = new Date(2026, 9, 30, 0, 0, 0);
      if (parts[4][7]) parts[4][0] = 1;
      for (var day = 0; day < 62; day++) {
        var dom = !!parts[2][d.getDate()], dow = !!parts[4][d.getDay()];
        if (parts[3][d.getMonth() + 1] && (domAny && dowAny || domAny && dow || dowAny && dom || !domAny && !dowAny && (dom || dow))) {
          Object.keys(parts[1]).map(Number).sort(function (a, b) { return a - b; }).forEach(function (hh) {
            Object.keys(parts[0]).map(Number).sort(function (a, b) { return a - b; }).forEach(function (mm) {
              runs.push(new Date(d.getFullYear(), d.getMonth(), d.getDate(), hh, mm).getTime());
            });
          });
        }
        d.setDate(d.getDate() + 1);
      }
    }
    CRON_CACHE[c] = runs;
    return runs;
  }
  function cronValid(c) { return cronRuns(c).length >= 2; }
  function cronGapHours(c) {
    var runs = cronRuns(c), gap = 0;
    for (var i = 1; i < runs.length; i++) gap = Math.max(gap, runs[i] - runs[i - 1]);
    return gap / 3600000;
  }
  function cronNext(c) {
    var now = S.clock.getTime(), runs = cronRuns(c);
    for (var i = 0; i < runs.length; i++) if (runs[i] > now) return runs[i];
    return null;
  }
  function cronNextLabel(c) {
    var t = cronNext(c);
    if (!t) return '—';
    var d = new Date(t);
    return d.getDate() + ' ' + MONTH_NAMES[d.getMonth()] + ' ' + hhmm(d);
  }
  function cronLabel(c) {
    var m;
    if (c === 'off') return 'Mati';
    if ((m = /^(\d+) (\d+) \* \* \*$/.exec(c))) return 'Tiap hari ' + p2(m[2]) + ':' + p2(m[1]);
    if ((m = /^(\d+) (\d+) \* \* ([0-7])$/.exec(c))) return 'Tiap ' + DAY_NAMES[+m[3] % 7] + ' ' + p2(m[2]) + ':' + p2(m[1]);
    if ((m = /^0 \*\/(\d+) \* \* \*$/.exec(c))) return 'Tiap ' + m[1] + ' jam';
    if (c === '0 * * * *') return 'Tiap 1 jam';
    if ((m = /^\*\/(\d+) \* \* \* \*$/.exec(c))) return 'Tiap ' + m[1] + ' menit';
    return 'Kustom: ' + c;
  }
  function hoursText(h) {
    if (h < 1) return num(h * 60) + ' menit';
    return num(h, h % 1 ? 1 : 0) + ' jam';
  }
  function cronThreshold(c) { return hoursText(cronGapHours(c) * 1.5); }
  function thresholdText(s, k) {
    var b = s.bsched;
    if (k === 'binlog') return 'Rantai binlog, bukan umur file';
    if (!b) return '36 jam';
    return b[k] === 'off' ? 'Tidak dinilai' : cronThreshold(b[k]);
  }
  function schedShort(s, k) {
    if (s.bsched) return esc(cronLabel(s.bsched[k]));
    return 'Belum dilaporkan';
  }
  function seedSched(name) {
    var r = seeded(name);
    return { src: 'bawaan', full: Math.floor(r() * 60) + ' ' + Math.floor(r() * 2) + ' * * *', dump: Math.floor(r() * 60) + ' ' + (3 + Math.floor(r() * 2)) + ' * * *', binlog: '*/15 * * * *', ver: 0, applied: 0 };
  }
  function logControl(s, act, detail) {
    S.ctrlLog = [{ server: s.name, at: '30 Okt ' + nowLabel(), act: act, detail: detail, by: 'admin' }].concat(S.ctrlLog);
  }

  function reqKindLabel(r) {
    if (r.kind === 'full') return 'Full';
    if (r.kind === 'binlog') return 'Arsip binlog';
    return r.db ? 'Dump ' + r.db : 'Dump semua database';
  }
  function reqsOf(s) { return S.reqs.filter(function (r) { return r.server === s.name; }); }
  function reqActive(s, kind) { return reqsOf(s).some(function (r) { return r.kind === kind && REQ_OPEN[r.status]; }); }
  function backupControlAlerts() {
    var list = [];
    S.servers.forEach(function (s) {
      if (s.status === 'off' || !s.bsched) return;
      var b = s.bsched;
      if (b.err) list.push({ key: 'jadwal-err-' + s.name, kind: 'Jadwal', short: 'Jadwal ditolak agen', cond: 'Agen menolak jadwal: ' + esc(b.err), server: s.name, since: b.errAt, notif: 'Terkirim' });
      if (b.full === 'off') list.push({ key: 'jadwal-off-' + s.name, kind: 'Jadwal', short: 'Jadwal full mati', cond: 'Jadwal full dimatikan', server: s.name, since: b.offAt || '—', notif: 'Terkirim' });
      ['full', 'dump', 'binlog'].forEach(function (k) {
        // Daftar terbaru dulu: yang pertama berhasil atau gagal menentukan alert. Status lain tidak menunjukkan hasil backup.
        var last = reqsOf(s).filter(function (r) { return r.kind === k && (r.status === 'Selesai' || r.status === 'Gagal' || r.status === 'Tidak selesai'); })[0];
        if (last && last.status !== 'Selesai') list.push({ key: 'req-' + last.id, kind: 'Permintaan', short: 'Permintaan backup ' + last.status.toLowerCase(), cond: 'Permintaan ' + esc(reqKindLabel(last)) + ' ' + last.status.toLowerCase() + (last.note ? ': ' + esc(last.note) : ''), server: s.name, since: last.at, notif: 'Terkirim' });
      });
    });
    return list;
  }

  function backupNowBtn(s, cls) {
    if (s.status !== 'active') return '';
    var blocked = !canWrite() || nasDown();
    return '<button class="btn ' + (cls || '') + '" data-act="req-open" data-srv="' + esc(s.name) + '"' + (blocked ? ' disabled title="' + (canWrite() ? 'NAS bermasalah, hasil backup tidak bisa sampai ke NAS' : 'Butuh login dashboard') + '"' : '') + '>Backup sekarang</button>';
  }
  function backupScheduleSection(s) {
    var b = s.bsched, h, enc = esc(s.name);
    if (!b) return section('Jadwal backup') + '<div class="card"><div class="empty"><b>Belum ada laporan jadwal</b>Jadwal tampil setelah heartbeat pertama. Agen memasang jadwal bawaan saat container start.</div></div>';
    var can = canWrite() && s.status === 'active';
    var acts = '<span style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;align-items:center">' + (SRC_LABEL[b.src] ? pill('off', SRC_LABEL[b.src]) : '') +
      '<button class="btn btn-sm" data-act="bs-edit" data-srv="' + enc + '"' + (can ? '' : ' disabled') + '>Ubah jadwal</button>' + backupNowBtn(s, 'btn-sm') + '</span>';
    h = '<div class="section-title" style="flex-wrap:wrap"><h2>Jadwal backup</h2>' + acts + '</div>';
    if (b.err) h += '<div class="banner banner-bad"><b>Agen menolak jadwal versi ' + b.ver + '.</b> ' + esc(b.err) + '. Crontab lama tetap dipakai.</div>';
    else if (b.want) {
      h += '<div class="banner banner-info">' + (agentAlive(s) ? '<span class="spin"></span> ' : '') + '<b>Menunggu diterapkan: versi ' + b.ver + '.</b> ' + BS_KINDS.map(function (k) { return k[1] + ' ' + esc(cronLabel(b.want[k[0]])); }).join(' · ') + '. ' +
        (agentAlive(s) ? 'Agen mengambilnya pada heartbeat berikutnya.' : 'Agen tidak merespons, jadi diterapkan saat agen kembali. Sampai itu cron tetap memakai jadwal di bawah.') + '</div>';
    }
    h += tbl(['Jenis', 'Jadwal berlaku', 'Berikutnya', 'Alert bila lebih tua dari'], BS_KINDS.map(function (k) {
      var c = b[k[0]], off = c === 'off';
      return '<tr' + (off ? ' class="row-' + (k[0] === 'full' ? 'bad' : 'warn') + '"' : '') + '><td>' + k[1] + '</td><td>' + (off ? '<b>Mati</b>' : esc(cronLabel(c))) + '</td><td>' + (off || s.status === 'off' ? '—' : cronNextLabel(c)) + '</td><td>' + esc(thresholdText(s, k[0])) + '</td></tr>';
    }));
    return h + '<p class="small muted" style="margin-top:6px">' + (s.status === 'off' ? 'Terakhir dilaporkan agen sebelum container dinonaktifkan. Agen ditolak, jadi jadwal baru tidak bisa dikirim.' : 'Dilaporkan agen lewat heartbeat. Cron di container tetap memakai jadwal ini saat LumbungDB mati.') + '</p>';
  }
  function backupRequestSection(s) {
    var logs = S.ctrlLog.filter(function (x) { return x.server === s.name; }), reqs = reqsOf(s), h = '';
    if (s.status !== 'off' || reqs.length) {
      h += section('Permintaan backup');
      h += reqs.length ? tbl(['Diminta', 'Jenis', 'Status', ['Durasi', 'num'], 'Keterangan', ''], reqs.map(function (r) {
        var lv = REQ_PILL[r.status], cls = lv === 'bad' || lv === 'warn' ? ' class="row-' + lv + '"' : '';
        return '<tr' + cls + '><td>' + esc(r.at) + '<br><span class="muted small">' + esc(r.by) + '</span></td><td>' + esc(reqKindLabel(r)) + '</td><td>' + (r.status === 'Berjalan' || r.status === 'Mengirim' ? '<span class="spin"></span> ' : '') + pill(lv, r.status) + '</td><td class="num">' + esc(r.dur || '—') + '</td><td class="small">' + esc(r.note || '') + '</td><td class="num">' +
          (r.status === 'Menunggu' && canWrite() ? '<button class="btn btn-sm" data-act="req-cancel" data-id="' + r.id + '">Batalkan</button>' : '') + '</td></tr>';
      })) : '<div class="card"><div class="empty"><b>Belum ada permintaan</b>Backup di luar jadwal diminta lewat Backup sekarang.</div></div>';
    }
    if (logs.length) {
      h += '<details class="box"><summary>Riwayat perubahan (' + logs.length + ')</summary><div class="box-body">' + tbl(['Waktu', 'Tindakan', 'Rincian', 'Oleh'], logs.map(function (x) {
        return '<tr><td>' + esc(x.at) + '</td><td>' + esc(x.act) + '</td><td>' + esc(x.detail) + '</td><td>' + esc(x.by) + '</td></tr>';
      })) + '</div></details>';
    }
    return h;
  }

  // ---------- Form jadwal backup ----------
  function bsMode(k, c) {
    if (c === 'off') return 'off';
    if (k !== 'binlog' && /^\d+ \d+ \* \* \*$/.test(c)) return 'day';
    if (k !== 'binlog' && /^\d+ \d+ \* \* [0-7]$/.test(c)) return 'week';
    if (k !== 'binlog' && /^0 \*\/(6|8|12) \* \* \*$/.test(c)) return 'hours';
    if (k === 'binlog' && (c === '0 * * * *' || /^\*\/(5|15|30) \* \* \* \*$/.test(c))) return 'mins';
    return 'custom';
  }
  function bsOpt(v, label, cur) { return '<option value="' + v + '"' + (String(cur) === String(v) ? ' selected' : '') + '>' + label + '</option>'; }
  function bsControls(k, label, c) {
    var mode = bsMode(k, c), p = c === 'off' ? [] : c.split(/\s+/), isClock = mode === 'day' || mode === 'week', id = 'bs-' + k + '-';
    var time = isClock ? p2(p[1]) + ':' + p2(p[0]) : (k === 'dump' ? '03:00' : '01:00');
    var modes = k === 'binlog' ? [['mins', 'Tiap N menit'], ['custom', 'Kustom (cron)'], ['off', 'Mati']] : [['day', 'Tiap hari'], ['week', 'Mingguan'], ['hours', 'Tiap N jam'], ['custom', 'Kustom (cron)'], ['off', 'Mati']];
    var hoursCur = mode === 'hours' ? p[1].slice(2) : 12, minsCur = mode === 'mins' ? (p[0] === '0' ? 60 : p[0].slice(2)) : 15;
    return '<div class="bs-kind field"><label for="' + id + 'mode">' + label + '</label><div class="bs-row">' +
      '<select id="' + id + 'mode" data-in="bs-input">' + modes.map(function (m) { return bsOpt(m[0], m[1], mode); }).join('') + '</select>' +
      '<select id="' + id + 'dow" data-in="bs-input" aria-label="Hari ' + label + '">' + DAY_NAMES.map(function (d, i) { return bsOpt(i, 'Tiap ' + d, mode === 'week' ? +p[4] % 7 : 0); }).join('') + '</select>' +
      '<input type="time" id="' + id + 'time" data-in="bs-input" value="' + time + '" aria-label="Jam ' + label + ' (WIB)">' +
      (k === 'binlog' ? '<select id="' + id + 'every" data-in="bs-input" aria-label="Jeda ' + label + '">' + [5, 15, 30, 60].map(function (n) { return bsOpt(n, n + ' menit', minsCur); }).join('') + '</select>'
        : '<select id="' + id + 'every" data-in="bs-input" aria-label="Jeda ' + label + '">' + [6, 8, 12].map(function (n) { return bsOpt(n, n + ' jam', hoursCur); }).join('') + '</select>') +
      '<input type="text" id="' + id + 'cron" data-in="bs-input" value="' + esc(mode === 'custom' ? c : '') + '" placeholder="mis. 30 1 * * 1-5" aria-label="Cron ' + label + '" autocomplete="off" spellcheck="false">' +
      '</div><div id="' + id + 'prev"></div></div>';
  }
  function bsEl(k, part) { return document.getElementById('bs-' + k + '-' + part); }
  function bsRead(k) {
    var mode = bsEl(k, 'mode').value, t = bsEl(k, 'time').value.split(':'), hm = (+t[1] || 0) + ' ' + (+t[0] || 0), n = bsEl(k, 'every').value;
    if (mode === 'off') return 'off';
    if (mode === 'day') return hm + ' * * *';
    if (mode === 'week') return hm + ' * * ' + bsEl(k, 'dow').value;
    if (mode === 'hours') return '0 */' + n + ' * * *';
    if (mode === 'mins') return n === '60' ? '0 * * * *' : '*/' + n + ' * * * *';
    return bsEl(k, 'cron').value.trim().replace(/\s+/g, ' ');
  }
  var BS_OFF_NOTE = {
    full: '<b>Full mati.</b> Tidak ada full baru, dan alert "Jadwal full dimatikan" aktif selama jadwal ini mati.',
    dump: '<b>Dump mati.</b> Restore per database dan dump <code>_users</code> tidak lagi diperbarui. Umur dump tidak dinilai.',
    binlog: '<b>Binlog mati.</b> Pemulihan ke titik waktu tidak bisa mencapai data terbaru.'
  };
  // Mengembalikan true bila semua isian form valid.
  function bsPreview(s) {
    var ok = true;
    BS_KINDS.forEach(function (x) {
      var k = x[0], mode = bsEl(k, 'mode').value, c = bsRead(k), prev = bsEl(k, 'prev'), txt;
      bsEl(k, 'dow').hidden = mode !== 'week';
      bsEl(k, 'time').hidden = mode !== 'day' && mode !== 'week';
      bsEl(k, 'every').hidden = mode !== 'hours' && mode !== 'mins';
      bsEl(k, 'cron').hidden = mode !== 'custom';
      if (c === 'off') txt = '<div class="msg-err">' + BS_OFF_NOTE[k] + '</div>';
      else if (!cronValid(c)) { ok = false; txt = '<div class="msg-err">✕ Bukan cron 5 kolom yang valid, atau tidak pernah berjalan dalam 2 bulan.</div>'; }
      else {
        txt = '<div class="hint">Berikutnya ' + cronNextLabel(c) + (k === 'binlog' ? '' : ' · alert bila lebih tua dari ' + cronThreshold(c)) + '</div>';
        if (k === 'full') {
          var next = cronNext(c), near = S.servers.filter(function (o) {
            return o !== s && o.host === s.host && o.bsched && o.bsched.full !== 'off' && o.status === 'active' && next && nearInDay(cronNext(o.bsched.full), next);
          })[0];
          if (near) txt += '<div class="msg-warn">Berdekatan dengan full ' + esc(near.name) + ' (' + esc(cronLabel(near.bsched.full)) + ') di server yang sama.</div>';
        }
      }
      prev.innerHTML = txt;
    });
    if (ok) document.getElementById('bs-err').innerHTML = '';
    return ok;
  }
  // Jarak jam dalam sehari, termasuk yang melewati tengah malam.
  function nearInDay(a, b) {
    var day = 86400000, diff = Math.abs(a - b) % day;
    return Math.min(diff, day - diff) < 1800000;
  }
  function bsChanges(b, want) {
    return BS_KINDS.filter(function (x) { return b[x[0]] !== want[x[0]]; }).map(function (x) { return x[1] + ': ' + cronLabel(b[x[0]]) + ' → ' + cronLabel(want[x[0]]); }).join(' · ');
  }
  // Jadwal sampai ke agen lewat jawaban heartbeat, jadi agen yang berhenti tidak menerapkannya.
  function bsDeliver(s) {
    var b = s.bsched, v = b.ver;
    setTimeout(function () {
      if (findServer(s.name) !== s || s.bsched !== b || b.ver !== v || !b.want) return;
      if (!agentAlive(s)) return;
      if (S.agentReject) { b.err = 'crontab menolak jadwal baru'; b.errAt = '30 Okt ' + nowLabel(); toast('Agen ' + s.name + ' menolak jadwal'); rerender(); return; }
      var w = b.want;
      if (w.full === 'off' && b.full !== 'off') b.offAt = '30 Okt ' + nowLabel();
      b.full = w.full; b.dump = w.dump; b.binlog = w.binlog; b.src = 'lumbung'; b.applied = v; b.want = null;
      toast('Jadwal ' + s.name + ' diterapkan agen');
      rerender();
    }, 4000);
  }
  function bsSubmit(s, want, detail) {
    var b = s.bsched;
    b.want = want; b.ver++; b.err = null; b.errAt = null;
    logControl(s, 'Ubah jadwal', detail);
    toast('Jadwal disimpan, menunggu agen ' + s.name);
    bsDeliver(s);
  }

  // ---------- Permintaan backup: alur simulasi ----------
  var reqSeq = 100;
  function reqFile(s, r) {
    var stamp = '2026-10-30_' + nowLabel().replace(':', ''), key = 300000 + S.clock.getHours() * 100 + S.clock.getMinutes(), when = '30 Okt ' + nowLabel();
    var rows = riwRows(s), sizeOf = function (kind, prefix) { var row = rows.filter(function (x) { return x[2] === kind && (!prefix || x[3].indexOf(prefix + '/') === 0); })[0]; return row ? row[4] : '—'; };
    var out = [];
    if (r.kind === 'full') out.push(['full', stamp + '.xbstream.gz', sizeOf('full')]);
    if (r.kind === 'binlog') {
      var last = rows.filter(function (x) { return x[2] === 'binlog'; })[0], n = last ? parseInt(/\.(\d+)\.gz/.exec(last[3])[1], 10) + 1 : 1;
      out.push(['binlog', 'mysql-bin.' + String(n).padStart(6, '0') + '.gz', sizeOf('binlog')]);
    }
    if (r.kind === 'dump') (r.db ? [r.db] : s.dbs.concat('_users')).forEach(function (db) { out.push(['dump', db + '/' + db + '-' + stamp + '.sql.gz', sizeOf('dump', db)]); });
    out.forEach(function (f) { S.manualFiles.unshift([key, s.name, f[0], f[1], f[2], when, 'manual']); s.recent.unshift([f[0], f[1], f[2], 'baru saja']); });
    if (r.kind === 'full') { s.full = 'baru saja'; s.files.full++; if (s.name === 'lab-db') S.resolved['full-lab'] = true; }
    if (r.kind === 'binlog') s.files.binlog++;
    if (r.kind === 'dump') {
      s.files.dump += out.length; s.dump = r.db ? s.dump : 'baru saja';
      (s.dumps || []).forEach(function (d) { if (!r.db || d[0] === r.db) { d[1] = 'baru saja'; d[2] = 'ok'; d[3] = ''; } });
    }
    return out.length === 1 ? out[0][1] + ' · ' + out[0][2] : out.length + ' file terkirim';
  }
  function reqRun(s, r) {
    var alive = function () { return S.reqs.indexOf(r) >= 0 && findServer(s.name) === s; };
    var set = function (status, note, dur) { r.status = status; r.note = note || ''; if (dur) r.dur = dur; toast(reqKindLabel(r) + ' ' + s.name + ': ' + status.toLowerCase()); rerender(); };
    setTimeout(function () {
      if (!alive() || r.status !== 'Menunggu' || !agentAlive(s)) return; // tetap menunggu; kedaluwarsa setelah batas waktu
      var sim = S.reqSim;
      if (sim === 'disk') { set('Ditolak', 'Sisa disk /backup kurang dari 1,2× ukuran full terakhir'); return; }
      if (sim === 'busy') { set('Sudah berjalan', 'Backup jenis ini sedang berjalan dari cron'); return; }
      set('Berjalan', 'Script sedang berjalan');
      setTimeout(function () {
        if (!alive()) return;
        if (sim === 'fail') { set('Gagal', 'Script keluar dengan kode 1; lihat log backup', '2 mnt'); return; }
        if (sim === 'restart') { set('Tidak selesai', 'Container restart saat berjalan; tidak diulang otomatis'); return; }
        set('Mengirim', 'Script selesai, file sedang dikirim');
        setTimeout(function finish() {
          if (!alive()) return;
          if (nasDown()) { setTimeout(finish, 2000); return; } // upload ditolak 503, agen mengulang
          var note = reqFile(s, r);
          set('Selesai', note, r.kind === 'full' ? s.fullDur : '1 mnt');
        }, 2500);
      }, 3000);
    }, 5000);
  }

  // ---------- Halaman: Tambah server (wizard 3 langkah) ----------
  var NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;
  // Nama project compose harus huruf kecil dan diawali huruf atau angka; titik menjadi tanda hubung.
  function projectName(n) { return n.toLowerCase().replace(/[^a-z0-9_-]/g, '-'); }
  function nameError(v) {
    if (!v) return 'Isi nama container.';
    if (/\s/.test(v)) return 'Tidak boleh berisi spasi.';
    if (!/^[A-Za-z0-9]/.test(v)) return 'Harus diawali huruf atau angka.';
    if (!NAME_RE.test(v)) return 'Hanya huruf, angka, _ . - yang diizinkan.';
    if (v.length > 64) return 'Maksimal 64 karakter.';
    if (v.toLowerCase() === 'tambah') return 'Nama “tambah” dipakai oleh aplikasi.';
    if (S.servers.some(function (s) { return s.name.toLowerCase() === v.toLowerCase(); })) return 'Nama “' + esc(v) + '” sudah terdaftar.';
    // Project yang sama berarti volume yang sama bila keduanya ada di satu server.
    var twin = S.servers.filter(function (s) { return projectName(s.name) === projectName(v); })[0];
    if (twin) return 'Bentrok dengan “' + esc(twin.name) + '”: keduanya menjadi project compose “' + esc(projectName(v)) + '”.';
    // Nama sesi Restore manual yang masih aktif dicadangkan sampai sesinya berakhir.
    var held = S.rm.filter(function (x) { return rmActive(x) && projectName(x.name) === projectName(v); })[0];
    if (held) return 'Dicadangkan oleh Restore manual yang masih aktif (“' + esc(held.name) + '”).';
    return '';
  }
  function wizCanNext(w) { return !!w.name && !nameError(w.name); }
  function pageWizard(fresh) {
    crumbs([['Container', '#/container'], 'Tambah container']);
    if (!canWrite()) return pageHead('Tambah container MariaDB') + '<div class="card"><div class="empty"><b>Aksi tulis dimatikan</b>Isi <code>DASHBOARD_PASS</code> di <code>.env</code> lalu restart container LumbungDB.</div></div>';
    // Wizard yang sudah selesai dimulai ulang hanya saat halaman dibuka lagi, bukan saat render ulang.
    if (!S.wizard || S.wizard.finished && fresh) S.wizard = { step: 1, name: '', port: 3306 };
    var w = S.wizard;
    var labels = ['Nama', 'Pasang MariaDB', 'Verifikasi'];
    var h = pageHead('Tambah container MariaDB', 'Satu container = satu agen di dalam image dan satu token. Pendaftaran baru selesai setelah agen benar-benar terhubung dan backup pertama masuk.');
    h += '<ol class="wiz-steps">' + labels.map(function (l, i) {
      var n = i + 1, cls = n < w.step ? 'done' : (n === w.step ? 'now' : '');
      var can = n < w.step && n !== 1 ? '' : ' disabled';
      return '<li class="' + cls + '"><button data-act="wiz-go" data-step="' + n + '"' + can + '><span class="n">' + (n < w.step ? '✓' : n) + '</span><span class="lbl">' + l + '</span></button></li>';
    }).join('') + '</ol>';
    h += '<div class="wiz-box">';
    if (w.step === 1) {
      var err = w.name ? nameError(w.name) : '';
      h += '<h2>Daftarkan container baru</h2>' +
        '<div class="field"><label for="wiz-name">Nama container</label><input type="text" id="wiz-name" data-in="wiz-name" value="' + esc(w.name) + '" placeholder="mis. radiologi-db" autocomplete="off" spellcheck="false"' + (err ? ' class="invalid"' : '') + '>' +
        '<div class="hint">Harus unik di seluruh LumbungDB, jadi hindari nama umum seperti <code>mariadb</code>. Huruf, angka, <code>_</code> <code>.</code> <code>-</code>; harus diawali huruf atau angka. Menjadi nama folder di NAS: <code id="wiz-path">/lumbungdb/' + esc(w.name || '&lt;nama&gt;') + '/</code></div>' +
        '<div id="wiz-msg">' + (w.name ? (err ? '<div class="msg-err">✕ ' + err + '</div>' : '<div class="msg-ok">✓ Nama tersedia</div>') : '') + '</div></div>' +
        '<div class="field"><label for="wiz-port">Port di server</label><input type="number" id="wiz-port" data-in="wiz-port" value="' + esc(String(w.port)) + '" min="1" max="65535"><div class="hint">Port MariaDB di server tujuan. Script menolak bila port sudah terpakai.</div></div>' +
        '<p class="hint" style="margin:-6px 0 14px">Server tidak perlu diisi. Agen membacanya dari mesin tempat container berjalan dan mengirimnya lewat heartbeat.</p>' +
        (w.confErr ? '<div class="banner banner-bad"><b>Tidak bisa menulis <code>config/agents.conf</code>.</b> File read-only atau izinnya salah. Container belum didaftarkan dan token belum dibuat; perbaiki izin file lalu coba lagi.</div>' : '') +
        '<div class="wiz-actions"><button class="btn btn-pri" id="wiz-next1" data-act="wiz-create"' + (wizCanNext(w) ? '' : ' disabled') + '>Lanjut: buat perintah ›</button><a class="btn" href="#/container">Batal</a></div>';
    } else if (w.step === 2) {
      var s = w.server;
      if (w.tokenHidden) {
        h += '<h2>Pasang ' + esc(s.name) + '</h2><div class="banner banner-warn">Token sudah disembunyikan dan tidak bisa ditampilkan lagi. Bila belum tersimpan, ganti token di baris <code>' + esc(s.name) + '</code> pada <code>config/agents.conf</code>, lalu pakai token itu di <code>.env</code> container.</div>' +
          '<div class="wiz-actions"><button class="btn btn-pri" data-act="wiz-go" data-step="3">Kembali ke verifikasi ›</button></div>';
      } else {
        var env = '### LumbungDB\nLUMBUNG_URL=https://lumbung.rumahsakit.co.id\nLUMBUNG_TOKEN=' + w.token + '\n\n### Image. Container lama: samakan versi MariaDB, mis. 11.4-a1\nIMAGE_TAG=12.3-a1\n\n### Container\nCONTAINER_NAME=' + s.name + '\nCONTAINER_HOSTNAME=' + s.name + '\nDATA_PORT=' + w.port + '\nDNS1=\nDNS2=\n\n### Isi sendiri di server; LumbungDB tidak menyimpannya\nDB_ROOTPASSWORD=\nDB_APPUSER=\nDB_APPPASS=\nDB_NAME=\nBACKUP_USER=user_backup\nBACKUP_PASS=\n\n### Kosong = jadwal bawaan; bisa diubah dari dashboard\nBACKUP_CRON=\nDUMP_CRON=\nBINLOG_CRON=\n\n### Batas tahan arsip binlog yang belum terkirim, dalam hari; sesudahnya dihapus\nBINLOG_UNSENT_MAX_DAYS=3';
        var cmd = 'docker compose -p ' + projectName(s.name) + ' -f docker-compose.lumbung.yml --env-file .env up -d';
        var install = 'curl -fsSL ' + RM_URL + '/install.sh | LUMBUNG_URL=' + RM_URL + ' INSTALL_TOKEN=lmi_' + w.token + ' sh';
        h += '<h2>Pasang ' + esc(s.name) + '</h2>' +
          '<div class="once"><b>Perintah hanya ditampilkan sekali</b> dan berlaku 2 jam. Bila hilang atau kedaluwarsa, buat yang baru di langkah verifikasi.</div>' +
          '<div class="field"><label>Jalankan di server database</label><div class="copy"><pre>' + esc(install) + '</pre><button class="btn" data-act="copy" data-copy="' + esc(install) + '">Salin</button></div></div>' +
          '<p class="hint">Butuh Docker dan Compose v2, serta akses dari server itu ke LumbungDB. Script membuat password acak di <code>.env</code> di subfolder <code>&lt;project&gt;/</code> pada folder tempat perintah dijalankan, menjalankan MariaDB, lalu menunggunya sehat. Password tidak dikirim ke LumbungDB.</p>' +
          '<p class="hint">Pemakaian pertama mengikat perintah ke mesin itu. Bila gagal, jalankan perintah yang sama lagi dari folder yang sama; password tidak berubah.</p>' +
          '<details style="margin:14px 0"><summary style="cursor:pointer;font-weight:600;margin-bottom:10px">Container lama atau pasang manual</summary>' +
          '<div class="once"><b>Token agen hanya ditampilkan sekali.</b> Baris <code>' + esc(s.name) + ' = …</code> sudah ditambahkan ke <code>config/agents.conf</code>. Abaikan bagian ini bila memakai perintah di atas.</div>' +
          '<div class="field"><label>Token agen</label><div class="copy"><code class="tok">' + w.token + '</code><button class="btn" data-act="copy" data-copy="' + w.token + '">Salin</button></div></div>' +
          '<div class="field"><label>Isi <code>.env</code> di server database</label><div class="copy"><pre>' + esc(env) + '</pre><button class="btn" data-act="copy" data-copy="' + esc(env) + '">Salin</button></div></div>' +
          '<p class="hint" style="margin:-6px 0 14px">Image <code>zmutclik/mariadb:lumbungdb-*</code> sudah berisi MariaDB, script backup, cron, dan agen. Jadwal bawaan: full dan dump tiap hari pada jam yang diturunkan dari nama container, binlog tiap 15 menit; semuanya bisa diubah dari dashboard. Nama project <code>-p</code> berbeda untuk tiap container supaya beberapa container di satu server tidak bentrok.</p><p class="hint" style="margin:-6px 0 14px"><b>Container lama:</b> ubah <code>IMAGE_TAG</code> ke versi MariaDB yang sedang berjalan, supaya MariaDB tidak ikut naik versi, isi <code>CONTAINER_HOSTNAME</code> dengan hasil <code>docker exec &lt;container&gt; hostname</code>, supaya jam jadwal bawaannya tetap, dan pakai nama project lama pada <code>-p</code>, supaya volume data dan <code>/backup</code> yang lama tetap terpakai. MariaDB ikut restart.</p><div class="field"><label>Salin <code>docker-compose.lumbung.yml</code> ke server, lalu jalankan</label><div class="copy"><pre>' + cmd + '</pre><button class="btn" data-act="copy" data-copy="' + cmd + '">Salin</button></div></div></details>' +
          '<div class="wiz-actions"><button class="btn btn-pri" id="wiz-next2" data-act="wiz-verify">Lanjut: verifikasi ›</button><button class="link-btn danger right" data-act="wiz-cancel">Batalkan pendaftaran</button></div>';
      }
    } else {
      var sv = w.server, hb = sv.status !== 'wait-hb', bk = sv.status === 'active';
      h += '<h2>Verifikasi koneksi ' + esc(sv.name) + '</h2><ul class="checks">' +
        '<li class="done"><span class="ic">✓</span><div>Token dibuat <span class="muted">' + w.createdAt + ' WIB</span></div></li>' +
        (hb ? '<li class="done"><span class="ic">✓</span><div>Heartbeat pertama diterima <span class="muted">server terbaca: ' + esc(sv.host) + ' · ' + sv.ip + ' · /backup ' + sv.disk + '% terpakai</span></div></li>'
          : '<li class="todo"><span class="ic"><span class="spin"></span></span><div><b style="color:var(--text)">Menunggu heartbeat pertama</b><span class="muted">agen mengirim heartbeat tiap 60 detik setelah container berjalan</span></div></li>') +
        (bk ? '<li class="done"><span class="ic">✓</span><div>Backup pertama diterima <span class="muted">' + esc(sv.recent[0][1]) + ' · ' + sv.recent[0][2] + '</span></div></li>'
          : '<li class="todo"><span class="ic">' + (hb ? '<span class="spin"></span>' : '') + '</span><div>' + (hb ? '<b style="color:var(--text)">Menunggu backup pertama</b><span class="muted">agen sudah memindai: 0 full, 0 dump, 2 binlog menunggu stabil (120 dtk)</span>' : 'Backup pertama') + '</div></li>') +
        '</ul>';
      if (bk) h += '<div class="banner banner-ok"><b>' + esc(sv.name) + ' siap dipantau.</b> Status container menjadi Menunggu full pertama sampai full mengikuti jadwal cron; alert muncul bila belum masuk 36 jam setelah didaftarkan.</div>';
      else h += '<p class="hint">Supaya tidak menunggu jadwal, setelah heartbeat masuk pakai Backup sekarang di detail container.</p>' +
        '<p class="small muted">Prototype: heartbeat dan backup pertama disimulasikan dalam beberapa detik.</p>';
      h += '<div class="wiz-actions"><a class="btn btn-pri" href="#/container/' + encodeURIComponent(sv.name) + '">Buka detail ' + esc(sv.name) + '</a><a class="btn" href="#/container">' + (bk ? 'Kembali ke daftar' : 'Tutup, cek nanti') + '</a></div>';
    }
    return h + '</div>';
  }
  function startVerifySim(w) {
    var s = w.server;
    setTimeout(function () {
      if (findServer(s.name) !== s || s.status !== 'wait-hb') return;
      s.status = 'wait-backup';
      // Server dibaca agen dari mesinnya, bukan diisi admin.
      var same = S.servers.filter(function (x) { return x.host === S.hostSim; })[0], asRunner = S.runners.filter(function (x) { return x.host === S.hostSim; })[0];
      s.host = S.hostSim; s.ip = same ? same.ip : asRunner ? asRunner.ip : '10.10.4.21'; s.disk = 34; s.hb = 'baru saja';
      s.files = { full: 0, dump: 0, binlog: 2 };
      s.upload = { pending: 2, lastOk: null, err: null };
      s.bsched = seedSched(s.name);
      toast('Heartbeat pertama dari ' + s.name + ' diterima');
      rerender();
    }, 4000);
    setTimeout(function firstBackup() {
      if (findServer(s.name) !== s || s.status !== 'wait-backup') return;
      if (nasDown()) { setTimeout(firstBackup, 2000); return; } // upload ditolak 503, agen mengulang
      var db = s.name.replace(/-db$/, '').replace(/[^A-Za-z0-9_]/g, '_');
      s.status = 'active'; s.dump = 'baru saja'; s.full = '— (menunggu jadwal)'; s.hb = '1 menit lalu';
      s.upload = { pending: 0, lastOk: 'baru saja', err: null };
      s.dbs = [db]; s.files = { full: 0, dump: 2, binlog: 2 };
      s.dumps = [[db, 'baru saja', 'ok', ''], ['_users', 'baru saja', 'ok', '']];
      s.recent = [['dump', db + '/' + db + '-2026-10-30_' + nowLabel().replace(':', '') + '.sql.gz', '214 MB', 'baru saja'], ['dump', '_users/_users-2026-10-30_' + nowLabel().replace(':', '') + '.sql.gz', '31 KB', 'baru saja']];
      s.log = '2026-10-30 ' + nowLabel() + ':02 databasebackup start\n2026-10-30 ' + nowLabel() + ':41 dump ' + db + ' ok (214 MB)\n2026-10-30 ' + nowLabel() + ':42 databasebackup done';
      w.finished = true;
      toast('Backup pertama ' + s.name + ' diterima · container siap dipantau');
      rerender();
    }, 10000);
  }

  // ---------- Runner: uji restore dan database cadangan ----------
  // Runner mengambil tugas dari LumbungDB dan membaca file dari NAS; tiap container ditetapkan ke satu runner.
  function runnerReady(r) { return !!r && (r.state === 'ok' || r.state === 'low'); }
  // Larangan per container: uji dan cadangan sebuah container tidak boleh berjalan di server yang sama dengan container itu.
  function sameServer(s, r) { return !!s && !!r && s.host !== '—' && s.host === r.host; }
  function runnerFor(s, r) { return runnerReady(r) && !sameServer(s, r); }
  // Runner untuk container hasil Naikkan ke production: utamakan yang sehat. Runner yang bermasalah tetap
  // ditetapkan supaya uji berjalan lagi saat ia pulih, dan alert runner-nya sudah menjelaskan dampaknya.
  function runnerForPromoted(host) {
    var c = { host: host };
    return S.runners.filter(function (x) { return runnerFor(c, x); })[0] ||
      S.runners.filter(function (x) { return x.state !== 'off' && x.state !== 'wait' && !sameServer(c, x); })[0] || null;
  }
  function runnerBusy(r) { return (!!S.restoreRun && S.restoreRun.runner === r.name) || (!!S.standbyRun && S.standbyRun.runner === r.name); }
  function defaultRunner() {
    return S.runners.filter(function (r) { return r.def && r.state !== 'off'; })[0] || S.runners.filter(runnerReady)[0] || null;
  }
  // Satu penyebab, satu alert: selama NAS bermasalah bagi semua, runner tidak ikut memberi alert NAS sendiri.
  function runnerAlerts() {
    var out = [];
    S.runners.forEach(function (r) {
      var live = S.servers.filter(function (s) { return s.status !== 'off'; });
      var nTest = live.filter(function (s) { return s.runner === r.name; }).length, nCad = live.filter(function (s) { return handlesCad(s, r.name); }).length, n = containersOf(r.name).filter(function (s) { return s.status !== 'off'; }).length;
      var sub = (nTest ? ' · ' + nTest + ' container tidak diuji' : '') + (nCad ? ' · ' + nCad + ' database cadangan tidak di-restore' : '');
      var add = function (k, cond, since, lvl) { out.push({ key: 'runner-' + k + '-' + r.name, kind: 'Runner', lvl: lvl, cond: cond, server: r.name, since: since, notif: 'Terkirim' }); };
      var same = live.filter(function (s) { return (s.runner === r.name || handlesCad(s, r.name)) && sameServer(s, r); }).map(function (s) { return s.name; });
      if (same.length) add('same', same.join(', ') + ' satu server dengan runner ini, jadi uji dan cadangannya tidak berjalan', '', 'bad');
      if (r.state === 'dead') add('dead', 'Runner tidak merespons > 10 menit' + sub, r.since, 'bad');
      if (r.state === 'dl' && !nasDown()) add('dl', 'Runner gagal mengunduh file backup' + sub, r.since, 'bad');
      if (r.state === 'low') add('disk', 'Scratch runner tersisa ' + r.free + '% (batas 15%)', r.since, 'warn');
      if (r.state === 'off' && n) add('off', 'Runner dinonaktifkan' + sub, r.since, 'bad');
    });
    return out;
  }
  function runnerPill(r) {
    if (r.state === 'off') return pill('off', 'Nonaktif');
    if (r.state === 'wait') return pill('wait', 'Menunggu heartbeat pertama');
    if (r.state === 'dead') return pill('bad', 'Tidak merespons');
    if (r.state === 'dl') return pill('bad', 'Unduhan gagal');
    if (runnerBusy(r)) return pill('wait', 'Sedang bertugas');
    if (r.state === 'low') return pill('warn', 'Scratch hampir penuh');
    return pill('ok', 'Aktif');
  }
  function runnerRowLvl(r) {
    if (r.state === 'dead' || r.state === 'dl') return 'bad';
    if (r.state === 'low') return 'warn';
    if (r.state === 'wait') return 'wait';
    return r.state === 'off' ? 'off' : 'ok';
  }
  function runnerDl(r) {
    if (r.state === 'dead' || r.state === 'off') return 'Tidak diketahui';
    if (r.state === 'wait') return '—';
    if (nasDown()) return 'Ditunda · ' + NAS_COND[S.nas.state];
    if (r.state === 'dl') return 'Gagal sejak ' + r.since + ' · ' + esc(r.dlErr);
    return 'Normal, lewat HTTPS';
  }
  function runnerJob(r) {
    if (!runnerBusy(r)) return '—';
    if (S.standbyRun && S.standbyRun.runner === r.name) return 'Restore <a href="#/container/' + encodeURIComponent(S.standbyRun.server) + '/pulih">' + esc(S.standbyRun.server) + '</a>';
    var run = S.restoreRun, n = run.steps.filter(function (st) { return !st.g; }).length;
    return 'Uji <a href="#/container/' + encodeURIComponent(run.server) + '/restore">' + esc(run.server) + '</a>, langkah ' + Math.min(run.step + 1, n) + ' dari ' + n;
  }
  function freeCell(r) {
    if (r.free == null) return '—';
    return '<span class="free-cell"><span class="bar"><i style="width:' + (100 - r.free) + '%"></i></span>' + r.free + '%</span>';
  }
  function prodBanner(list) {
    return list.map(function (r) {
      var same = S.servers.filter(function (s) { return s.status !== 'off' && (s.runner === r.name || handlesCad(s, r.name)) && sameServer(s, r); }).map(function (s) { return esc(s.name); });
      return same.length ? '<div class="banner banner-warn"><b>' + same.join(', ') + ' berada di server yang sama dengan ' + esc(r.name) + '.</b> Uji dan cadangannya tidak berjalan di sini. Pilih runner lain di detail container itu.</div>' : '';
    }).join('');
  }
  function addRunnerBtn() {
    return canWrite() ? '<a class="btn btn-pri" href="#/runner/tambah">+ Tambah runner</a>' : '<span class="btn btn-pri disabled" aria-disabled="true" title="Butuh login dashboard">+ Tambah runner</span>';
  }
  function pageRunnerList() {
    crumbs(['Runner']);
    var h = pageHead('Runner <span class="muted" style="font-weight:400">(' + S.runners.length + ')</span>', 'Menjalankan uji restore dan database cadangan, di VM atau server khusus.', addRunnerBtn());
    if (!S.runners.length) return h + '<div class="card"><div class="empty"><b>Belum ada runner</b>Tanpa runner, uji restore dan database cadangan tidak berjalan. Pengumpulan backup dan alert tetap berjalan.</div></div>';
    h += prodBanner(S.runners);
    h += tbl(['Runner', 'Server', 'Status', 'Heartbeat', 'Sisa scratch', 'Tugas berjalan', ['Container', 'num']], S.runners.map(function (r) {
      return '<tr class="row-' + runnerRowLvl(r) + '"><td><a href="#/runner/' + encodeURIComponent(r.name) + '"><b>' + esc(r.name) + '</b></a>' + (r.def ? ' <span class="muted small">bawaan</span>' : '') + '</td><td>' + esc(r.host) + '</td><td>' + runnerPill(r) + '</td><td>' + esc(r.hb) + '</td><td>' + freeCell(r) + '</td><td>' + runnerJob(r) + '</td><td class="num">' + containersOf(r.name).length + '</td></tr>';
    }));
    h += '<p class="small muted" style="margin-top:8px">Runner yang menghubungi LumbungDB lewat HTTPS, jadi mesin runner tidak perlu membuka port dan tidak punya akses NFS. Ia hanya bisa mengunduh file untuk tugasnya, dan tiap unduhan dicatat. Container baru memakai runner bawaan; runner uji diganti di tab Uji restore, runner cadangan di tab Cadangan. Token hanya muncul sekali saat runner ditambahkan.</p>';
    return h;
  }
  function restorePill(r) {
    if (r.skip) return pill('warn', 'Tidak terlaksana');
    return r.ok ? pill('ok', 'Lolos') : pill('bad', 'Gagal');
  }
  function skipRow(r, when, cells, span, trailing) {
    return '<tr class="row-warn"><td>' + when + '</td>' + cells + '<td colspan="' + span + '">' + pill('warn', 'Tidak terlaksana') + ' <span class="muted small">' + esc(r.reason) + '</span></td><td class="num">—</td>' + (trailing != null ? '<td class="num">' + trailing + '</td>' : '') + '</tr>';
  }
  function pageRunnerDetail(name) {
    var r = findRunner(name);
    if (!r) { crumbs([['Runner', '#/runner'], 'Tidak ditemukan']); return '<div class="empty"><b>Runner “' + esc(name) + '” tidak ada</b><a href="#/runner">Kembali ke daftar runner</a></div>'; }
    crumbs([['Runner', '#/runner'], esc(r.name)]);
    var acts = r.state === 'off' || r.state === 'wait' || !canWrite() ? '' : '<details class="row-menu"><summary aria-label="Aksi untuk ' + esc(r.name) + '">⋮</summary><div class="menu-pop"><button class="danger" data-act="runner-off" data-run="' + esc(r.name) + '">Nonaktifkan</button></div></details>';
    var h = pageHead(esc(r.name) + ' &nbsp;' + runnerPill(r), esc(r.host) + (r.ip && r.ip !== '—' ? ' · ' + r.ip : '') + ' · didaftarkan ' + r.reg, acts);
    h += prodBanner([r]);
    var list = containersOf(r.name);
    if (r.state === 'off') h += '<div class="banner banner-warn"><b>Nonaktif sejak ' + esc(r.since) + '.</b> Baris <code>' + esc(r.name) + '</code> dikomentari (<code>#</code>) di <code>config/runners.conf</code>, jadi runner ditolak (401). Pindahkan container-nya ke runner lain.</div>';
    if (r.state === 'dead') h += '<div class="banner banner-bad"><b>Tidak ada heartbeat sejak ' + esc(r.since) + '.</b> Tugas yang berjalan ditandai tidak selesai setelah batas waktu, dan runner membersihkan sisa container serta scratch saat hidup lagi. Periksa mesin dan koneksinya ke LumbungDB.</div>';
    if (r.state === 'dl' && !nasDown()) h += '<div class="banner banner-bad"><b>Runner ini gagal mengunduh file sejak ' + esc(r.since) + '.</b> ' + esc(r.dlErr) + '. Uji ditandai tidak terlaksana, dan backup tidak dicap gagal. Periksa jaringan antara mesin ini dan LumbungDB.</div>';
    if (r.state === 'wait') h += '<div class="banner banner-info"><b>Menunggu heartbeat pertama.</b>' + (S.runWiz && S.runWiz.runner === r && !S.runWiz.finished ? ' <a href="#/runner/tambah">Buka langkah verifikasi ›</a>' : '') + '</div>';
    h += '<div class="kv">' +
      '<div><small>Heartbeat terakhir</small>' + esc(r.hb) + '</div>' +
      '<div><small>Versi runner</small>' + esc(r.ver) + '</div>' +
      '<div><small>Unduhan file</small>' + runnerDl(r) + '</div>' +
      '<div><small>Sisa disk scratch</small>' + (r.free == null ? '—' : r.free + '% · ' + esc(r.freeTxt)) + '</div>' +
      '</div>';
    if (runnerBusy(r)) h += '<div class="banner banner-info" style="margin-top:14px"><b>Sedang bertugas.</b> ' + runnerJob(r) + '</div>';
    h += section('Container yang ditangani (' + list.length + ')');
    if (list.length) h += tbl(['Container', 'Server', 'Tugas', 'Jadwal uji', 'Jadwal restore', 'Data cadangan sampai', 'Uji terakhir'], list.map(function (s) {
      var isTest = s.runner === r.name, isCad = handlesCad(s, r.name), none = '<span class="muted">Runner lain</span>';
      var last = S.restores.filter(function (x) { return x.server === s.name; })[0];
      return '<tr' + (s.status === 'off' ? ' class="row-off"' : '') + '><td><a href="#/container/' + encodeURIComponent(s.name) + '/restore"><b>' + esc(s.name) + '</b></a></td><td>' + esc(s.host === '—' ? NO_HOST : s.host) + '</td><td>' + [isTest ? 'Uji restore' : '', isCad ? 'Database cadangan' : ''].filter(Boolean).join(', ') + '</td><td>' + (!isTest ? none : s.sched.test.on ? esc(schedLabel(s.sched.test)) : '<span class="muted">Tidak terjadwal</span>') + '</td><td>' + (!isCad ? (usesCad(s) ? none : '<span class="muted">Tidak disiapkan</span>') : s.sched.standby.on ? esc(schedLabel(s.sched.standby)) : '<span class="muted">Dimatikan</span>') + '</td><td>' + (!isCad ? '—' : s.standby ? esc(s.standby.until) : '—') + '</td><td>' + (!isTest ? '—' : last ? restorePill(last) + ' <span class="muted small">' + esc(last.when) + '</span>' : '<span class="muted">Belum pernah</span>') + '</td></tr>';
    }));
    else h += '<div class="card"><div class="empty"><b>Belum ada container</b>Tetapkan runner ini di tab Uji restore atau Restore pada detail container.</div></div>';
    var hist = S.restores.filter(function (x) { return x.runner === r.name; }).map(function (x) { return { task: 'Uji restore', tab: 'restore', x: x }; })
      .concat(S.standby.filter(function (x) { return x.runner === r.name; }).map(function (x) { return { task: 'Restore', tab: 'pulih', x: x }; }))
      .sort(function (p, q) { return whenKey(q.x.when) - whenKey(p.x.when); });
    h += section('Riwayat tugas');
    if (hist.length) h += tbl(['Waktu', 'Container', 'Tugas', ['Durasi', 'num'], 'Hasil'], hist.map(function (t) {
      var x = t.x;
      return '<tr class="row-' + (x.skip ? 'warn' : x.ok ? 'ok' : 'bad') + '"><td>' + esc(x.when) + '</td><td><a href="#/container/' + encodeURIComponent(x.server) + '/' + t.tab + '">' + esc(x.server) + '</a></td><td>' + t.task + '</td><td class="num">' + esc(x.dur) + '</td><td>' + restorePill(x) + (x.skip ? ' <span class="muted small">' + esc(x.reason) + '</span>' : '') + '</td></tr>';
    }));
    else h += '<div class="card"><div class="empty"><b>Belum ada tugas</b>Tugas pertama berjalan pada jadwal berikutnya.</div></div>';
    return h;
  }
  // ---------- Jadwal uji dan database cadangan ----------
  var DAYS = ['Harian', 'Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu', 'Minggu', 'Bulanan'];
  function schedLabel(c) {
    var when = c.day === 'Harian' ? 'Tiap hari' : c.day === 'Bulanan' ? 'Tanggal 1 tiap bulan' : 'Tiap ' + c.day;
    return when + ' ' + c.at;
  }
  function scopeLabel(s) {
    var t = s.sched.test, dbs = t.dbs || s.dbs, parts = [];
    if (t.full) parts.push('full');
    parts.push(t.dbs ? 'dump ' + dbs.join(', ') : 'dump semua database');
    return parts.join(' dan ');
  }
  function whenKey(w) {
    var m = /^(\d+) \S+ (\d+):(\d+)/.exec(w || '');
    return m ? +m[1] * 10000 + +m[2] * 100 + +m[3] : 0;
  }
  function daySel(id, cur) {
    return '<select id="' + id + '" style="width:100%">' + DAYS.map(function (d) { return '<option value="' + d + '"' + (cur === d ? ' selected' : '') + '>' + (d === 'Harian' ? 'Tiap hari' : d === 'Bulanan' ? 'Tanggal 1 tiap bulan' : 'Tiap ' + d) + '</option>'; }).join('') + '</select>';
  }
  function timeSel(id, cur) {
    var h = '<select id="' + id + '" style="width:100%">';
    for (var i = 0; i < 48; i++) { var v = p2n(Math.floor(i / 2)) + ':' + (i % 2 ? '30' : '00'); h += '<option' + (v === cur ? ' selected' : '') + '>' + v + '</option>'; }
    return h + '</select>';
  }
  function schedSection(s) {
    var t = s.sched.test, r = runnerOf(s), can = canTest(s);
    var btns = '<span style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">' +
      '<button class="btn btn-sm" data-act="runner-change" data-role="test" data-srv="' + esc(s.name) + '"' + (canWrite() && s.status !== 'off' && !(S.restoreRun && S.restoreRun.server === s.name) && S.runners.length ? '' : ' disabled') + '>Ganti runner uji</button>' +
      '<button class="btn btn-sm" data-act="sched-test" data-srv="' + esc(s.name) + '"' + (canWrite() && s.status !== 'off' ? '' : ' disabled') + '>Ubah jadwal</button>' +
      '<button class="btn btn-sm" data-act="restore-open" data-srv="' + esc(s.name) + '"' + (can ? '' : ' disabled') + '>Jalankan uji sekarang</button></span>';
    var h = '<div class="section-title" style="margin-top:0;flex-wrap:wrap"><h2>Jadwal uji restore</h2>' + btns + '</div>';
    if (s.status !== 'off' && r && r.state === 'dead') h += '<div class="banner banner-bad"><b>' + esc(r.name) + ' tidak merespons.</b> Uji terjadwal tidak berjalan sampai runner pulih.</div>';
    if (s.status !== 'off' && r && r.state === 'dl' && !nasDown()) h += '<div class="banner banner-bad"><b>' + esc(r.name) + ' gagal mengunduh file.</b> Uji terjadwal ditandai tidak terlaksana sampai masalah ini selesai.</div>';
    if (s.status !== 'off' && sameServer(s, r)) h += '<div class="banner banner-bad"><b>' + esc(r.name) + ' berada di server yang sama dengan container ini.</b> Uji dan cadangannya tidak berjalan di sini. Pilih runner lain.</div>';
    if (s.status !== 'off' && r && r.state === 'off') h += '<div class="banner banner-warn"><b>' + esc(r.name) + ' nonaktif.</b> Pindahkan container ini ke runner lain.</div>';
    if (s.status !== 'off' && s.promoted && !r) h += '<div class="banner banner-bad"><b>Belum ada runner di server lain.</b> Container production ini belum diuji dan tidak punya cadangan. <a href="#/runner/tambah">Tambahkan runner</a> di server lain, lalu tetapkan di sini.</div>';
    if (s.status === 'off') h += '<div class="banner banner-warn"><b>Container nonaktif.</b> Tidak diuji sampai diaktifkan lagi.</div>';
    else if (!t.on) h += '<div class="banner banner-warn"><b>Uji terjadwal mati.</b> ' + (s.sched.standby.on ? 'Backup hanya terbukti lewat restore.' : 'Tidak ada yang rutin membuktikan backup container ini bisa dipulihkan.') + '</div>';
    var skipped = t.dbs ? s.dbs.filter(function (x) { return t.dbs.indexOf(x) < 0; }) : [];
    h += '<div class="kv kv-3"><div><small>Jadwal</small>' + (t.on ? esc(schedLabel(t)) : '<span class="muted">Tidak terjadwal</span>') + '</div>' +
      '<div><small>Cakupan</small>' + esc(scopeLabel(s)) + (skipped.length ? '<span class="muted small" style="display:block">tidak diuji: ' + skipped.map(esc).join(', ') + '</span>' : '') + (t.full ? '' : '<span class="muted small" style="display:block">full tidak diuji</span>') + '</div>' +
      '<div><small>Runner uji</small>' + (r ? '<a href="#/runner/' + encodeURIComponent(r.name) + '">' + esc(r.name) + '</a> ' + runnerPill(r) : '<span class="muted">Belum ada runner</span>') + '</div></div>';
    return h + '<p class="small muted" style="margin-top:6px">Dipulihkan ke container sementara di mesin runner, lalu dihapus. Hasilnya tidak mengubah database cadangan.</p>';
  }
  // Bagian database cadangan di tab Cadangan: dipulihkan terjadwal dari full + binlog, untuk kejadian tidak terduga.
  function cadSection(s) {
    var r = cadRunnerOf(s), sb = s.sched.standby, c = s.standby, running = S.standbyRun && S.standbyRun.server === s.name;
    var canRun = canWrite() && s.status === 'active' && fullChoices(s).length && S.runners.some(function (x) { return runnerFor(s, x); }) && !nasDown() && !S.restoreRun && !S.standbyRun && !s.runnerPending;
    var btn = '<span style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">' +
      (c && r ? '<button class="btn btn-sm" data-act="cad-promote" data-srv="' + esc(s.name) + '"' + (canWrite() && !s.runnerPending && !running && r.state !== 'dead' && r.state !== 'off' ? '' : ' disabled') + '>Naikkan ke production</button>' : '') +
      '<button class="btn btn-sm" data-act="runner-change" data-role="cad" data-srv="' + esc(s.name) + '"' + (canWrite() && s.status !== 'off' && !s.runnerPending && !running && S.runners.length ? '' : ' disabled') + '>Ganti runner cadangan</button>' +
      '<button class="btn btn-sm" data-act="sched-standby" data-srv="' + esc(s.name) + '"' + (canWrite() && s.status !== 'off' && !running ? '' : ' disabled') + '>Ubah jadwal</button>' +
      '<button class="btn btn-sm" data-act="sb-run" data-srv="' + esc(s.name) + '"' + (canRun ? '' : ' disabled') + '>Restore sekarang</button></span>';
    var h = '<div class="section-title" style="margin-top:0;flex-wrap:wrap"><h2>Database cadangan</h2>' + btn + '</div>';
    if (s.status !== 'off' && sameServer(s, r)) h += '<div class="banner banner-bad"><b>' + esc(r.name) + ' berada di server yang sama dengan container ini.</b> Uji dan cadangannya tidak berjalan di sini. Pilih runner lain.</div>';
    if (fullRows(s).length && (s.status === 'off' || !S.runners.some(function (x) { return runnerFor(s, x); }))) h += '<div class="banner banner-info"><b>' + (s.status === 'off' ? 'Container nonaktif tidak di-restore oleh runner.' : 'Tidak ada runner sehat di server lain.') + '</b> Untuk memulihkan ke server mana pun tanpa runner, pakai <a href="#/container/' + encodeURIComponent(s.name) + '/manual/baru">Restore manual</a>.</div>';
    if (!sb.on && !c) return h + '<div class="card"><div class="empty"><b>Tidak disiapkan untuk container ini</b>Database cadangan berisi full dan binlog yang dipulihkan terjadwal, siap dipakai saat downtime. Aktifkan lewat Ubah jadwal, atau buat sekali lewat Restore sekarang.' + (r ? ' Runner cadangan: <a href="#/runner/' + encodeURIComponent(r.name) + '">' + esc(r.name) + '</a>.' : '') + '</div></div>';
    if (!r) return h + '<div class="card"><div class="empty"><b>Belum ada runner</b><a href="#/runner">Tambahkan runner</a> supaya database cadangan bisa disiapkan.</div></div>';
    if (s.runnerPending) h += '<div class="banner banner-info"><span class="spin"></span> <b>Pindah ke ' + esc(s.runnerPending) + '.</b>' + (c ? ' Runner baru memulihkan dan menguji dulu; database cadangan di ' + esc(s.runnerCad) + ' baru dihapus setelah lolos.' : '') + '</div>';
    if (running) h += '<div class="banner banner-info"><span class="spin"></span> <b>Sedang di-restore di ' + esc(r.name) + '.</b> Database cadangan yang ada tetap dipakai sampai hasil baru lolos pemeriksaan.</div>';
    if (s.status !== 'off' && r.state === 'dead') h += '<div class="banner banner-bad"><b>' + esc(r.name) + ' tidak merespons.</b> Kondisi database cadangan tidak bisa dipastikan, dan database cadangan tidak di-restore.</div>';
    if (s.status !== 'off' && r.state === 'dl' && !nasDown()) h += '<div class="banner banner-bad"><b>' + esc(r.name) + ' gagal mengunduh file.</b> Database cadangan yang ada tetap hidup, tetapi tidak di-restore sampai masalah ini selesai.</div>';
    if (s.status !== 'off' && r.state === 'off') h += '<div class="banner banner-warn"><b>' + esc(r.name) + ' nonaktif.</b> Pindahkan container ini ke runner lain.</div>';
    if (s.status === 'off') h += '<div class="banner banner-warn"><b>Container nonaktif.</b> Cadangannya tidak di-restore.</div>';
    if (!sb.on && c) h += '<div class="banner banner-info"><b>Hasil Restore sekarang, tanpa jadwal.</b> Database cadangan ini tidak di-restore otomatis. Atur lewat Ubah jadwal.</div>';
    var stale = alertsFor(s.name).some(function (a) { return a.kind === 'Cadangan'; });
    h += '<div class="kv kv-3">' +
      '<div><small>Jadwal</small>' + (sb.on ? 'Pulihkan full ' + esc(schedLabel(sb)).toLowerCase() + (sb.binlog === 'off' ? '' : '<span class="muted small" style="display:block">binlog baru diterapkan tiap ' + esc(sb.binlog) + '</span>') : '<span class="muted">Tidak terjadwal</span>') + '</div>' +
      '<div' + (stale ? ' class="kv-bad"' : '') + '><small>Data sampai</small>' + (c ? esc(c.until) + (stale ? ' · melewati jadwal' : '') : 'Menunggu restore pertama') + '</div>' +
      '<div><small>Full yang dipakai</small>' + (c ? '<span class="mono">' + esc(c.full) + '</span>' : '—') + '</div>' +
      '<div><small>Lokasi</small>' + (c ? '<code>' + esc(r.host) + ':' + c.port + '</code>' : esc(r.host)) + '</div>' +
      '<div><small>Akses</small>' + (c ? '<code>read_only</code>, port hanya untuk admin' : '—') + '</div>' +
      '<div><small>Runner cadangan</small><a href="#/runner/' + encodeURIComponent(r.name) + '">' + esc(r.name) + '</a> ' + runnerPill(r) + '</div>' +
      '</div>';
    h += '<p class="small muted" style="margin-top:6px">Database cadangan lama baru diganti setelah hasil baru lolos pemeriksaan. Memindahkan aplikasi ke database cadangan tetap keputusan manusia.</p>';
    var hist = S.standby.filter(function (x) { return x.server === s.name; });
    if (hist.length) h += section('Riwayat restore') + tbl(['Waktu', 'Full', 'Data sampai', ['Durasi', 'num'], 'Hasil'], hist.map(function (x) {
      return '<tr class="row-' + (x.skip ? 'warn' : x.ok ? 'ok' : 'bad') + '"><td>' + esc(x.when) + '</td><td class="mono">' + esc(x.full) + '</td><td>' + esc(x.until) + '</td><td class="num">' + esc(x.dur) + '</td><td>' + restorePill(x) + (x.manual ? ' ' + pill('wait', 'Manual') : '') + (x.reason ? ' <span class="muted small">' + esc(x.reason) + '</span>' : '') + '</td></tr>';
    }));
    return h;
  }

  // ---------- Halaman: Tambah runner (wizard 3 langkah) ----------
  function runnerNameError(v) {
    if (!v) return 'Isi nama runner.';
    if (/\s/.test(v)) return 'Tidak boleh berisi spasi.';
    if (!/^[A-Za-z0-9]/.test(v)) return 'Harus diawali huruf atau angka.';
    if (!NAME_RE.test(v)) return 'Hanya huruf, angka, _ . - yang diizinkan.';
    if (v.length > 64) return 'Maksimal 64 karakter.';
    if (v.toLowerCase() === 'tambah') return 'Nama “tambah” dipakai oleh aplikasi.';
    if (S.runners.some(function (r) { return r.name.toLowerCase() === v.toLowerCase(); })) return 'Nama “' + esc(v) + '” sudah terdaftar.';
    return '';
  }
  function runWizCanNext(w) { return !!w.name && !runnerNameError(w.name); }
  function pageRunnerWizard(fresh) {
    crumbs([['Runner', '#/runner'], 'Tambah runner']);
    if (!canWrite()) return pageHead('Tambah runner') + '<div class="card"><div class="empty"><b>Aksi tulis dimatikan</b>Isi <code>DASHBOARD_PASS</code> di <code>.env</code> lalu restart container LumbungDB.</div></div>';
    if (!S.runWiz || S.runWiz.finished && fresh) S.runWiz = { step: 1, name: '' };
    var w = S.runWiz, labels = ['Nama', 'Pasang runner', 'Verifikasi'];
    var h = pageHead('Tambah runner', 'Pendaftaran selesai setelah runner mengirim heartbeat dan berhasil mengunduh file uji.');
    h += '<ol class="wiz-steps">' + labels.map(function (l, i) {
      var n = i + 1, cls = n < w.step ? 'done' : (n === w.step ? 'now' : '');
      return '<li class="' + cls + '"><button disabled><span class="n">' + (n < w.step ? '✓' : n) + '</span><span class="lbl">' + l + '</span></button></li>';
    }).join('') + '</ol><div class="wiz-box">';
    if (w.step === 1) {
      var err = w.name ? runnerNameError(w.name) : '';
      h += '<h2>Daftarkan runner baru</h2>' +
        '<div class="field"><label for="rw-name">Nama runner</label><input type="text" id="rw-name" data-in="rw-name" value="' + esc(w.name) + '" placeholder="mis. runner-c" autocomplete="off" spellcheck="false"' + (err ? ' class="invalid"' : '') + '>' +
        '<div id="rw-msg">' + (w.name ? (err ? '<div class="msg-err">✕ ' + err + '</div>' : '<div class="msg-ok">✓ Nama tersedia</div>') : '') + '</div></div>' +
        '<p class="hint" style="margin:-6px 0 14px">Pasang di VM atau server khusus, boleh VM LumbungDB sendiri. Server tidak perlu diisi; runner membacanya dari mesinnya.</p>' +
        (w.confErr ? '<div class="banner banner-bad"><b>Tidak bisa menulis <code>config/runners.conf</code>.</b> File read-only atau izinnya salah. Runner belum didaftarkan dan token belum dibuat; perbaiki izin file lalu coba lagi.</div>' : '') +
        '<div class="wiz-actions"><button class="btn btn-pri" id="rw-next1" data-act="rw-create"' + (runWizCanNext(w) ? '' : ' disabled') + '>Lanjut: buat perintah ›</button><a class="btn" href="#/runner">Batal</a></div>';
    } else if (w.step === 2) {
      var env = 'LUMBUNG_URL=https://lumbung.rumahsakit.co.id\nLUMBUNG_RUNNER_TOKEN=' + w.token + '\nSCRATCH_DIR=/mnt/scratch';
      var cmd = 'docker compose -f docker-compose.runner.yml --env-file .env.runner up -d';
      h += '<h2>Pasang ' + esc(w.name) + '</h2>' +
        '<div class="once"><b>Token hanya ditampilkan sekali.</b> Baris <code>' + esc(w.name) + ' = …</code> sudah ditambahkan ke <code>config/runners.conf</code>. Simpan token sekarang; setelah lanjut ke langkah 3, aplikasi tidak menampilkannya lagi.</div>' +
        '<div class="field"><label>Token runner</label><div class="copy"><code class="tok">' + w.token + '</code><button class="btn" data-act="copy" data-copy="' + w.token + '">Salin</button></div></div>' +
        '<div class="field"><label>Isi <code>.env.runner</code></label><div class="copy"><pre>' + esc(env) + '</pre><button class="btn" data-act="copy" data-copy="' + esc(env) + '">Salin</button></div></div>' +
        '<p class="hint" style="margin:-6px 0 14px">Runner tidak butuh akses NFS. Sebelumnya: pastikan mesin ini bisa menjangkau LumbungDB lewat HTTPS, dan siapkan disk scratch terenkripsi di <code>SCRATCH_DIR</code>.</p>' +
        '<div class="field"><label>Salin <code>docker-compose.runner.yml</code> ke mesin itu, lalu jalankan</label><div class="copy"><pre>' + cmd + '</pre><button class="btn" data-act="copy" data-copy="' + cmd + '">Salin</button></div></div>' +
        '<label class="check"><input type="checkbox" data-in="rw-saved"' + (w.saved ? ' checked' : '') + '> Saya sudah menyimpan token dan menjalankan perintah di mesin runner</label>' +
        '<div class="wiz-actions"><button class="btn btn-pri" id="rw-next2" data-act="rw-verify"' + (w.saved ? '' : ' disabled') + '>Lanjut: verifikasi ›</button><button class="link-btn danger right" data-act="rw-cancel">Batalkan pendaftaran</button></div>';
    } else {
      var r = w.runner, hb = r.state !== 'wait';
      h += '<h2>Verifikasi ' + esc(r.name) + '</h2><ul class="checks">' +
        '<li class="done"><span class="ic">✓</span><div>Token dibuat <span class="muted">' + w.createdAt + ' WIB</span></div></li>' +
        (hb ? '<li class="done"><span class="ic">✓</span><div>Heartbeat pertama diterima <span class="muted">server terbaca: ' + esc(r.host) + ' · ' + r.ip + ' · versi ' + esc(r.ver) + '</span></div></li><li class="done"><span class="ic">✓</span><div>File uji berhasil diunduh <span class="muted">scratch ' + r.free + '% bebas · ' + esc(r.freeTxt) + '</span></div></li>'
          : '<li class="todo"><span class="ic"><span class="spin"></span></span><div><b style="color:var(--text)">Menunggu heartbeat pertama</b><span class="muted">runner menghubungi LumbungDB setelah container berjalan</span></div></li><li class="todo"><span class="ic"></span><div>Unduh file uji dari LumbungDB</div></li>') + '</ul>';
      if (hb) h += '<div class="banner banner-ok"><b>' + esc(r.name) + ' siap dipakai.</b> Belum ada container yang ditangani; tetapkan lewat tab Uji restore di detail container.</div>';
      else h += '<p class="small muted">Prototype: heartbeat disimulasikan dalam beberapa detik.</p>';
      h += '<div class="wiz-actions"><a class="btn btn-pri" href="#/runner/' + encodeURIComponent(r.name) + '">Buka detail ' + esc(r.name) + '</a><a class="btn" href="#/runner">' + (hb ? 'Kembali ke daftar' : 'Tutup, cek nanti') + '</a></div>';
    }
    return h + '</div>';
  }
  function hasOpenToken() { return (S.wizard && S.wizard.token) || (S.runWiz && S.runWiz.token) || S.rm.some(function (x) { return x.token; }); }
  function applyRunnerSim(kind) {
    var base = JSON.parse(BASE).runners;
    S.runnerSim = kind;
    S.runners = kind === 'none' ? [] : base;
    S.runWiz = null;
    S.runners.forEach(function (r) {
      r.state = 'ok'; r.since = ''; r.dlErr = '';
      if (r.name === 'runner-b') r.hb = '1 menit lalu';
    });
    var a = findRunner('runner-a'), b = findRunner('runner-b');
    if (kind === 'normal' && b) { b.state = 'dl'; b.since = '03:12'; b.dlErr = 'Unduhan terputus berulang (connection reset)'; }
    if (kind === 'dead' && a) { a.state = 'dead'; a.since = '07:48'; a.hb = '27 menit lalu'; a.free = null; a.freeTxt = ''; }
    if (kind === 'disk' && a) { a.state = 'low'; a.since = '06:20'; a.free = 9; a.freeTxt = '180 GB dari 2 TB'; }
  }

  // ---------- Restore: Restore sekarang dan naikkan ke production ----------
  // Restore dijalankan runner cadangan, selalu ke volume baru. Memindahkan aplikasi ke container yang dinaikkan tetap dikerjakan admin.
  function logRestore(s, act, detail) {
    S.restoreLog = [{ server: s.name, at: '30 Okt ' + nowLabel(), act: act, detail: detail, by: 'admin' }].concat(S.restoreLog);
  }
  function p2n(n) { return String(n).padStart(2, '0'); }
  function keyLabel(k) { return Math.floor(k / 10000) + ' Okt ' + p2n(Math.floor(k / 100) % 100) + ':' + p2n(k % 100); }
  function fullNameKey(name) {
    var m = /^\d{4}-\d{2}-(\d{2})_(\d{2})(\d{2})/.exec(name);
    return m ? +m[1] * 10000 + +m[2] * 100 + +m[3] : 0;
  }
  function fullRows(s) { return riwRows(s).filter(function (r) { return r[2] === 'full'; }); }
  function fullStem(file) { return file.replace(/\.xbstream\.gz.*$/, ''); }
  // Pilihan full: terakhir yang lolos uji, dan yang lebih baru bila belum pernah diuji. Full yang gagal uji tidak ditawarkan.
  function fullChoices(s) {
    var rows = fullRows(s), out = [], tested = S.restores.filter(function (r) { return r.server === s.name && !r.skip && !(r.scope && !r.scope.full); });
    if (!rows.length) return out;
    var newest = fullStem(rows[0][3]);
    var resolve = function (n) { return /^\d{4}-/.test(n) ? n : newest; };
    var passes = tested.filter(function (r) { return r.fail !== 'full'; }).concat(S.standby.filter(function (r) { return r.server === s.name && r.ok; }))
      .sort(function (x, y) { return fullNameKey(resolve(y.full)) - fullNameKey(resolve(x.full)); });
    var pass = passes[0], passName = pass ? resolve(pass.full) : null;
    var failed = tested.filter(function (r) { return r.fail === 'full'; }).map(function (r) { return resolve(r.full); });
    var row = function (n) { return rows.filter(function (r) { return fullStem(r[3]) === n; })[0]; };
    if (passName && row(passName)) out.push({ name: passName, row: row(passName), passed: true, when: pass.when });
    if (newest !== passName && failed.indexOf(newest) < 0) out.push({ name: newest, row: rows[0], passed: false });
    return out;
  }
  function binlogRows(s, startKey) {
    var num = function (r) { return +/mysql-bin\.(\d+)/.exec(r[3])[1]; };
    return riwRows(s).filter(function (r) { return r[2] === 'binlog' && r[0] >= startKey; }).sort(function (x, y) { return num(x) - num(y); });
  }
  // Batas pemulihan untuk full terpilih: berhenti sebelum nomor binlog yang hilang.
  function binlogRange(s, startKey) {
    var num = function (r) { return +/mysql-bin\.(\d+)/.exec(r[3])[1]; }, rows = binlogRows(s, startKey), stopped = null;
    if (s.binlogGap) {
      var g = +s.binlogGap.no, before = rows.filter(function (r) { return num(r) < g; });
      if (rows.length && before.length < rows.length && before.length) { stopped = s.binlogGap.no; rows = before; }
    }
    return { rows: rows, stopped: stopped, maxKey: rows.length ? rows[rows.length - 1][0] : startKey };
  }
  function restoreLogSection(s) {
    var list = S.restoreLog.filter(function (x) { return x.server === s.name; });
    if (!list.length) return '';
    return section('Riwayat tindakan restore', '<span class="muted small">dicatat LumbungDB; langkah yang dijalankan admin di mesin runner tidak terpantau</span>') +
      tbl(['Waktu', 'Tindakan', 'Rincian', 'Oleh'], list.map(function (x) {
        return '<tr><td>' + esc(x.at) + '</td><td>' + esc(x.act) + '</td><td>' + esc(x.detail) + '</td><td>' + esc(x.by) + '</td></tr>';
      }));
  }

  // Container baru dari salinan container lain: nama, token, dan folder NAS sendiri, rantai binlog mulai dari nol.
  function containerFromSource(s, name, host, ip, promoted) {
    var c = JSON.parse(JSON.stringify(s));
    c.name = name; c.host = host; c.ip = ip; c.reg = 'baru saja'; c.regAt = nowLabel(); c.status = 'wait-backup'; c.hb = 'baru saja';
    delete c.offSince; c.binlogGap = null; c.runnerPending = null;
    c.standby = null; c.sched.standby.on = false; c.promoted = promoted;
    c.full = '—'; c.dump = '—'; c.dumps = []; c.recent = []; c.log = ''; c.restore = null; c.fullDur = '—';
    c.files = { full: 0, dump: 0, binlog: 0 }; c.upload = { pending: 1, lastOk: null, err: null };
    if (c.bsched) { c.bsched.want = null; c.bsched.err = null; c.bsched.errAt = null; }
    var dr = runnerForPromoted(host);
    c.runner = dr ? dr.name : null; c.runnerCad = dr ? dr.name : null;
    return c;
  }
  function firstFullSim(c, size) {
    setTimeout(function firstFull() {
      if (findServer(c.name) !== c || c.status !== 'wait-backup') return;
      if (nasDown()) { setTimeout(firstFull, 2000); return; } // upload ditolak 503, agen mengulang
      c.status = 'active'; c.full = 'baru saja'; c.upload = { pending: 0, lastOk: 'baru saja', err: null }; c.files = { full: 1, dump: 0, binlog: 0 };
      c.recent = [['full', '2026-10-30_' + nowLabel().replace(':', '') + '.xbstream.gz', size, 'baru saja']];
      toast('Full pertama ' + c.name + ' diterima'); rerender();
    }, 4000);
  }

  // ---------- Halaman: Restore manual ----------
  // Memulihkan seluruh container ke server mana pun dengan satu perintah, tanpa runner.
  // Sesi punya token dan batas waktu sendiri; hasil yang lolos pemeriksaan langsung terdaftar sebagai container baru.
  var RM_URL = 'https://lumbung.rumahsakit.co.id';
  var RM_MAX_HOURS = 24, RM_EXTEND_HOURS = 2;
  var RM_PILL = { wait: ['wait', 'Menunggu script'], run: ['wait', 'Berjalan'], fail: ['bad', 'Gagal, bisa diulang'], done: ['ok', 'Terdaftar'], expired: ['off', 'Kedaluwarsa'], revoked: ['off', 'Dicabut'] };
  var rmSeq = 100;
  function rmActive(x) { return x.status === 'wait' || x.status === 'run' || x.status === 'fail'; }
  function rmFind(id) { return S.rm.filter(function (x) { return x.id === id; })[0]; }
  function rmPill(x) { var p = RM_PILL[x.status]; return (x.status === 'run' ? '<span class="spin"></span> ' : '') + pill(p[0], p[1]); }
  function rmCmd(x) { return 'curl -fsSL ' + RM_URL + '/restore.sh | RESTORE_TOKEN=' + x.token + ' sh'; }
  function rmLeft(x) {
    var ms = x.expires - S.clock.getTime();
    if (ms < 60000) return 'kurang dari 1 menit';
    var m = Math.floor(ms / 60000), j = Math.floor(m / 60);
    return (j ? j + ' jam ' : '') + (m % 60) + ' mnt';
  }
  function rmLimitText(x) {
    return rmActive(x) ? 'Sampai ' + hhmm(new Date(x.expires)) + ' WIB · sisa <span class="rm-left" data-id="' + x.id + '">' + rmLeft(x) + '</span>' : (x.endAt ? 'Berakhir ' + esc(x.endAt) : '—');
  }
  // Tiap full diberi bukti yang ada: lolos atau gagal uji, atau belum pernah diuji.
  function rmFulls(s) {
    var rows = fullRows(s);
    if (!rows.length) return [];
    var newest = fullStem(rows[0][3]), resolve = function (n) { return /^\d{4}-/.test(n) ? n : newest; };
    var tests = S.restores.filter(function (r) { return r.server === s.name && !r.skip && !(r.scope && !r.scope.full); });
    return rows.map(function (r) {
      var name = fullStem(r[3]), done = tests.filter(function (t) { return resolve(t.full) === name; })[0];
      var cad = S.standby.filter(function (t) { return t.server === s.name && t.ok && t.full === name; })[0];
      var state = done && done.fail === 'full' ? 'bad' : done || cad ? 'ok' : 'new';
      var note = state === 'bad' ? 'gagal uji ' + done.when : state === 'ok' ? 'lolos ' + (done ? 'uji ' + done.when : 'restore ' + cad.when) : 'belum diuji';
      return { name: name, size: r[4], state: state, label: keyLabel(fullNameKey(name)) + ' · ' + r[4] + ' · ' + note };
    });
  }
  function rmSuggestName(s) {
    var n = 2, taken = function (v) { return !!nameError(v); };
    while (taken(s.name + '-' + n)) n++;
    return s.name + '-' + n;
  }
  function rmPickSource(f, s) {
    var fulls = rmFulls(s), pass = fulls.filter(function (x) { return x.state === 'ok'; })[0];
    f.src = s.name; f.full = (pass || fulls[0]).name; f.name = rmSuggestName(s);
  }
  function rmFormErrors(f) {
    var ne = nameError(f.name), port = +f.port;
    return { name: ne, port: /^\d+$/.test(f.port) && port >= 1024 && port <= 65535 || port === 3306 ? '' : 'Port harus angka 1024–65535.' };
  }
  function rmUntil(s, f) {
    var key = fullNameKey(f.full);
    if (f.binlog === 'none') return { until: keyLabel(key), stopped: null };
    var range = binlogRange(s, key);
    return { until: keyLabel(range.maxKey), stopped: range.stopped };
  }
  function rmPreview(s, f) {
    var info = rmFulls(s).filter(function (x) { return x.name === f.full; })[0], u = rmUntil(s, f), m = D.metrics[s.name], h = '';
    if (info.state === 'bad') h += '<div class="banner banner-bad"><b>Full ini gagal uji restore.</b> Script tetap memeriksa hasilnya, tetapi kemungkinan besar gagal. Pilih full lain bila ada.</div>';
    if (info.state === 'new') h += '<div class="banner banner-warn"><b>Full ini belum pernah diuji.</b> Script memeriksa hasilnya sebelum mendaftarkannya.</div>';
    if (u.stopped) h += '<div class="banner banner-bad"><b>Binlog ' + esc(u.stopped) + ' hilang.</b> Data hanya sampai ' + esc(u.until) + '.</div>';
    if (s.status === 'active' && agentAlive(s)) h += '<div class="banner banner-warn"><b>' + esc(s.name) + ' masih mengirim heartbeat.</b> Container baru tidak menggantikannya. Pastikan yang lama tidak lagi menerima tulisan sebelum aplikasi dipindahkan, supaya data tidak terpecah.</div>';
    h += '<ul><li>Dipulihkan sebagai container baru <b>' + esc(f.name || '—') + '</b>, dengan data sampai <b>' + esc(u.until) + '</b>. ' + esc(s.name) + ' tidak disentuh.</li>' +
      '<li>Image <code>zmutclik/mariadb:lumbungdb-' + esc(s.agent ? s.agent.tag : '—') + '</code>, sama dengan sumber, supaya MariaDB tidak naik versi.</li>' +
      '<li>Unduh ' + esc(info.size) + (f.binlog === 'all' ? ' ditambah binlog' : '') + '. Butuh ruang disk ' + (m ? 'sekitar ' + num(m.dbSize[0], 0) + ' GB untuk data, ditambah unduhan' : 'sebesar data asli, ditambah unduhan') + '.</li></ul>';
    return h;
  }
  function rmBase(name) { return '#/container/' + encodeURIComponent(name) + '/manual'; }
  function manualTab(s, sub) {
    if (sub === 'baru') return manualNew(s);
    return sub ? manualDetail(s, +sub) : manualList(s);
  }
  function manualList(s) {
    var base = rmBase(s.name), has = fullRows(s).length, list = S.rm.filter(function (x) { return x.source === s.name; });
    var btn = canWrite() && has ? '<a class="btn btn-sm btn-pri" href="' + base + '/baru">+ Restore manual baru</a>' : '<span class="btn btn-sm btn-pri disabled" aria-disabled="true" title="' + (has ? 'Butuh login dashboard' : 'Belum ada full backup') + '">+ Restore manual baru</span>';
    var h = '<div class="section-title" style="margin-top:0;flex-wrap:wrap"><h2>Restore manual</h2><span style="margin-left:auto">' + btn + '</span></div>';
    h += '<p class="small muted" style="margin:0 0 10px">Memulihkan ' + esc(s.name) + ' ke server mana pun dengan satu perintah, tanpa runner. Hasil yang lolos pemeriksaan langsung terdaftar sebagai container baru.</p>';
    if (!list.length) h += '<div class="card"><div class="empty"><b>' + (has ? 'Belum ada restore manual' : 'Belum ada full backup') + '</b>' + (has ? 'Pilih full backup, lalu jalankan satu perintah di server tujuan.' : 'Restore manual butuh minimal satu full di NAS.') + '</div></div>';
    else h += tbl(['Dibuat', 'Full', 'Container baru', 'Server tujuan', 'Status', 'Batas waktu'], list.map(function (x) {
      var p = RM_PILL[x.status][0];
      return '<tr' + (p === 'bad' ? ' class="row-bad"' : '') + '><td><a href="' + base + '/' + x.id + '">' + esc(x.created) + '</a></td><td class="mono">' + esc(x.full) + '</td><td>' + (x.status === 'done' && findServer(x.name) ? '<a href="#/container/' + encodeURIComponent(x.name) + '"><b>' + esc(x.name) + '</b></a>' : esc(x.name)) + '</td><td>' + esc(x.host || '—') + '</td><td>' + rmPill(x) + '</td><td>' + rmLimitText(x) + '</td></tr>';
    }));
    h += '<p class="small muted" style="margin-top:8px">Script butuh LumbungDB yang hidup. Bila LumbungDB mati, atau perlu pemulihan ke waktu tertentu atau satu database, pakai file di NAS mengikuti README image.</p>';
    return h;
  }
  function manualNew(s) {
    var base = rmBase(s.name), fresh = location.hash !== lastRoute;
    var head = '<div class="section-title" style="margin-top:0"><h2>Restore manual baru</h2><span class="muted small">perintah dan token dibuat setelah ini; belum ada yang dijalankan</span></div>';
    if (!canWrite()) return head + '<div class="card"><div class="empty"><b>Aksi tulis dimatikan</b>Isi <code>DASHBOARD_PASS</code> di <code>.env</code> lalu restart container LumbungDB.</div></div>';
    if (!fullRows(s).length) return head + '<div class="card"><div class="empty"><b>Belum ada full backup</b>Restore manual butuh minimal satu full di NAS.</div></div>';
    if (fresh || !S.rmForm || S.rmForm.src !== s.name) {
      S.rmForm = { binlog: 'all', port: '3306', hours: 4 };
      rmPickSource(S.rmForm, s);
    }
    var f = S.rmForm, err = rmFormErrors(f), fulls = rmFulls(s), ok = !err.name && !err.port && !nasDown();
    var h = head + '<div class="wiz-box">';
    h += '<div class="field"><label for="rm-full">Full backup</label><select id="rm-full" data-in="rm-full" style="width:100%">' + fulls.map(function (x) { return '<option value="' + esc(x.name) + '"' + (x.name === f.full ? ' selected' : '') + '>' + esc(x.label) + '</option>'; }).join('') + '</select></div>';
    h += '<div class="field"><label for="rm-binlog">Binlog</label><select id="rm-binlog" data-in="rm-binlog" style="width:100%"><option value="all"' + (f.binlog === 'all' ? ' selected' : '') + '>Terapkan sampai data terbaru</option><option value="none"' + (f.binlog === 'none' ? ' selected' : '') + '>Full saja</option></select><div class="hint">Pilih Full saja untuk kembali ke sebelum kesalahan data, karena binlog akan mengulang kesalahan itu.</div></div>';
    h += '<div class="grid2"><div class="field"><label for="rm-name">Nama container baru</label><input type="text" id="rm-name" data-in="rm-name" value="' + esc(f.name) + '" autocomplete="off" spellcheck="false" style="width:100%"' + (err.name ? ' class="invalid"' : '') + '><div id="rm-name-msg">' + (err.name ? '<div class="msg-err">✕ ' + err.name + '</div>' : '') + '</div></div>' +
      '<div class="field"><label for="rm-port">Port</label><input type="text" inputmode="numeric" id="rm-port" data-in="rm-port" value="' + esc(f.port) + '" autocomplete="off" style="width:100%"' + (err.port ? ' class="invalid"' : '') + '><div id="rm-port-msg">' + (err.port ? '<div class="msg-err">✕ ' + err.port + '</div>' : '') + '</div></div></div>';
    h += '<div class="field"><label for="rm-hours">Batas waktu</label><select id="rm-hours" data-in="rm-hours" style="width:100%">' + [2, 4, 8, 24].map(function (n) { return '<option value="' + n + '"' + (f.hours === n ? ' selected' : '') + '>' + n + ' jam</option>'; }).join('') + '</select><div class="hint">Sampai batas ini restore harus selesai. Bisa diperpanjang selama sesi aktif, paling lama ' + RM_MAX_HOURS + ' jam sejak dibuat.</div></div>';
    h += '<div id="rm-preview">' + rmPreview(s, f) + '</div>';
    if (f.confErr) h += '<div class="banner banner-bad"><b>Tidak bisa menulis <code>config/agents.conf</code>.</b> Hasil restore tidak akan bisa didaftarkan, jadi token belum dibuat. Perbaiki izin file lalu coba lagi.</div>';
    h += '<div class="wiz-actions"><button class="btn btn-pri" id="rm-create" data-act="rm-create"' + (ok ? '' : ' disabled') + (nasDown() ? ' title="NAS bermasalah, file tidak bisa diunduh"' : '') + '>Buat perintah restore ›</button><a class="btn" href="' + base + '">Batal</a></div></div>';
    return h;
  }
  function rmCheckForm() {
    var f = S.rmForm, err = rmFormErrors(f), n = document.getElementById('rm-name'), p = document.getElementById('rm-port');
    n.classList.toggle('invalid', !!err.name); p.classList.toggle('invalid', !!err.port);
    document.getElementById('rm-name-msg').innerHTML = err.name ? '<div class="msg-err">✕ ' + err.name + '</div>' : '';
    document.getElementById('rm-port-msg').innerHTML = err.port ? '<div class="msg-err">✕ ' + err.port + '</div>' : '';
    document.getElementById('rm-preview').innerHTML = rmPreview(findServer(f.src), f);
    document.getElementById('rm-create').disabled = !!err.name || !!err.port || nasDown();
  }
  function rmSteps(x) {
    var st = [{ k: 'token', t: 'Token diterima dan terikat ke mesin ini' }, { k: 'pre', t: 'Cek Docker, ruang disk, dan port ' + x.port }, { k: 'dl', t: 'Unduh full ' + x.full + ', cek SHA256' }, { k: 'unpack', t: 'Ekstrak xbstream + mariadb-backup --prepare' }];
    if (x.binlog === 'all') st.push({ k: 'binlog', t: 'Terapkan binlog sampai ' + x.until });
    st.push({ k: 'check', t: 'Jalankan MariaDB read_only tanpa event scheduler; database terbuka, tabel utama terbaca' });
    st.push({ k: 'reg', t: 'Buka tulisan, nyalakan agen, dan daftar sebagai ' + x.name });
    return st;
  }
  function manualDetail(s, id) {
    var x = rmFind(id), base = rmBase(s.name);
    if (!x || x.source !== s.name) return '<div class="card"><div class="empty"><b>Restore manual tidak ada</b><a href="' + base + '">Kembali ke daftar</a></div></div>';
    var acts = '';
    if (rmActive(x) && canWrite()) {
      var capped = x.expires + RM_EXTEND_HOURS * 3600000 > x.createdAt + RM_MAX_HOURS * 3600000;
      acts = '<span style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end"><button class="btn btn-sm" data-act="rm-extend" data-id="' + x.id + '"' + (capped ? ' disabled title="Sudah mencapai batas ' + RM_MAX_HOURS + ' jam"' : '') + '>Perpanjang ' + RM_EXTEND_HOURS + ' jam</button><button class="btn btn-sm btn-danger" data-act="rm-revoke" data-id="' + x.id + '">Cabut</button></span>';
    }
    var h = '<p class="small" style="margin:0 0 8px"><a href="' + base + '">‹ Semua restore manual</a></p><div class="section-title" style="margin-top:0;flex-wrap:wrap"><h2>' + esc(x.name) + '</h2> ' + rmPill(x) + '<span class="muted small">dibuat ' + esc(x.created) + ' oleh ' + esc(x.by) + '</span>' + acts + '</div>';
    h += '<div class="kv kv-3"><div><small>Full</small><span class="mono">' + esc(x.full) + '</span> · ' + esc(x.fullSize) + '</div><div><small>Data sampai</small>' + esc(x.until) + (x.binlog === 'none' ? ' <span class="muted small">full saja</span>' : '') + '</div><div><small>Batas waktu</small>' + rmLimitText(x) + '</div>' +
      '<div><small>Server tujuan</small>' + esc(x.host || 'Terbaca saat script berjalan') + '</div><div><small>Port</small>' + x.port + '</div><div><small>Image</small><code>' + esc(x.tag) + '</code>, sama dengan sumber</div></div>';
    if (x.status === 'wait') {
      if (x.token) {
        h += '<div class="once" style="margin-top:14px"><b>Token hanya ditampilkan sekali.</b> Salin perintah ini sekarang. Bila halaman ini ditinggalkan, token tidak tampil lagi, jadi cabut sesi ini dan buat yang baru.</div>' +
          '<div class="field"><label>Jalankan di server tujuan, sebagai root atau user yang boleh memakai Docker</label><div class="copy"><pre>' + esc(rmCmd(x)) + '</pre><button class="btn" data-act="copy" data-copy="' + esc(rmCmd(x)) + '">Salin</button></div></div>' +
          '<p class="hint">Butuh Docker dan akses HTTPS ke LumbungDB. Pemakaian pertama mengikat token ke mesin itu, jadi mesin lain ditolak. Token ikut tercatat di riwayat shell, tetapi tidak berlaku lagi setelah sesi berakhir. Untuk membaca script lebih dulu: <code>curl -fsSL ' + RM_URL + '/restore.sh -o restore.sh</code>; script tidak berisi token.</p>';
      } else h += '<div class="banner banner-warn" style="margin-top:14px"><b>Token sudah disembunyikan.</b> Bila perintahnya belum tersimpan, cabut sesi ini dan buat yang baru.</div>';
    }
    if (x.host && x.sameAsSource && rmActive(x)) h += '<div class="banner banner-warn" style="margin-top:14px"><b>Server tujuan sama dengan server ' + esc(x.source) + '.</b> Bila server ini mati, container baru ikut mati. Pakai server lain untuk insiden yang menyangkut server ini.</div>';
    if (x.status === 'fail') h += '<div class="banner banner-bad" style="margin-top:14px"><b>Script berhenti.</b> ' + esc(x.note) + ' Perbaiki, lalu jalankan ulang perintah yang sama di mesin yang sama sebelum batas waktu. Unduhan yang sudah ada dilanjutkan.</div>';
    if (x.status === 'run' && x.note) h += '<div class="banner banner-warn" style="margin-top:14px"><b>' + esc(x.note) + '</b></div>';
    if (x.status === 'expired' || x.status === 'revoked') h += '<div class="banner banner-warn" style="margin-top:14px"><b>' + (x.status === 'expired' ? 'Batas waktu habis.' : 'Sesi dicabut.') + '</b> ' + esc(x.note || 'Token tidak berlaku lagi, dan script menghapus unduhan serta container sementara.') + '</div>';
    if (x.status === 'done') {
      var c = findServer(x.name);
      h += '<div class="banner banner-ok" style="margin-top:14px"><b>' + esc(x.name) + ' terdaftar di ' + esc(x.host) + '.</b> Token restore tidak berlaku lagi, dan full pertama langsung dijalankan. ' + (c ? '<a href="#/container/' + encodeURIComponent(x.name) + '">Buka container ›</a>' : '') + '<p>Admin tetap membuka port ' + x.port + ', mengarahkan aplikasi, dan memastikan ' + esc(x.source) + ' tidak lagi menerima tulisan.</p></div>';
    }
    if (x.started) {
      var st = rmSteps(x);
      h += section('Langkah') + '<div class="card card-pad"><ul class="steps-list">' + st.map(function (t, i) {
        var cls = x.status === 'done' || i < x.step ? 'ok' : i > x.step ? 'todo' : x.status === 'run' ? 'run' : x.status === 'wait' ? 'todo' : 'bad';
        return '<li class="' + cls + '">' + esc(t.t) + '</li>';
      }).join('') + '</ul></div>';
    }
    if ((x.status === 'wait' || x.status === 'fail') && canWrite()) h += '<p class="small muted" style="margin-top:12px"><button class="btn btn-sm" data-act="rm-run" data-id="' + x.id + '">Prototype: ' + (x.status === 'fail' ? 'jalankan ulang script' : 'jalankan script') + '</button> Hasilnya dipilih di Pengaturan › Simulasi prototype.</p>';
    return h;
  }
  function rmStop(x) { if (x.timer) { clearInterval(x.timer); x.timer = null; } }
  function rmEnd(x, status, note) {
    rmStop(x); x.status = status; x.token = null; x.endAt = '30 Okt ' + nowLabel(); x.note = note;
  }
  function rmExpire(x) {
    rmEnd(x, 'expired', 'Token tidak berlaku lagi, unduhan ditolak, dan script menghapus unduhan serta container sementara.');
    logRestore(findServer(x.source) || { name: x.source }, 'Restore manual kedaluwarsa', x.name + ' · full ' + x.full);
    toast('Restore manual ' + x.name + ' kedaluwarsa');
  }
  function rmFail(x, note) { rmStop(x); x.status = 'fail'; x.note = note; toast('Restore manual ' + x.name + ' gagal'); }
  function rmFinish(x) {
    var src = findServer(x.source);
    rmStop(x); x.status = 'done'; x.token = null; x.note = ''; x.endAt = '30 Okt ' + nowLabel();
    var c = containerFromSource(src, x.name, x.host, x.ip, { from: src.name, until: x.until, port: x.port, manual: true });
    S.servers.push(c);
    logRestore(src, 'Restore manual selesai', 'Full ' + x.full + ' · data sampai ' + x.until + ' → ' + x.name + ' di ' + x.host);
    toast(x.name + ' terdaftar di ' + x.host);
    firstFullSim(c, x.fullSize);
  }
  function rmStart(x) {
    var steps = rmSteps(x), src = findServer(x.source), same = S.servers.filter(function (o) { return o.host === S.hostSim; })[0], asRunner = S.runners.filter(function (o) { return o.host === S.hostSim; })[0];
    if (!x.host) { x.host = S.hostSim; x.ip = same ? same.ip : asRunner ? asRunner.ip : '10.10.4.21'; x.sameAsSource = !!src && src.host === x.host; }
    x.status = 'run'; x.started = true; x.token = null; x.note = ''; x.step = x.resume || 0; x.resume = 0;
    x.timer = setInterval(function () {
      if (rmFind(x.id) !== x || x.status !== 'run') { rmStop(x); return; }
      var k = steps[x.step].k, sim = S.rmSim;
      if (k === 'dl' && nasDown()) { x.note = 'Unduhan menunggu NAS pulih.'; rerender(); return; } // 503, script mengulang
      x.note = '';
      if (k === 'pre' && sim === 'disk') { rmFail(x, 'Ruang disk tidak cukup untuk data dan unduhan.'); rerender(); return; }
      if (k === 'check' && sim === 'check') { rmFail(x, 'Pemeriksaan gagal: tabel utama tidak terbaca. Hasil restore tidak didaftarkan.'); rerender(); return; }
      if (k === 'unpack' && sim === 'expire') { x.expires = S.clock.getTime(); rmExpire(x); rerender(); return; }
      // Database sudah hidup, jadi percobaan berikutnya mengulang pendaftaran saja.
      if (k === 'reg' && S.confRO) { x.resume = x.step; rmFail(x, 'Tidak bisa menulis config/agents.conf. Database sudah hidup, tetapi belum terdaftar; agen belum menyala.'); rerender(); return; }
      x.step++;
      if (x.step >= steps.length) rmFinish(x);
      rerender();
    }, 1100);
    render();
  }

  // ---------- Riwayat file backup (data contoh) ----------
  // Jam full backup tiap server, dan jadwal dump: [jam di nama file, jam diterima, menit diterima].
  var RIW_HR = { 'simrs-utama': 2, 'lab-db': 2, 'siak-db': 1, 'farmasi-db': 0, 'kepegawaian-db': 23, 'keuangan-db': 3 };
  var RIW_DUMP = { 'simrs-utama': ['0230', 3, 1], 'lab-db': ['0300', 3, 8], 'siak-db': ['0100', 1, 15], 'farmasi-db': ['0000', 0, 36], 'kepegawaian-db': ['2330', 23, 35], 'keuangan-db': ['0400', 4, 7] };
  var NOW_KEY = 300815; // jam prototype: 30 Okt 08:15, ditulis sebagai hari·jam·menit
  var RIW = (function () {
    var rows = [], sz = { 'simrs-utama': ['52,7 GB', '9,9 GB', '104 MB'], 'lab-db': ['18,4 GB', '412 MB', '96 MB'], 'siak-db': ['14,2 GB', '3,1 GB', '22 MB'], 'farmasi-db': ['7,9 GB', '1,2 GB', '41 MB'], 'kepegawaian-db': ['2,3 GB', '640 MB', '8 MB'], 'keuangan-db': ['6,8 GB', '1,1 GB', '14 MB'] };
    var bin = { 'simrs-utama': 2104, 'lab-db': 812, 'siak-db': 331, 'farmasi-db': 533, 'kepegawaian-db': 37, 'keuangan-db': 410 };
    // Hari tanpa file: full = hanya full yang hilang, all = agen tidak mengirim apa pun.
    var holes = { 'lab-db': { 12: 'full', 29: 'full', 30: 'full' }, 'farmasi-db': { 8: 'all' } };
    function p2(n) { return String(n).padStart(2, '0'); }
    function add(srv, kind, file, size, day, hh, mm) {
      var key = day * 10000 + hh * 100 + mm;
      if (key <= NOW_KEY) rows.push([key, srv, kind, file, size, day + ' Okt ' + p2(hh) + ':' + p2(mm)]);
    }
    D.servers.forEach(function (s) {
      var first = regDay(s), last = offDay(s) || 30, h = RIW_HR[s.name], z = sz[s.name], dm = RIW_DUMP[s.name];
      for (var day = Math.max(first, 1); day <= last; day++) {
        var hole = (holes[s.name] || {})[day];
        if (hole === 'all') continue;
        if (!hole) add(s.name, 'full', '2026-10-' + p2(day) + '_' + p2(h) + '00.xbstream.gz', z[0], day, h, 31);
        s.dbs.forEach(function (db) {
          add(s.name, 'dump', db + '/' + db + '-2026-10-' + p2(day) + '_' + dm[0] + '.sql.gz', z[1], day, dm[1], dm[2]);
        });
        // Dump _users siak-db 30 Okt gagal terkirim (file kosong), jadi tidak ada di tabel backups.
        if (!(s.name === 'siak-db' && day === 30)) add(s.name, 'dump', '_users/_users-2026-10-' + p2(day) + '_' + dm[0] + '.sql.gz', '48 KB', day, dm[1], dm[2]);
        for (var b = 0; b < 3; b++) {
          var n = bin[s.name] - (last - day) * 3 - b, hh = 8 - b * 3;
          if (s.binlogGap && String(n).padStart(6, '0') === s.binlogGap.no) continue; // file yang hilang tidak pernah masuk tabel backups
          if (n > 0) add(s.name, 'binlog', 'mysql-bin.' + String(n).padStart(6, '0') + '.gz', z[2], day, hh, 5);
        }
      }
    });
    // Nama sama dengan isi berbeda tersimpan sebagai versi baru (lihat D.integrity).
    add('siak-db', 'dump', 'siakdb/siakdb-2026-10-29_0100.sql.gz.v2-41bd07c3', '3,14 GB', 29, 1, 14);
    add('simrs-utama', 'binlog', 'mysql-bin.002011.gz.v2-77e1b2d4', '98 MB', 21, 22, 42);
    // Hasil permintaan backup (lihat D.backupRequests).
    add('simrs-utama', 'dump', 'simrs/simrs-2026-10-29_1610.sql.gz', '9,9 GB', 29, 16, 42);
    rows[rows.length - 1][6] = 'manual';
    return rows.sort(function (a, b) { return b[0] - a[0]; });
  })();
  // Riwayat file per server (tabel backups), tampil di tab Backup & log.
  // Server baru belum punya baris contoh di RIW, jadi pakai s.recent.
  function riwRows(s) {
    var rows = RIW.filter(function (r) { return r[1] === s.name; });
    var manual = S.manualFiles.filter(function (r) { return r[1] === s.name; });
    if (rows.length) return manual.concat(rows);
    // s.recent server baru sudah memuat file manual, jadi yang manual tidak ditambahkan dua kali.
    return s.recent.map(function (r) {
      var m = manual.filter(function (x) { return x[3] === r[1]; })[0];
      return m || [0, s.name, r[0], r[1], r[2], r[3]];
    });
  }
  function riwTable(s) {
    var f = S.riw;
    var all = riwRows(s), list = all.filter(function (r) {
      return (!f.kind || r[2] === f.kind) && (!f.q || r[3].toLowerCase().indexOf(f.q.toLowerCase()) >= 0);
    });
    var per = 15, pages = Math.max(1, Math.ceil(list.length / per));
    if (f.page >= pages) f.page = pages - 1;
    var slice = list.slice(f.page * per, f.page * per + per);
    var rows = slice.map(function (r) {
      return '<tr><td>' + r[2] + (r[6] === 'manual' ? ' ' + pill('wait', 'Manual') : '') + '</td><td class="mono">' + esc(r[3]) + '</td><td class="num">' + r[4] + '</td><td>' + r[5] + '</td></tr>';
    });
    if (!rows.length) rows = ['<tr><td colspan="4"><div class="empty"><b>Tidak ada file yang cocok</b>Ubah filter atau kata kunci.</div></td></tr>'];
    var foot = '<span class="grow">' + (list.length ? 'Menampilkan ' + (f.page * per + 1) + '–' + (f.page * per + slice.length) + ' dari ' + list.length + ' file (30 hari terakhir)' : '0 file') + '</span>' +
      '<button class="btn btn-sm" data-act="riw-page" data-d="-1"' + (f.page ? '' : ' disabled') + '>‹ Sebelumnya</button><button class="btn btn-sm" data-act="riw-page" data-d="1"' + (f.page < pages - 1 ? '' : ' disabled') + '>Berikutnya ›</button>';
    return tbl(['Jenis', 'File', ['Ukuran', 'num'], 'Diterima'], rows, foot);
  }
  function riwSection(s) {
    if (S.riw.server !== s.name) S.riw = { server: s.name, kind: '', q: '', page: 0 };
    var h = section('Riwayat file diterima', '<span class="muted small">satu baris per file (tabel <code>backups</code>) · isi file hanya ada di NAS</span>');
    if (!riwRows(s).length) return h + '<div class="card"><div class="empty"><b>Belum ada file</b>File muncul di sini setelah agen mengirim backup pertama.</div></div>';
    var f = S.riw;
    h += '<div class="filters"><select data-in="riw-kind" aria-label="Jenis"><option value="">Semua jenis</option>' + ['full', 'dump', 'binlog'].map(function (k) {
      return '<option' + (f.kind === k ? ' selected' : '') + '>' + k + '</option>';
    }).join('') + '</select><input type="search" data-in="riw-q" data-srv="' + esc(s.name) + '" value="' + esc(f.q) + '" placeholder="Cari nama file…" aria-label="Cari nama file"></div>';
    return h + '<div id="riw-table">' + riwTable(s) + '</div>';
  }

  // ---------- Linimasa backup (bagian dari Dashboard) ----------
  // Hari di bulan Okt 2026 saat server terdaftar; yang lebih lama dianggap sudah ada sejak awal rentang.
  function regDay(s) {
    if (s.reg === 'baru saja') return 30;
    var m = /^(\d+) Okt 2026$/.exec(s.reg);
    return m ? +m[1] : 0;
  }
  function offDay(s) {
    var m = /^(\d+) Okt 2026$/.exec(s.offSince || '');
    return m && +m[1] <= 30 ? +m[1] : 0;
  }
  function tlRows(s, day) {
    return riwRows(s).filter(function (r) { return (r[0] ? Math.floor(r[0] / 10000) : 30) === day; });
  }
  function tlCell(s, day) {
    var label = day + ' Okt', reg = regDay(s), off = offDay(s), h = RIW_HR[s.name], dm = RIW_DUMP[s.name];
    if (day < reg) return { c: 'pre', t: '', d: label + ': belum terdaftar' };
    if (off && day > off) return { c: 'off', t: '', d: label + ': nonaktif' };
    var rows = tlRows(s, day);
    var F = rows.some(function (r) { return r[2] === 'full'; });
    // Dump dianggap lengkap hanya bila tiap database punya dump hari itu; _users tidak menutupi database yang gagal.
    var Dm = s.dbs.length ? s.dbs.every(function (db) { return rows.some(function (r) { return r[2] === 'dump' && r[3].indexOf(db + '/') === 0; }); }) : rows.some(function (r) { return r[2] === 'dump'; });
    var key = function (m) { return day * 10000 + m; };
    // Backup yang jadwalnya belum lewat bukan lubang.
    var fullDue = h != null && key(h * 100 + 31) <= NOW_KEY;
    var dumpDue = !!dm && key(dm[1] * 100 + dm[2]) <= NOW_KEY;
    if (S.sim === 'sehat' && s.status === 'active' && (fullDue || dumpDue)) return { c: 'full', t: 'F D', d: label + ': full dan dump masuk' };
    var t = [F ? 'F' : '', Dm ? 'D' : ''].filter(Boolean).join(' ');
    if (!F && !Dm && !fullDue && !dumpDue) return { c: 'pre', t: '', d: label + ': belum waktunya backup' };
    if (!F && !Dm) return { c: 'none', t: '✕', d: label + ': tidak ada backup' };
    var miss = (fullDue && !F ? 1 : 0) + (dumpDue && !Dm ? 1 : 0);
    if (miss) return { c: 'part', t: t, d: label + ': ' + (F ? 'dump tidak lengkap' : 'full tidak masuk') };
    return { c: 'full', t: t, d: label + ': ' + (F && Dm ? 'full dan dump masuk' : F ? 'full masuk' : 'dump masuk, full menunggu jadwal') };
  }
  function linimasaSection() {
    var n = S.tlRange, days = [];
    for (var d = 30 - n + 1; d <= 30; d++) days.push(d);
    var h = section('Linimasa backup', '<span class="muted small">lubang backup per container dan hari · ' + days[0] + '–' + days[n - 1] + ' Okt 2026</span><select data-in="tl-range" aria-label="Rentang waktu" style="margin-left:auto">' + [7, 14, 30].map(function (v) {
      return '<option value="' + v + '"' + (n === v ? ' selected' : '') + '>' + v + ' hari terakhir</option>';
    }).join('') + '</select>');
    var rows = S.servers.map(function (s) {
      var agen = alertsFor(s.name).some(function (a) { return a.kind === 'Agen'; });
      return '<tr' + (s.status === 'off' ? ' class="row-off"' : '') + '><td class="tl-name"><a href="#/container/' + encodeURIComponent(s.name) + '/backup"><b>' + esc(s.name) + '</b></a></td>' + days.map(function (day) {
        var c = tlCell(s, day);
        if (c.c === 'pre' || c.c === 'off') return '<td class="tl-td"><span class="tl-cell tl-' + c.c + '" title="' + esc(s.name) + ' · ' + c.d + '"></span></td>';
        return '<td class="tl-td"><button class="tl-cell tl-' + c.c + '" data-act="tl-cell" data-srv="' + esc(s.name) + '" data-day="' + day + '" title="' + esc(s.name) + ' · ' + c.d + '" aria-label="' + esc(s.name) + ' · ' + c.d + '">' + c.t + '</button></td>';
      }).join('') + '<td class="tl-hb' + (agen ? ' bad' : '') + '">' + s.hb + '</td></tr>';
    });
    h += '<div class="tl">' + tbl(['Container'].concat(days.map(function (d) { return ['<span class="tl-day">' + (n <= 7 ? d + ' Okt' : d) + '</span>', 'tl-th']; })).concat(['Heartbeat']), rows) + '</div>';
    h += '<div class="legend-row"><span><i class="tl-full"></i>F = full, D = dump masuk</span><span><i class="tl-part"></i>hanya sebagian masuk</span><span><i class="tl-none"></i>tidak ada backup</span><span><i class="tl-pre"></i>belum terdaftar atau belum waktunya</span><span><i class="tl-off"></i>nonaktif</span><span>Klik sel untuk daftar file hari itu</span></div>';
    return h;
  }

  // ---------- Halaman: Kesehatan DB ----------
  var MCOLS = ['Koneksi puncak', 'Buffer pool', 'Slow query/hari', 'Deadlock', 'Tumbuh/minggu', 'Disk data', 'Disk /backup', 'Ukuran backup'];
  // Tujuan klik tiap kolom matriks: segmen terakhir menggulir ke bagian itu di halaman Kesehatan; ukuran backup ada di tab Backup & log.
  var MLINK = ['kesehatan/beban', 'kesehatan/performa', 'kesehatan/performa', 'kesehatan/performa', 'kesehatan/ukuran', 'kesehatan/disk', 'kesehatan/disk', 'backup'];
  function rangeSelect() {
    return '<select data-in="h-range" aria-label="Rentang waktu">' + [7, 14, 30, 90].map(function (n) {
      return '<option value="' + n + '"' + (S.hRange === n ? ' selected' : '') + '>' + n + ' hari</option>';
    }).join('') + '</select>';
  }
  function healthMatrix() {
    var h = section('Kesehatan semua container · hari ini', '<span class="muted small">dari user MariaDB read-only <code>lumbung_monitor</code> · beban tiap jam, ukuran tabel tiap hari</span>');
    var names = Object.keys(D.matrix).filter(function (n) { var s = findServer(n); return s && s.status === 'active'; });
    var rows = names.map(function (n) {
      return '<tr><td><a href="#/container/' + n + '/kesehatan"><b>' + n + '</b></a></td>' + D.matrix[n].map(function (c, i) {
        return '<td><a class="hcell h-' + c[1] + '" href="#/container/' + n + '/' + MLINK[i] + '">' + c[0] + '</a></td>';
      }).join('') + '</tr>';
    });
    S.servers.forEach(function (s) {
      if (s.status === 'active' && !D.matrix[s.name]) rows.push('<tr><td><b>' + esc(s.name) + '</b></td><td colspan="8" class="muted">Belum ada metrik. Metrik pertama masuk dalam 1 jam setelah user monitor dibuat; ukuran tabel besok pagi.</td></tr>');
    });
    h += tbl(['Container'].concat(MCOLS), rows);
    h += '<div class="legend-row"><span><i style="background:var(--bad-bg);border:1px solid var(--bad-line)"></i>masalah</span><span><i style="background:var(--warn-bg);border:1px solid var(--warn-line)"></i>perlu diperhatikan</span><span>abu-abu: tidak ada data baru (bukan berarti sehat)</span><span>Klik sel untuk grafik</span></div>';
    return h;
  }
  function mcard(title, val, lvl, sub, body) {
    return '<div class="mcard"><div class="mhead"><b>' + title + '</b><span><span class="val ' + (lvl || '') + '">' + val + '</span> <span class="muted small">' + (sub || '') + '</span></span></div>' + body + '</div>';
  }
  function forecastFull(pct, rate) {
    if (rate <= 0) return { days: Infinity, label: '> 1 tahun' };
    var d = Math.floor((100 - pct) / rate);
    if (d > 365) return { days: d, label: '> 1 tahun' };
    if (d > 180) return { days: d, label: '> 6 bulan' };
    return { days: d, label: '±' + addDays(d) };
  }
  function diskCard(name, title, d, n) {
    var hist = gen(name + title, n, d[0], d[1], 0.4, 0, 100);
    var fc = Math.min(30, Math.max(7, Math.round(n / 2)));
    var labels = dayLabels(n), vals = hist.slice(), fut = [];
    for (var i = 0; i < n - 1; i++) fut.push(null);
    fut.push(d[0]);
    for (var k = 1; k <= fc; k++) {
      var dd = new Date(2026, 9, 30 + k);
      labels.push(n > 31 ? dd.getDate() + '/' + (dd.getMonth() + 1) : String(dd.getDate()));
      vals.push(null); fut.push(Math.min(100, d[0] + d[1] * k));
    }
    var f = forecastFull(d[0], d[1]);
    var lvl = f.days <= 30 ? 'bad' : '';
    return mcard(title + ' terpakai', d[0] + '%', lvl, 'naik ' + num(d[1], 1) + '%/hari · penuh ' + f.label,
      chart({ title: title, labels: labels, min: Math.max(0, 100 - 20 * Math.ceil((100 - Math.min.apply(null, hist) + 5) / 20)), max: 100, thr: 90, thrLabel: 'batas alert 90%', split: n - 1, fmt: function (v) { return Math.round(v) + '%'; },
        series: [{ values: vals, color: C.main }, { values: fut, color: lvl ? C.bad : C.alt, dash: 1 }] }));
  }
  function healthSec(id, title, body, after) {
    return '<section class="health-sec" id="kes-' + id + '">' + section(title) + '<div class="grid2">' + body + '</div>' + (after || '') + '</section>';
  }
  function healthTab(name) {
    var s = findServer(name), m = D.metrics[name];
    if (s.status !== 'active' || !m) return '<div class="card"><div class="empty"><b>Belum ada metrik untuk ' + esc(name) + '</b>Metrik pertama masuk dalam 1 jam setelah user monitor dibuat; ukuran tabel besok pagi.</div></div>';
    var n = S.hRange, labels = dayLabels(n);
    var h = '';
    if (m.stale) h += '<div class="banner banner-bad"><b>Tidak ada metrik sejak 08:01</b> — agen tidak merespons. Grafik di bawah berhenti di data terakhir.</div>';
    if (m.partial) h += '<div class="banner banner-warn"><b>Query terberat tidak tersedia</b> — <code>performance_schema</code> nonaktif di container ini.<p>Metrik lain tetap masuk. Mengaktifkannya butuh ubahan konfigurasi dan restart MariaDB, jadi jadwalkan di luar jam sibuk.</p></div>';
    h += '<div class="filters">' + rangeSelect() + '</div>';

    var conn = gen(name + 'conn', n, m.conn[0], m.conn[1], m.conn[2], 0, m.maxConn);
    var cp = Math.round(m.conn[0] / m.maxConn * 100);
    var rej = gen(name + 'rej', n, m.rejected[0], m.rejected[1], m.rejected[2], 0).map(Math.round);
    h += healthSec('beban', 'Beban',
      mcard('Koneksi puncak harian', m.conn[0] + ' / ' + m.maxConn, cp >= 90 ? 'bad' : '', cp + '% dari max_connections',
        chart({ title: 'koneksi', labels: labels, thr: m.maxConn * 0.9, thrLabel: '90% max_connections', max: m.maxConn, floor: 0, series: [{ values: conn, color: cp >= 90 ? C.bad : C.main }] })) +
      mcard('Koneksi ditolak per hari', m.rejected[0], m.rejected[0] ? 'bad' : '', m.rejected[0] ? 'hari ini' : 'tidak ada',
        chart({ title: 'ditolak', labels: labels, min: 0, max: Math.max(5, Math.max.apply(null, rej) * 1.2), series: [{ values: rej, bars: 1, color: C.main, colorAt: function (v) { return v > 0 ? C.bad : C.main; } }] })));

    var perf = '';
    var bp = gen(name + 'bp', n, m.bp[0], -m.bp[1], m.bp[2], 90, 100);
    perf += mcard('Buffer pool hit ratio', num(m.bp[0], 1) + '%', m.bp[0] < 98 ? 'warn' : '', m.bp[1] ? 'turun ' + num(m.bp[1], 2) + ' poin/hari' : 'stabil',
      chart({ title: 'buffer pool', labels: labels, min: 95, max: 100, thr: 98, thrLabel: 'batas 98%', fmt: function (v) { return num(v, 1) + '%'; }, series: [{ values: bp, color: m.bp[0] < 98 ? C.warn : C.main }] }));
    var slow = gen(name + 'slow', n, m.slow[0], m.slow[1], m.slow[2], 0).map(Math.round);
    var avg = []; for (var i = 0; i < n; i++) avg.push(m.slowAvg);
    var up = m.slow[0] > m.slowAvg * 1.5;
    perf += mcard('Slow query per hari', num(m.slow[0]), up ? 'bad' : '', 'rata-rata 30 hari ' + num(m.slowAvg),
      chart({ title: 'slow query', labels: labels, floor: 0, series: [{ values: avg, color: C.alt, width: 1.2, dots: false }, { values: slow, color: up ? C.bad : C.main }] }) + '<div class="legend-row"><span><i style="background:' + (up ? C.bad : C.main) + '"></i>per hari</span><span><i style="background:' + C.alt + '"></i>rata-rata 30 hari</span></div>');
    var dl = gen(name + 'dl', n, m.deadlock[0], m.deadlock[1], m.deadlock[2], 0).map(Math.round);
    perf += mcard('Deadlock per hari', m.deadlock[0], m.deadlock[0] ? 'warn' : '', 'selisih harian Innodb_deadlocks',
      chart({ title: 'deadlock', labels: labels, min: 0, max: Math.max(4, Math.max.apply(null, dl) + 1), series: [{ values: dl, bars: 1, color: C.main, colorAt: function (v) { return v > 0 ? C.warn : C.main; } }] }));
    if (name === 'simrs-utama') {
      perf += mcard('Query terberat hari ini', '612×', 'bad', 'rata-rata 3,8 dtk', '<pre style="white-space:pre-wrap;background:var(--surface-2);border:1px solid var(--line);padding:8px;border-radius:5px;margin:6px 0">SELECT * FROM log_aktivitas WHERE user_id = ? AND created_at BETWEEN ? AND ? ORDER BY created_at DESC</pre><p class="small muted">Dari <code>performance_schema</code>; nilai literal sudah diganti <code>?</code>. Saran index ada di <a href="#/laporan">laporan harian</a>.</p>');
    }
    if (m.partial) perf += mcard('Query terberat hari ini', '—', '', 'tidak tersedia', '<div class="unavail">performance_schema nonaktif di container ini</div>');
    h += healthSec('performa', 'Performa', perf);

    var wk = Math.max(4, Math.round(n / 7)), sz = [], wl = [];
    var r = seeded(name + 'sz' + wk);
    for (var w = 0; w < wk; w++) { sz.push(m.dbSize[0] - m.dbSize[1] * 7 * (wk - 1 - w) + (w === wk - 1 ? 0 : (r() - 0.5) * m.dbSize[1] * 2)); wl.push('mg ' + (44 - (wk - 1 - w))); }
    h += healthSec('ukuran', 'Ukuran',
      mcard('Ukuran database', num(m.dbSize[0], 1) + ' GB', '', '+' + num(m.dbSize[1] * 7, 1) + ' GB/minggu',
        chart({ title: 'ukuran', labels: wl, fmt: function (v) { return num(v, v < 20 ? 1 : 0) + ' GB'; }, series: [{ values: sz, color: C.main }] })) +
      '<div>' + tbl(['Tabel terbesar', ['Ukuran', 'num'], ['Tumbuh/minggu', 'num']], m.tables.map(function (t) { return '<tr><td class="mono">' + t[0] + '</td><td class="num">' + t[1] + '</td><td class="num">' + t[2] + '</td></tr>'; })) + '<p class="small muted" style="margin-top:6px">Dari <code>information_schema.TABLES</code>, diambil sekali sehari.</p></div>');

    h += healthSec('disk', 'Disk',
      diskCard(name, 'Disk /backup', m.diskBackup, n) + diskCard(name, 'Disk data', m.diskData, n),
      '<div style="margin-top:12px">' + tbl(['Disk', ['Terpakai', 'num'], ['Laju', 'num'], 'Perkiraan penuh'], [['/backup', m.diskBackup], ['/var/lib/mysql', m.diskData]].map(function (d) {
        var f = forecastFull(d[1][0], d[1][1]);
        return '<tr class="row-' + (f.days <= 30 ? 'bad' : 'ok') + '"><td class="mono">' + d[0] + '</td><td class="num">' + d[1][0] + '%</td><td class="num">+' + num(d[1][1], 1) + '%/hari</td><td>' + (f.days <= 30 ? '<b>' + f.label + ' 2026</b>' : f.label) + '</td></tr>';
      })) + '<p class="small muted" style="margin-top:6px">Perkiraan memakai regresi linear atas riwayat 30 hari, bukan AI.</p></div>');

    return h + '<p class="small muted" style="margin-top:10px">Garis putus-putus merah memakai ambang yang sama dengan alert, jadi grafik dan alert tidak bertentangan.</p>';
  }

  // ---------- Halaman: Laporan ----------
  function aiLabel(t) { return '<div class="ailabel"><b>Dibuat AI</b> · claude-sonnet-5-5 · ' + (t || '07:01') + ' · <a href="#" data-act="ai-input">Lihat input</a></div>'; }
  // Laporan harian berbentuk dokumen: dibaca berurutan dari atas dan bisa dicetak ke A4.
  function reportDaily(mode) {
    var withAI = mode === 'ai', snap = D.dailySnap, sehat = S.sim === 'sehat';
    var F = [
      { lvl: 'bad', t: 'lab-db: disk /backup diperkirakan penuh ±12 Nov (13 hari lagi)', p: 'Naik rata-rata 2,1% per hari selama 14 hari. Full 27 Okt gagal uji restore, dan full 29 Okt tidak masuk karena disk penuh sesaat.', href: '#/container/lab-db/kesehatan/disk', sp: gen('lab-dbDisk /backup', 14, 71, 2.1, 0.4, 0, 100),
        ai: '<ol><li>Tambah ruang /backup atau aktifkan retensi (Laporan › Retensi masih mode uji coba).</li><li>Jalankan ulang full backup lab-db, lalu uji restore.</li></ol>' },
      { lvl: 'bad', t: 'lab-db: rantai binlog putus, 000790 hilang sejak 23 Okt', p: 'Point-in-time dari full terakhir yang lolos uji (22 Okt) berhenti sebelum nomor ini.', href: '#/container/lab-db/backup',
        ai: '<p>Cari file itu di /backup server lab-db. Bila sudah tidak ada, full baru yang lolos uji restore memulai rantai baru.</p>' },
      { lvl: 'bad', t: 'siak-db: dump _users gagal terkirim sejak 01:14 (HTTP 422, file kosong)', p: 'Dump ini tidak ada di NAS; file siak-db lainnya tetap terkirim.', href: '#/container/siak-db/backup',
        ai: '<p>Log backup mencatat dump _users 0 byte. Periksa penyebabnya, lalu jalankan ulang dump di server.</p>' },
      { lvl: 'bad', t: 'simrs-utama: koneksi puncak 468 dari 500 (94%) pukul 10:05', p: '37 koneksi ditolak kemarin. Puncak naik 5 hari berturut-turut.', href: '#/container/simrs-utama/kesehatan/beban', sp: gen('simrs-utamaconn', 14, 468, 9, 18, 0, 500),
        ai: '<p>Jangan naikkan <code>max_connections</code> dulu: koneksi tertahan menunggu slow query #1. Perbaiki query itu, lalu pantau puncak 3 hari.</p>' },
      { lvl: 'warn', t: 'simrs-utama: slow query 1.284, rata-rata 30 hari 702', p: 'Naik 83%.', href: '#/container/simrs-utama/kesehatan/performa', sp: gen('simrs-utamaslow', 14, 1284, 40, 60, 0), open: 1,
        ai: '<p>Query terberat (612×/hari, rata-rata 3,8 dtk) memeriksa rata-rata 41 juta baris per eksekusi:</p><pre>SELECT * FROM log_aktivitas WHERE user_id = ? AND created_at BETWEEN ? AND ? ORDER BY created_at DESC</pre><p>Saran index:</p><pre>CREATE INDEX idx_log_user_waktu ON log_aktivitas (user_id, created_at);</pre><p class="small muted">Hanya saran, tidak dijalankan LumbungDB. Uji dulu di luar jam sibuk. Nilai di WHERE sudah diganti ? sebelum dikirim ke AI.</p>' },
      { lvl: 'warn', t: 'lab-db: full backup 18,4 GB, 31% lebih kecil dari rata-rata 26,6 GB', p: 'Ukuran yang turun jauh biasanya berarti backup terpotong; cocok dengan hasil uji restore yang gagal.', href: '#/container/lab-db/backup', sp: [26.1, 26.4, 26.8, 26.5, 27, 18.4],
        ai: '<p>Ikut selesai bila masalah disk /backup lab-db diatasi; periksa ukuran full backup berikutnya.</p>' }
    ];
    if (sehat) F = [];
    // Isi laporan diambil dari snapshot saat terbit, bukan kondisi saat ini.
    var al = sehat ? [] : snap.alerts.map(function (x) { var c = x[2] || D.alerts.filter(function (b) { return b.key === x[0]; })[0]; return { cond: c.cond, server: c.server, kind: c.kind, lvl: c.lvl, since: x[1] }; });
    var nBad = F.filter(function (f) { return f.lvl === 'bad'; }).length, LVL = { bad: 'Segera', warn: 'Perhatikan' };
    var h = '<article class="report">' + repHead('Laporan harian', 'Jumat, 30 Oktober 2026', 'Periode 29 Okt 07:00 – 30 Okt 07:00 · terbit 30 Okt 07:00 · isi tidak berubah setelah terbit');
    h += '<div class="rep-stats">' + repStat(snap.rows.length, 'container dipantau') + repStat(nBad, 'perlu tindakan segera', 'bad') + repStat(F.length - nBad, 'perlu diperhatikan', 'warn') + repStat(al.length, 'alert aktif') + '</div>';
    h += '<h2>1. Ringkasan</h2>';
    if (sehat) h += '<p>Tidak ada temuan. Semua container mengirim backup dan lolos uji terakhir.</p>';
    else if (withAI) h += '<p>Empat hal butuh tindakan hari ini. <b>Disk /backup lab-db</b> akan penuh sekitar 12 November; full yang gagal uji 27 Oktober dan ukuran backup yang turun 31% kemungkinan besar berasal dari masalah yang sama. <b>Rantai binlog lab-db</b> putus di 000790. <b>Dump _users siak-db</b> gagal terkirim karena file kosong. <b>Beban simrs-utama</b> naik lima hari berturut-turut, dan koneksi penuh serta slow query sama-sama memuncak sekitar pukul 10:00, cocok dengan satu query di tabel <code>log_aktivitas</code>.</p>' +
      '<p class="rep-note"><span class="rep-ai">AI</span> Ditulis AI (claude-sonnet-5-5, 07:01) dari angka di laporan ini. <a href="#" class="no-print" data-act="ai-input">Lihat input</a></p>';
    else h += '<div class="aifail">Ringkasan dan saran AI tidak tersedia hari ini (penyedia tidak merespons pukul 07:01 dan 07:16). Angka dan temuan di bawah tetap lengkap.</div>' +
      '<p>' + F.length + ' temuan: ' + nBad + ' perlu tindakan segera, ' + (F.length - nBad) + ' perlu diperhatikan.</p>';
    h += '<h2>2. Temuan dan tindakan</h2><ol class="rep-finds">' + F.map(function (f) {
      return '<li class="' + f.lvl + '"><div class="rep-find-head"><span class="rep-lvl ' + f.lvl + '">' + LVL[f.lvl] + '</span><b>' + f.t + '</b></div><p>' + f.p + ' <a class="no-print small" href="' + f.href + '">Lihat metrik ›</a></p>' +
        (withAI ? '<div class="rep-saran"><div class="rep-saran-head"><b>Saran</b> <span class="rep-ai">AI</span></div>' + f.ai + '</div>' : '') + '</li>';
    }).join('') + '</ol>';
    if (!F.length) h = h.replace('<h2>2. Temuan dan tindakan</h2><ol class="rep-finds"></ol>', '<h2>2. Temuan dan tindakan</h2><p>Tidak ada temuan.</p>');
    h += '<h2>3. Status container <span class="rep-h-meta">per 30 Okt 07:00</span></h2>' + tbl(['Container', 'Full terakhir', 'Dump terakhir', 'Uji restore', 'Status'], snap.rows.map(function (x) {
      var rok = sehat || x[3].ok;
      return '<tr><td><b>' + esc(x[0]) + '</b></td><td>' + x[1] + '</td><td>' + x[2] + '</td><td>' + (rok ? 'Lolos' : '<b style="color:var(--bad)">Gagal</b>') + ' · ' + x[3].date + '</td><td>' + (sehat ? pill('ok', 'OK') : pill(x[4], x[5])) + '</td></tr>';
    }));
    h += '<h2>4. Alert aktif (' + al.length + ') <span class="rep-h-meta">per 30 Okt 07:00</span></h2>' + (al.length ? tbl(['Kondisi', 'Sumber', 'Jenis', 'Sejak'], al.map(function (a) {
      return '<tr class="row-' + alertLvl(a) + '"><td><b>' + a.cond + '</b></td><td>' + esc(a.server) + '</td><td>' + a.kind + '</td><td>' + a.since + '</td></tr>';
    })) : '<p>Tidak ada alert aktif.</p>');
    h += '<h2>5. Catatan</h2><ul class="rep-notes">' + (sehat ? '' : '<li><b>Data tidak lengkap:</b> kepegawaian-db belum punya daftar query terberat karena <code>performance_schema</code> nonaktif.</li>') +
      '<li><b>Nonaktif, tidak dipantau:</b> ' + snap.off.join(', ') + '.</li><li>Kejadian setelah 07:00 ada di Dashboard dan laporan berikutnya.</li></ul>';
    return h + repFoot() + '</article>';
  }
  function repHead(kind, title, meta) {
    return '<header class="rep-head"><div class="rep-brand"><span class="logo-mark">L</span>LumbungDB · ' + kind + '</div><h1>' + title + '</h1><div class="rep-meta">' + meta + '</div></header>';
  }
  function repStat(n, label, lvl) {
    return '<div class="rep-stat' + (lvl && n ? ' ' + lvl : '') + '"><b>' + n + '</b><span>' + label + '</span></div>';
  }
  function repFoot() {
    return '<footer class="rep-foot">Dibuat otomatis oleh LumbungDB. Angka dan alert dihitung dengan aturan biasa; bagian bertanda AI hanya saran dan tidak dijalankan otomatis.</footer>';
  }
  // ---------- Laporan bulanan sampai tahunan ----------
  // Periode yang sudah selesai. Tiap server hanya dihitung sejak tanggal terdaftar.
  var PERIODS = {
    m: { kind: 'Laporan bulanan', title: 'September 2026', meta: '1–30 September 2026 · terbit 1 Okt 2026 07:00', days: 30, end: new Date(2026, 8, 30), arsip: ['September 2026', 'Agustus 2026', 'Juli 2026'] },
    q: { kind: 'Laporan 3 bulan', title: 'Juli – September 2026', meta: 'Triwulan III · 1 Jul – 30 Sep 2026 · terbit 1 Okt 2026 07:00', days: 92, end: new Date(2026, 8, 30), arsip: ['Jul–Sep 2026', 'Apr–Jun 2026'] },
    s: { kind: 'Laporan 6 bulan', title: 'Januari – Juni 2026', meta: 'Semester I · 1 Jan – 30 Jun 2026 · terbit 1 Jul 2026 07:00', days: 181, end: new Date(2026, 5, 30), arsip: ['Jan–Jun 2026'] }
  };
  // [server, database, ukuran sekarang GB, tumbuh GB/hari, peluang full gagal per hari, alert per hari, disk data sekarang %, naik %/hari, tanggal terdaftar]
  var REP_SRV = [
    ['simrs-utama', 'simrs', 412, 0.87, 0.003, 0.08, 62, 0.25, new Date(2026, 2, 12)],
    ['lab-db', 'lab_hasil', 38, 0.17, 0.02, 0.12, 46, 0.2, new Date(2026, 8, 3)],
    ['siak-db', 'siakdb', 21, 0.06, 0.002, 0.03, 39, 0.1, new Date(2026, 2, 12)],
    ['farmasi-db', 'farmasi, farmasi_stok', 17, 0.05, 0.008, 0.1, 52, 0.1, new Date(2026, 8, 3)],
    ['keuangan-db', 'keuangan', 9, 0.02, 0.004, 0.02, 33, 0.01, new Date(2026, 5, 12)]
  ];
  var ALERT_MIX = [['Backup', 0.3, '9 jam'], ['Agen', 0.25, '41 mnt'], ['Kesehatan', 0.35, '1,8 hari'], ['Uji restore', 0.05, '2 hari'], ['Integritas', 0.05, '6 jam']];
  var BULAN = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
  function pct(a, b) { return num(a / b * 100, 1) + '%'; }
  function reportPeriod(k) {
    var p = PERIODS[k], ago = Math.round((new Date(2026, 9, 30) - p.end) / 864e5), start = new Date(p.end.getTime() - (p.days - 1) * 864e5);
    var rows = REP_SRV.map(function (r) {
      var from = r[8] > start ? r[8] : start, D = Math.round((p.end - from) / 864e5) + 1;
      if (D <= 0) return null;
      var fullFail = Math.round(D * r[4]), dumpFail = Math.round(D * r[4] / 2), tests = Math.max(1, Math.round(D / REP_SRV.length));
      var endGb = r[2] - r[3] * ago, startGb = endGb - r[3] * D, disk = Math.max(12, r[6] - r[7] * ago), left = (100 - disk) / r[7];
      var full = new Date(p.end.getTime() + left * 864e5);
      return { name: r[0], db: r[1], days: D, partial: D < p.days, fullOk: D - fullFail, fullFail: fullFail, dumpOk: D - dumpFail, tests: tests, testFail: Math.round(tests * r[4] * 2), alerts: Math.round(D * r[5]),
        start: startGb, end: endGb, disk: disk, full: left > 730 ? '&gt; 2 tahun' : '±' + BULAN[full.getMonth()] + ' ' + full.getFullYear(), soon: left < 180 };
    }).filter(Boolean);
    var sum = function (f) { return rows.reduce(function (a, r) { return a + r[f]; }, 0); };
    var tot = sum('days'), fullOk = sum('fullOk'), tests = sum('tests'), testOk = tests - sum('testFail'), alerts = sum('alerts');
    var grow = rows.reduce(function (a, r) { return a + r.end - r.start; }, 0);
    var worst = rows.slice().sort(function (a, b) { return b.fullFail - a.fullFail; })[0], simrs = rows[0];
    var lvlOf = function (ok, n) { return ok / n < 0.95 ? 'bad' : ok / n < 0.99 ? 'warn' : ''; };

    var h = '<article class="report">' + repHead(p.kind, p.title, p.meta);
    h += '<div class="rep-stats">' + repStat(rows.length, 'container dipantau') + repStat(pct(fullOk, tot), 'full backup berhasil', lvlOf(fullOk, tot)) +
      repStat(testOk + ' / ' + tests, 'uji restore lolos', testOk < tests ? 'warn' : '') + repStat('+' + num(grow, 0) + ' GB', 'pertumbuhan data') + '</div>';

    h += '<h2>1. Ringkasan</h2><p>Selama periode ini <b>' + pct(fullOk, tot) + ' full backup berhasil</b> (' + num(fullOk) + ' dari ' + num(tot) + ').' + (worst.fullFail ? ' Kegagalan terbanyak di <b>' + worst.name + '</b> (' + worst.fullFail + ' kali) karena ruang /backup penuh sesaat.' : '') + ' ' +
      'Uji restore lolos ' + testOk + ' dari ' + tests + ' kali' + (testOk < tests ? ' (' + (tests - testOk) + ' gagal, rinciannya di bagian 2)' : ', jadi backup yang diuji terbukti bisa dipulihkan') + '. Data bertambah ' + num(grow, 0) + ' GB; ' + num((simrs.end - simrs.start) / grow * 100, 0) + '% berasal dari <b>simrs</b>, dan disk data simrs-utama diperkirakan penuh ' + simrs.full + '.</p>';
    h += '<p><b>Rekomendasi:</b></p><ol>' + (worst.fullFail ? '<li>Tambah ruang /backup ' + worst.name + ' atau perketat retensinya.</li>' : '') + '<li>Anggarkan penambahan disk simrs-utama sebelum ' + simrs.full.replace('±', '') + '.</li><li>Sepakati masa simpan <code>log_aktivitas</code> dengan unit terkait; tabel ini penyumbang pertumbuhan terbesar.</li></ol>' +
      '<p class="rep-note"><span class="rep-ai">AI</span> Ditulis AI (claude-sonnet-5-5) dari angka di laporan ini. <a href="#" class="no-print" data-act="ai-input">Lihat input</a></p>';

    h += '<h2>2. Keberhasilan backup</h2>' + tbl(['Container', ['Full berhasil', 'num'], ['Dump berhasil', 'num'], ['Uji restore lolos', 'num'], ['Alert', 'num']], rows.map(function (r) {
      var lv = lvlOf(r.fullOk, r.days);
      return '<tr' + (lv ? ' class="row-' + lv + '"' : '') + '><td><b>' + r.name + '</b></td><td class="num">' + pct(r.fullOk, r.days) + ' <span class="muted small">(' + r.fullOk + '/' + r.days + ')</span></td><td class="num">' + pct(r.dumpOk, r.days) +
        '</td><td class="num">' + (r.tests - r.testFail) + ' / ' + r.tests + '</td><td class="num">' + r.alerts + '</td></tr>';
    }).concat('<tr><td><b>Total</b></td><td class="num"><b>' + pct(fullOk, tot) + '</b></td><td class="num"><b>' + pct(sum('dumpOk'), tot) + '</b></td><td class="num"><b>' + testOk + ' / ' + tests + '</b></td><td class="num"><b>' + alerts + '</b></td></tr>'));
    h += '<p class="rep-note">Dihitung per hari: satu full dan satu dump tiap database per container. Uji restore mengikuti jadwal tiap container.</p>';

    h += '<h2>3. Pertumbuhan data</h2>' + tbl(['Database', 'Container', ['Awal', 'num'], ['Akhir', 'num'], ['Tumbuh', 'num'], 'Tren'], rows.map(function (r) {
      return '<tr><td>' + r.db + '</td><td>' + r.name + '</td><td class="num">' + num(r.start, 1) + ' GB</td><td class="num">' + num(r.end, 1) + ' GB</td><td class="num">+' + num(r.end - r.start, 1) + ' GB</td><td>' +
        spark(gen(k + r.name, 12, r.end, (r.end - r.start) / 11, (r.end - r.start) / 40)) + '</td></tr>';
    }));

    h += '<h2>4. Kapasitas disk data <span class="rep-h-meta">per akhir periode · regresi linear</span></h2>' + tbl(['Container', ['Terpakai', 'num'], 'Perkiraan penuh'], rows.map(function (r) {
      return '<tr' + (r.soon ? ' class="row-bad"' : '') + '><td>' + r.name + '</td><td class="num">' + num(r.disk, 0) + '%</td><td>' + (r.soon ? '<b>' + r.full + '</b>' : r.full) + '</td></tr>';
    }));

    // Sisa pembulatan masuk ke baris terakhir supaya rincian sama dengan total di judul.
    var given = 0, mix = ALERT_MIX.map(function (a, i) { var c = i === ALERT_MIX.length - 1 ? Math.max(0, alerts - given) : Math.min(alerts - given, Math.round(alerts * a[1])); given += c; return [a[0], c, a[2]]; });
    h += '<h2>5. Kejadian alert (' + alerts + ')</h2>' + tbl(['Jenis', ['Jumlah', 'num'], ['Rata-rata sampai pulih', 'num']], mix.map(function (a) {
      return '<tr><td>' + a[0] + '</td><td class="num">' + a[1] + '</td><td class="num">' + a[2] + '</td></tr>';
    }));

    var part = rows.filter(function (r) { return r.partial; }).map(function (r) { return r.name + ' (' + r.days + ' hari)'; });
    h += '<h2>6. Catatan</h2><ul class="rep-notes">' + (part.length ? '<li>Didaftarkan di tengah periode, dihitung sejak tanggal daftar: ' + part.join(', ') + '.</li>' : '') +
      '<li>Hari tanpa data dari agen dihitung sebagai backup gagal.</li></ul>';
    return h + repFoot() + '</article>';
  }
  function reportWeekly() {
    var h = '<article class="report">' + repHead('Laporan mingguan', '19–25 Oktober 2026', 'Senin–Minggu · terbit Senin 26 Okt 07:00');
    h += '<h2>1. Ringkasan tren</h2><p>Pertumbuhan minggu ini didominasi <code>simrs.log_aktivitas</code> (+2,8 GB, hampir separuh pertumbuhan simrs). Dengan laju ini disk data simrs-utama penuh sekitar Maret 2027, tetapi disk /backup lab-db jauh lebih mendesak.</p>';
    h += '<p><b>Rekomendasi minggu ini:</b></p><ol><li>Atasi disk /backup lab-db sebelum 12 Nov.</li><li>Sepakati masa simpan <code>log_aktivitas</code> dengan unit terkait; tabel ini penyumbang pertumbuhan terbesar.</li><li>Rencanakan penambahan disk simrs-utama paling lambat Januari 2027.</li></ol>' +
      '<p class="rep-note"><span class="rep-ai">AI</span> Ditulis AI (claude-sonnet-5-5, Senin 07:02) dari angka di laporan ini. <a href="#" class="no-print" data-act="ai-input">Lihat input</a></p>';
    h += '<h2>2. Ukuran database</h2>' + tbl(['Database', 'Container', ['Ukuran', 'num'], ['Tumbuh seminggu', 'num'], 'Tren 8 minggu'], [['simrs', 'simrs-utama', '412 GB', '+6,1 GB', 412, 0.87], ['lab_hasil', 'lab-db', '38 GB', '+1,2 GB', 38, 0.17], ['siakdb', 'siak-db', '21 GB', '+0,4 GB', 21, 0.06], ['farmasi', 'farmasi-db', '17 GB', '+0,4 GB', 17, 0.05]].map(function (r) {
      var vals = []; for (var i = 0; i < 8; i++) vals.push(r[4] - r[5] * 7 * (7 - i));
      return '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td><td class="num">' + r[2] + '</td><td class="num">' + r[3] + '</td><td>' + spark(vals) + '</td></tr>';
    }));
    h += '<h2>3. Tabel terbesar</h2>' + tbl(['Tabel', ['Ukuran', 'num'], ['Tumbuh seminggu', 'num']], [['simrs.log_aktivitas', '96 GB', '+2,8 GB'], ['simrs.rekam_medis_detail', '71 GB', '+1,1 GB'], ['lab_hasil.hasil_item', '22 GB', '+0,7 GB']].map(function (r) {
      return '<tr><td class="mono">' + r[0] + '</td><td class="num">' + r[1] + '</td><td class="num">' + r[2] + '</td></tr>';
    }));
    h += '<h2>4. Perkiraan disk penuh <span class="rep-h-meta">regresi linear 30 hari</span></h2>' + tbl(['Container', 'Disk', ['Sisa', 'num'], 'Perkiraan penuh'], [
      '<tr class="row-bad"><td>lab-db</td><td>/backup</td><td class="num">29%</td><td><b>±12 Nov 2026</b></td></tr>',
      '<tr><td>simrs-utama</td><td>data</td><td class="num">38%</td><td>±Mar 2027</td></tr>',
      '<tr><td>siak-db</td><td>data</td><td class="num">61%</td><td>&gt; 1 tahun</td></tr>']);
    return h + repFoot() + '</article>';
  }
  function pageLaporan() {
    crumbs(['Laporan']);
    var head = 'Rekap backup, kesehatan, dan kapasitas container, serta daftar alert, uji restore, retensi, dan antrean notifikasi.';
    if (!S.servers.length) return pageHead('Laporan', head) + '<div class="card"><div class="empty"><b>Belum ada data</b>Laporan terbit setelah container pertama mengirim backup.</div></div>';
    var pn = pendingNotif();
    var tabs = { lap: 'Laporan', alert: 'Alert', rst: 'Uji restore', kes: 'Kesehatan container', ret: 'Retensi', notif: 'Notifikasi' + (pn ? ' (' + pn + ')' : '') };
    // Semua jenis laporan ada di satu tab; dipilih lewat dropdown, bukan tab sendiri-sendiri.
    var JENIS = { h: 'Harian', w: 'Mingguan', m: 'Bulanan', q: '3 bulan', s: '6 bulan', y: 'Tahunan' };
    S.lapTab = route()[1] || 'h';
    var isLap = !!JENIS[S.lapTab] || S.lapTab === 'na', cur = isLap ? 'lap' : S.lapTab;
    if (!tabs[cur]) { S.lapTab = 'h'; isLap = true; cur = 'lap'; }
    // 'na' = contoh laporan harian saat AI gagal, dipilih dari arsip harian.
    var arsip = PERIODS[S.lapTab] ? PERIODS[S.lapTab].arsip : S.lapTab === 'y' ? ['Belum ada'] : S.lapTab === 'w' ? ['19–25 Okt 2026', '12–18 Okt 2026', '5–11 Okt 2026'] : ['Jumat 30 Okt 2026', 'Kamis 29 Okt 2026', 'Rabu 28 Okt 2026'];
    var h = pageHead('Laporan', head, isLap && S.lapTab !== 'y' ? '<button class="btn btn-pri" data-act="print">Cetak / simpan PDF</button>' : '');
    h += '<div class="filters"><div class="seg">' + Object.keys(tabs).map(function (k) {
      return '<button data-act="lap-tab" data-v="' + (k === 'lap' ? 'h' : k) + '" class="' + (cur === k ? 'on' : '') + '">' + tabs[k] + '</button>';
    }).join('') + '</div>';
    if (isLap) h += '<select data-in="lap-jenis" aria-label="Jenis laporan">' + Object.keys(JENIS).map(function (k) {
        return '<option value="' + k + '"' + (S.lapTab === k || S.lapTab === 'na' && k === 'h' ? ' selected' : '') + '>' + JENIS[k] + '</option>';
      }).join('') + '</select><select data-in="lap-arsip" aria-label="Periode laporan">' + arsip.map(function (a, i) { return '<option value="' + i + '">' + a + '</option>'; }).join('') +
      (S.lapTab === 'h' || S.lapTab === 'na' ? '<option value="na"' + (S.lapTab === 'na' ? ' selected' : '') + '>Contoh: saat AI gagal</option>' : '') + '</select>';
    h += '</div>';
    if (S.lapTab === 'h') h += reportDaily(S.aiDown ? 'fail' : 'ai');
    if (S.lapTab === 'na') h += reportDaily('fail');
    if (S.lapTab === 'w') h += reportWeekly();
    if (S.lapTab === 'y') h += '<div class="card"><div class="empty"><b>Belum ada laporan tahunan</b>LumbungDB dipakai sejak Maret 2026; laporan tahun 2026 terbit 1 Januari 2027.</div></div>';
    if (PERIODS[S.lapTab]) h += reportPeriod(S.lapTab);
    if (S.lapTab === 'alert') h += alertTab();
    if (S.lapTab === 'rst') h += restoreAllTab();
    if (S.lapTab === 'kes') h += healthMatrix();
    if (S.lapTab === 'ret') h += retentionTab();
    if (S.lapTab === 'notif') h += notifTab();
    return h;
  }

  // ---------- Halaman: Pengaturan ----------
  function pagePengaturan() {
    crumbs(['Pengaturan']);
    var simSel = function (key, label, cur, opts) {
      return '<label class="sim"><span>' + label + '</span><select data-in="' + key + '" aria-label="' + label + '">' + Object.keys(opts).map(function (k) {
        return '<option value="' + k + '"' + (String(cur) === k ? ' selected' : '') + '>' + opts[k] + '</option>';
      }).join('') + '</select></label>';
    };
    var h = pageHead('Pengaturan', 'Hanya-baca. Ubah lewat <code>.env</code> di VM LumbungDB lalu restart container LumbungDB.') + configTab();
    var rs = S.rescan, busy = rs && rs.running;
    h += section('Pemeliharaan') + '<div class="card card-pad"><b>Pindai ulang NAS</b><p class="small muted" style="margin:4px 0 8px">Membangun kembali metadata di tabel <code>backups</code> dari file di NAS, misalnya setelah SQLite hilang. File tidak diubah.</p>' +
      '<button class="btn" data-act="rescan-open"' + (busy || !canWrite() || nasDown() ? ' disabled' : '') + '>' + (busy ? '<span class="spin"></span> Memindai…' : 'Pindai ulang') + '</button>' +
      (rs && !busy ? ' <span class="small muted">Terakhir ' + rs.at + ' · ' + rs.result + '</span>' : '') + '</div>';
    h += section('Simulasi prototype', '<span class="muted small">hanya untuk melihat tampilan; tidak ada di aplikasi asli</span>') + '<div class="sim-grid">' +
      simSel('sim-state', 'Data', S.sim, { normal: 'Ada masalah (contoh utama)', sehat: 'Semua sehat', kosong: 'Belum ada container' }) +
      simSel('sim-nas', 'NAS', S.nas.state, { ok: 'Normal', down: 'Tidak ter-mount', hang: 'Tidak merespons (macet)', low: 'Ruang di bawah 10%' }) +
      simSel('sim-ai', 'Penyedia AI', S.aiDown ? 'down' : 'ok', { ok: 'Normal', down: 'Gagal' }) +
      simSel('sim-runner', 'Runner', S.runnerSim, { normal: 'runner-b gagal mengunduh file', ok: 'Semua sehat', dead: 'runner-a tidak merespons', disk: 'Scratch runner-a hampir penuh', none: 'Belum ada runner' }) +
      simSel('sim-conf', 'agents.conf dan runners.conf', S.confRO ? 'ro' : 'rw', { rw: 'Bisa ditulis', ro: 'Gagal ditulis' }) +
      simSel('sim-login', 'Login dashboard', S.noLogin ? 'off' : 'on', { on: 'Terisi', off: 'Kosong' }) +
      simSel('sim-req', 'Hasil Backup sekarang', S.reqSim, { ok: 'Selesai', fail: 'Gagal', restart: 'Container restart (tidak selesai)', busy: 'Sudah berjalan dari cron', disk: 'Ditolak: disk kurang' }) +
      simSel('sim-restore', 'Hasil Restore sekarang', S.restoreSim, { ok: 'Lolos', fail: 'Gagal' }) +
      simSel('sim-rm', 'Hasil Restore manual', S.rmSim, { ok: 'Lolos dan terdaftar', disk: 'Ruang disk kurang', check: 'Pemeriksaan gagal', expire: 'Batas waktu habis saat berjalan' }) +
      simSel('sim-host', 'Server terbaca agen baru', S.hostSim, { 'db-baru.rs.local': 'Server baru', 'db-klinis.rs.local': 'Server yang sudah ada', 'vm-dr.rs.local': 'Server runner-b' }) +
      simSel('sim-runner-host', 'Server terbaca runner baru', S.runnerHostSim, { 'vm-baru.rs.local': 'Server khusus', 'db-utama.rs.local': 'Server production' }) +
      simSel('sim-agent', 'Perubahan jadwal', S.agentReject ? 'reject' : 'ok', { ok: 'Diterapkan agen', reject: 'Ditolak agen' }) + '</div>';
    return h;
  }
  function configTab() {
    var h = tbl(['Variabel', 'Nilai'], D.env.map(function (e) {
      var val = e[0] === 'MOUNT_MARKER' && nasDown() ? (S.nas.state === 'hang' ? '.lumbung-mounted · pemeriksaan melewati batas 10 detik' : '.lumbung-mounted · tidak ditemukan') : e[1];
      return '<tr><td><code>' + e[0] + '</code></td><td>' + esc(val) + '</td></tr>';
    }));
    h += '<p class="small muted" style="margin-top:8px">Bila NFS belum ter-mount, file penanda tidak ditemukan: container tetap berjalan, upload ditolak (503), agen mengulang otomatis, dan alert dikirim. Bila NAS macet, pemeriksaan berhenti di batas 10 detik dan hasilnya sama. Status terakhir juga ada di <code>/healthz</code>.</p>';
    return h;
  }
  // Semua alert: aktif, pulih, dan milik server nonaktif.
  var ALERT_ST = { act: ['bad', 'Aktif'], off: ['off', 'Container nonaktif'], rec: ['ok', 'Pulih'] };
  function allAlerts() {
    var act = activeAlerts(), keys = act.map(function (a) { return a.key; });
    var list = act.map(function (a) { return { cond: a.cond, kind: a.kind, server: a.server, since: a.since, end: '', st: 'act', note: '' }; });
    S.alerts.forEach(function (a) {
      if (keys.indexOf(a.key) >= 0) return;
      if (a.kind === 'Upload' && nasDown()) return;
      list.push({ cond: a.cond, kind: a.kind, server: a.server, since: a.since, end: S.resolved[a.key] ? 'hari ini' : '', st: S.resolved[a.key] ? 'rec' : 'off', note: '' });
    });
    return list.concat(D.recovered.map(function (r) { return { cond: r[0], kind: r[4], server: r[1], since: r[2], end: r[3], st: 'rec', note: '' }; }));
  }
  function alertTab() {
    var all = allAlerts(), F = S.alertF;
    var uniq = function (f) { return all.map(function (a) { return a[f]; }).filter(function (v, i, arr) { return arr.indexOf(v) === i; }).sort(); };
    var sel = function (k, label, opts) {
      return '<select data-in="alert-f" data-k="' + k + '" aria-label="' + label + '"><option value="">Semua ' + label.toLowerCase() + '</option>' + opts.map(function (o) {
        return '<option value="' + esc(o[0]) + '"' + (F[k] === o[0] ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
      }).join('') + '</select>';
    };
    var pair = function (v) { return [v, v]; };
    var list = all.filter(function (a) { return (!F.status || a.st === F.status) && (!F.server || a.server === F.server) && (!F.kind || a.kind === F.kind); });
    var h = '<div class="filters">' + sel('status', 'Status', Object.keys(ALERT_ST).map(function (k) { return [k, ALERT_ST[k][1]]; })) + sel('server', 'Sumber', uniq('server').map(pair)) + sel('kind', 'Jenis', uniq('kind').map(pair)) + '</div>';
    var rows = list.map(function (a) {
      var st = ALERT_ST[a.st], cls = a.st === 'act' ? ' class="row-' + (alertLvl(a)) + '"' : '';
      var srv = srvLink(a);
      return '<tr' + cls + '><td>' + (a.st === 'act' ? '<b>' + a.cond + '</b>' : a.cond) + '</td><td>' + a.kind + '</td><td>' + srv + '</td><td>' + a.since + '</td><td>' + (a.end || '—') + '</td><td>' + pill(st[0], st[1]) + (a.note ? ' <span class="muted small">' + esc(a.note) + '</span>' : '') + '</td></tr>';
    });
    if (!rows.length) rows = ['<tr><td colspan="6"><div class="empty"><b>Tidak ada alert</b>Tidak ada yang cocok dengan filter ini.</div></td></tr>'];
    return h + tbl(['Kondisi', 'Jenis', 'Sumber', 'Mulai', 'Pulih', 'Status'], rows, '<span class="grow">Menampilkan ' + list.length + ' dari ' + all.length + ' alert</span>');
  }
  function retentionTab() {
    var R = D.retention;
    var h = '<div class="policy"><div><small>Harian</small><b>30 hari</b></div><div><small>Bulanan</small><b>12 bulan</b><span class="muted small"> · full tanggal 1</span></div><div><small>Binlog</small><b>3 hari</b></div>' +
      '<div class="note"><b style="font-size:13px">Usulan awal, belum diputuskan</b><br>Ubah lewat <code>.env</code>: <code>RETENTION_DAILY_DAYS</code>, <code>RETENTION_MONTHLY</code>, <code>RETENTION_BINLOG_DAYS</code></div></div>';
    if (nasDown()) h += '<div class="banner banner-bad"><b>Retensi ditunda.</b> Tidak ada file yang dinilai atau dihapus selama NAS tidak tersedia.</div>';
    else h += '<div class="plan"><div><b>Rencana malam ini (31 Okt 01:00)</b> · 412 file · 1,3 TB dibebaskan · NAS jadi ' + (S.nas.state === 'low' ? 24 : 42) + '% bebas</div>' +
      '<span class="muted small" style="margin-left:auto">Mode uji coba (<code>RETENTION_DRY_RUN=true</code>): hanya dicatat, tidak ada yang dihapus</span></div>';
    // File sesi Restore manual yang masih aktif tidak boleh hilang saat script sedang berjalan.
    var lindung = R.lindung.concat(S.rm.filter(rmActive).map(function (x) { return [x.source, x.full + '.xbstream.gz', 'Dipakai <b>Restore manual ' + esc(x.name) + '</b>, sampai sesinya berakhir']; }));
    var tabs = { hapus: 'Akan dihapus (412)', lindung: 'Dilindungi (' + lindung.length + ')', log: 'Sudah dijalankan' };
    h += '<div class="filters"><div class="seg">' + Object.keys(tabs).map(function (k) {
      return '<button data-act="ret-tab" data-v="' + k + '" class="' + (S.retTab === k ? 'on' : '') + '">' + tabs[k] + '</button>';
    }).join('') + '</div></div>';
    if (S.retTab === 'hapus') h += tbl(['Container', 'Jenis', 'File', ['Ukuran', 'num'], 'Alasan'], R.hapus.slice(0, 5).map(function (r) {
      return '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td><td class="mono">' + r[2] + '</td><td class="num">' + r[3] + '</td><td>' + r[4] + '</td></tr>';
    }), '<span class="grow">Menampilkan 5 dari 412 file</span><a href="#" data-act="toast" data-msg="Prototype: unduhan CSV tidak tersedia">Unduh daftar lengkap (CSV)</a>');
    if (S.retTab === 'lindung') h += tbl(['Container', 'File', 'Mengapa tidak dihapus'], lindung.map(function (r) {
      return '<tr><td>' + r[0] + '</td><td class="mono">' + r[1] + '</td><td>' + r[2] + '</td></tr>';
    }));
    if (S.retTab === 'log') h += tbl(['Waktu', 'File', ['Ukuran', 'num'], 'Hasil'], R.log.map(function (r) {
      return '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td><td class="num">' + r[2] + '</td><td>' + pill(r[3], r[4]) + '</td></tr>';
    }));
    h += '<p class="small muted" style="margin-top:8px">Berjalan tiap hari 01:00 WIB dan tidak pernah menghapus satu-satunya backup yang masih valid. Tidak ada tombol hapus manual: penghapusan hanya lewat jadwal supaya tercatat di log.</p>';
    return h;
  }
  function notifTab() {
    var pn = pendingNotif();
    var h = '<div class="filters"><span class="muted small">Tujuan: Telegram grup IT RS</span><span style="margin-left:auto;display:flex;gap:8px"><button class="btn" data-act="notif-test"' + (canWrite() ? '' : ' disabled') + '>Kirim pesan uji</button><button class="btn btn-pri" data-act="notif-retry"' + (pn && canWrite() ? '' : ' disabled') + '>Kirim ulang sekarang</button></span></div>';
    if (S.notifDown) h += '<div class="banner banner-bad"><b>Telegram tidak terjangkau sejak 08:11</b> — ' + pn + ' pesan menunggu. Dicoba lagi tiap 5 menit, bertahap hingga 1 jam.</div>';
    else h += '<div class="banner banner-ok"><b>Telegram terjangkau.</b> Tidak ada pesan yang menunggu.</div>';
    h += tbl(['Dibuat', 'Pesan', ['Percobaan', 'num'], 'Status'], S.notif.map(function (n) {
      return '<tr class="row-' + (n.ok ? 'ok' : 'bad') + '"><td>' + n.at + '</td><td>' + esc(n.msg) + '</td><td class="num">' + n.tries + '</td><td>' + (n.ok ? pill('ok', 'Terkirim') : pill('bad', 'Coba lagi 08:20')) + '</td></tr>';
    }));
    h += '<p class="small muted" style="margin-top:8px">Pesan hanya berisi nama container dan kondisi, tanpa isi data atau log.</p>';
    return h;
  }

  // ---------- Render ----------
  var lastRoute = '';
  function render() {
    var r = route(), p = r[0] || '', html;
    var ae = document.activeElement, keepIn = ae && ae.getAttribute && ae.getAttribute('data-in'), keepK = keepIn && ae.getAttribute('data-k');
    // Token restore tampil sekali: hilang begitu halaman sesinya ditinggalkan.
    S.rm.forEach(function (x) { if (x.token && !(p === 'container' && r[2] === 'manual' && r[3] === String(x.id))) x.token = null; });
    if (p === '') html = pageDashboard();
    else if (p === 'container' && !r[1]) html = pageServerList();
    else if (p === 'container' && r[1] === 'tambah') html = pageWizard(location.hash !== lastRoute);
    else if (p === 'container' && r[1]) html = pageServerDetail(r[1], r[2], r[3]);
    else if (p === 'runner' && !r[1]) html = pageRunnerList();
    else if (p === 'runner' && r[1] === 'tambah') html = pageRunnerWizard(location.hash !== lastRoute);
    else if (p === 'runner') html = pageRunnerDetail(r[1]);
    else if (p === 'laporan') html = pageLaporan();
    else if (p === 'pengaturan') html = pagePengaturan();
    else { crumbs(['Tidak ditemukan']); html = '<div class="empty"><b>Halaman tidak ditemukan</b><a href="#/">Kembali ke Dashboard</a></div>'; }
    var loginBanner = S.noLogin ? '<div class="banner banner-warn no-print"><b>Dashboard tanpa login.</b> <code>DASHBOARD_PASS</code> kosong, jadi semua aksi tulis dimatikan dan hanya bisa dibaca.</div>' : '';
    document.getElementById('view').innerHTML = loginBanner + nasBanner() + html;
    if (keepIn) {
      var back = document.querySelector('[data-in="' + keepIn + '"]' + (keepK ? '[data-k="' + keepK + '"]' : ''));
      if (back) back.focus();
    }
    // Hari terbaru paling penting; di layar sempit linimasa mulai dari sisi kanan.
    var tlw = document.querySelector('.tl .tbl-wrap');
    if (tlw) tlw.scrollLeft = tlw.scrollWidth;
    renderNav();
    var key = location.hash;
    if (key !== lastRoute) {
      window.scrollTo(0, 0);
      lastRoute = key;
      var target = p === 'container' && r[2] === 'kesehatan' && r[3] && document.getElementById('kes-' + r[3]);
      if (target) target.scrollIntoView();
    }
  }
  function rerender() {
    var a = document.activeElement;
    if (a && (a.tagName === 'INPUT' || a.tagName === 'SELECT') && a.type !== 'checkbox' && a.type !== 'radio') { renderNav(); return; }
    if (!document.getElementById('modal-back').hidden) { renderNav(); return; }
    render();
  }

  // ---------- Modal & toast ----------
  var modalConfirm = null, modalOpener = null;
  function modal(o) {
    var ae = document.activeElement;
    modalOpener = ae && ae.getAttribute && ae.getAttribute('data-act') ? '[data-act="' + ae.getAttribute('data-act') + '"]' : null;
    document.getElementById('modal-title').innerHTML = o.title;
    document.getElementById('modal-body').innerHTML = o.body;
    var acts = '';
    if (o.cancel !== false) acts += '<button class="btn" data-act="modal-close">' + (o.cancelText || 'Batal') + '</button>';
    if (o.confirm) acts += '<button class="btn ' + (o.danger ? 'btn-danger' : 'btn-pri') + '" data-act="modal-ok">' + o.confirm + '</button>';
    document.getElementById('modal-actions').innerHTML = acts;
    modalConfirm = o.onConfirm || null;
    document.getElementById('modal-back').hidden = false;
    // Aksi berbahaya tidak boleh terkonfirmasi hanya dengan menekan Enter.
    var b = document.querySelector('#modal-actions .btn:' + (o.danger ? 'first-child' : 'last-child'));
    if (b) b.focus();
  }
  function closeModal() { document.getElementById('modal-back').hidden = true; modalConfirm = null; render();
    var op = modalOpener && document.querySelector(modalOpener);
    if (op) op.focus();
  }
  var toastT;
  function toast(msg) {
    var t = document.getElementById('toast');
    t.textContent = msg; t.classList.add('show');
    clearTimeout(toastT); toastT = setTimeout(function () { t.classList.remove('show'); }, 2600);
  }
  function copyText(text) {
    function done() { toast('Disalin ke clipboard'); }
    try {
      if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done, fallback); return; }
    } catch (e) { /* lanjut ke cara lama */ }
    fallback();
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      var copied = false;
      try { copied = document.execCommand('copy'); } catch (e) { /* ditangani di bawah */ }
      if (copied) done(); else toast('Gagal menyalin; pilih teks lalu salin manual');
      document.body.removeChild(ta);
    }
  }

  // ---------- Aksi ----------
  var ACT = {
    'modal-close': function () { closeModal(); },
    'modal-ok': function () { var f = modalConfirm; if (f && f() === false) return; closeModal(); },
    copy: function (el) { copyText(el.getAttribute('data-copy')); },
    toast: function (el) { toast(el.getAttribute('data-msg')); },
    go: function (el) { location.hash = el.getAttribute('data-href'); },
    theme: function () {
      var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem('lumbung-theme', next); } catch (e) { /* tetap berlaku untuk sesi ini */ }
      setTheme(next);
    },
    'ret-tab': function (el) { S.retTab = el.getAttribute('data-v'); render(); },
    'lap-tab': function (el) {
      var v = el.getAttribute('data-v');
      if (v === 'h' && (PERIODS[S.lapTab] || S.lapTab === 'w' || S.lapTab === 'y' || S.lapTab === 'na')) return;
      location.hash = '#/laporan/' + v;
    },
    'restore-sel': function (el) { S.restoreSel = { server: el.getAttribute('data-srv'), i: +el.getAttribute('data-i') }; render(); },
    'rescan-open': function () {
      modal({ title: 'Pindai ulang NAS?', confirm: 'Mulai pindai',
        body: '<ul><li>Membaca semua folder container di NAS dan menambah baris yang hilang di tabel <code>backups</code>.</li><li>SHA256 file lama dihitung ulang, jadi NAS dibaca penuh dan bisa berjam-jam.</li><li>File tidak diubah atau dihapus. Hanya satu pemindaian berjalan dalam satu waktu.</li></ul>',
        onConfirm: function () {
          S.rescan = { running: true };
          setTimeout(function () { S.rescan = { at: nowLabel(), result: '2.417 file dicocokkan · 0 baris baru' }; toast('Pindai ulang NAS selesai'); rerender(); }, 3000);
        } });
    },
    'riw-page': function (el) { S.riw.page += +el.getAttribute('data-d'); render(); },
    'ai-input': function () {
      modal({ title: 'Input yang dikirim ke AI', cancelText: 'Tutup',
        body: '<p class="small muted">Disimpan untuk audit bersama hasilnya. Hanya metrik teragregasi dan metadata.</p><pre class="log" style="border-radius:6px;white-space:pre-wrap">{\n  "tanggal": "2026-10-30",\n  "temuan": [\n    {"server": "lab-db", "metrik": "disk_backup_pct", "nilai": 71, "laju_per_hari": 2.1},\n    {"server": "simrs-utama", "metrik": "conn_peak", "nilai": 468, "max": 500},\n    {"server": "simrs-utama", "metrik": "slow_query", "nilai": 1284, "rata30": 702,\n     "query_teratas": "SELECT * FROM log_aktivitas WHERE user_id = ? AND created_at BETWEEN ? AND ? ..."},\n    {"server": "lab-db", "metrik": "full_backup_gb", "nilai": 18.4, "rata": 26.6},\n    {"server": "lab-db", "metrik": "binlog_hilang", "nomor": "000790"},\n    {"server": "siak-db", "metrik": "upload_gagal", "kode": 422, "jenis": "dump", "alasan": "file kosong"}\n  ]\n}</pre>' });
    },
    'rm-create': function () {
      var f = S.rmForm, err = rmFormErrors(f), s = findServer(f.src);
      if (err.name || err.port || nasDown()) return;
      f.confErr = S.confRO;
      if (f.confErr) { render(); return; }
      var u = rmUntil(s, f), info = rmFulls(s).filter(function (o) { return o.name === f.full; })[0], now = S.clock.getTime();
      var x = { id: ++rmSeq, source: s.name, tag: s.agent ? s.agent.tag : '—', full: f.full, fullSize: info.size, binlog: f.binlog, until: u.until, name: f.name, port: +f.port, created: '30 Okt ' + nowLabel(), createdAt: now, expires: now + f.hours * 3600000, by: 'admin', status: 'wait', step: 0, token: 'lmr_' + genToken().slice(4) };
      S.rm = [x].concat(S.rm);
      S.rmForm = null;
      logRestore(s, 'Buat restore manual', 'Full ' + x.full + ' · sampai ' + x.until + ' → ' + x.name + ' · batas ' + f.hours + ' jam');
      location.hash = rmBase(s.name) + '/' + x.id;
    },
    'rm-run': function (el) { rmStart(rmFind(+el.getAttribute('data-id'))); },
    'rm-extend': function (el) {
      var x = rmFind(+el.getAttribute('data-id'));
      x.expires += RM_EXTEND_HOURS * 3600000;
      logRestore(findServer(x.source) || { name: x.source }, 'Perpanjang restore manual', x.name + ' · sampai ' + hhmm(new Date(x.expires)));
      toast('Batas waktu diperpanjang sampai ' + hhmm(new Date(x.expires)));
      render();
    },
    'rm-revoke': function (el) {
      var x = rmFind(+el.getAttribute('data-id'));
      modal({ title: 'Cabut restore manual ' + esc(x.name) + '?', confirm: 'Cabut', danger: 1,
        body: '<ul><li>Token langsung tidak berlaku, jadi unduhan dan pendaftaran ditolak.</li><li>Script yang sedang berjalan berhenti dan menghapus unduhan serta container sementara.</li><li>' + esc(x.source) + ' dan file di NAS tidak disentuh.</li></ul>',
        onConfirm: function () {
          rmEnd(x, 'revoked', 'Dicabut admin. Token tidak berlaku lagi.');
          logRestore(findServer(x.source) || { name: x.source }, 'Cabut restore manual', x.name);
          toast('Restore manual ' + x.name + ' dicabut');
        } });
    },
    'wiz-go': function (el) { var st = +el.getAttribute('data-step'); if (S.wizard && st < S.wizard.step || st === 3) { S.wizard.step = st; render(); } },
    'wiz-create': function () {
      var w = S.wizard;
      if (!wizCanNext(w)) return;
      w.confErr = S.confRO;
      if (w.confErr) { render(); return; }
      var token = genToken();
      var dr = defaultRunner();
      var s = { name: w.name, host: '—', ip: '—', reg: 'baru saja', regAt: nowLabel(), status: 'wait-hb', runner: dr ? dr.name : null, runnerCad: dr ? dr.name : null, ver: '12.3', agent: { tag: '12.3-a1' }, fullDur: '—', bsched: null, standby: null, sched: { test: { on: true, day: 'Minggu', at: '03:30', full: true, dbs: null }, standby: { on: false, day: 'Harian', at: '01:00', binlog: '1 jam' } }, dbs: [], full: '—', dump: '—', hb: '—', disk: null, restore: null, files: { full: 0, dump: 0, binlog: 0 }, recent: [], log: '' };
      S.servers.push(s);
      w.server = s; w.token = token; w.createdAt = nowLabel(); w.step = 2;
      toast('Perintah dibuat dan agen ditambahkan ke agents.conf');
      render();
    },
    'wiz-verify': function () {
      var w = S.wizard;
      w.tokenHidden = true; w.token = null; w.step = 3;
      if (!w.simStarted) { w.simStarted = true; startVerifySim(w); }
      render();
    },
    'wiz-cancel': function () {
      var w = S.wizard;
      modal({ title: 'Batalkan pendaftaran ' + esc(w.server.name) + '?', confirm: 'Batalkan pendaftaran', danger: 1,
        body: '<p>Baris ' + esc(w.server.name) + ' dihapus dari <code>config/agents.conf</code>, jadi token yang baru dibuat langsung tidak berlaku. Penghapusan ini hanya boleh untuk agen yang baru dibuat di sini dan belum pernah mengirim heartbeat; belum ada file di NAS untuk container ini.</p>',
        onConfirm: function () { S.servers.splice(S.servers.indexOf(w.server), 1); S.wizard = null; location.hash = '#/container'; toast('Pendaftaran dibatalkan'); } });
    },
    'runner-change': function (el) {
      var s = findServer(el.getAttribute('data-srv')), isCad = el.getAttribute('data-role') === 'cad', cur = isCad ? s.runnerCad : s.runner;
      var what = isCad ? 'runner cadangan ' : 'runner uji ';
      var opts = S.runners.filter(function (r) { return r.name !== cur; });
      var usable = opts.filter(function (r) { return runnerFor(s, r); });
      if (!usable.length) { modal({ title: 'Ganti ' + what + esc(s.name), cancelText: 'Tutup', body: '<p>Tidak ada runner lain yang sehat. Untuk pindah ke server lain, <a href="#/runner/tambah" data-act="modal-close">tambahkan runner di server itu</a>, atau <a href="#/runner" data-act="modal-close">lihat daftar runner</a>.</p>' }); return; }
      var other = isCad ? s.runner : s.runnerCad;
      modal({ title: 'Ganti ' + what + esc(s.name), confirm: isCad && usesCad(s) ? 'Pindahkan' : 'Simpan',
        body: '<div class="field"><label for="rn-target">Runner baru</label><select id="rn-target" style="width:100%">' + opts.map(function (r) {
            return '<option value="' + esc(r.name) + '"' + (runnerFor(s, r) ? '' : ' disabled') + '>' + esc(r.name) + ' · ' + esc(r.host) + (runnerFor(s, r) ? ' · scratch ' + r.free + '% bebas' : sameServer(s, r) ? ' · satu server dengan container ini' : ' · tidak tersedia') + (r.name === other ? (isCad ? ' · runner uji' : usesCad(s) ? ' · runner cadangan' : '') : '') + '</option>';
          }).join('') + '</select></div>' +
          (isCad && !usesCad(s) ? '<ul><li>Dipakai saat database cadangan diaktifkan. Sekarang belum ada database cadangan yang perlu dipindahkan.</li><li>Runner uji tidak berubah.</li></ul>'
            : isCad ? '<ul><li>Runner baru memulihkan dan menguji full terbaru lebih dulu.</li><li>Database cadangan di runner lama baru dihapus setelah lolos. Bila gagal, database cadangan lama tetap dipakai.</li><li>Runner uji tidak berubah.</li></ul>'
            : '<ul><li>Berlaku mulai uji berikutnya. Container sementara dan datanya tidak dipindahkan, karena dihapus sesudah tiap uji.</li><li>Database cadangan tidak berubah.</li></ul>'),
        onConfirm: function () {
          var target = document.getElementById('rn-target').value;
          if (!isCad) { s.runner = target; toast('Runner uji ' + s.name + ': ' + target); return; }
          if (!usesCad(s)) { s.runnerCad = target; toast('Runner cadangan ' + s.name + ': ' + target); return; }
          s.runnerPending = target;
          setTimeout(function () {
            if (findServer(s.name) !== s || s.runnerPending !== target) return;
            s.runnerPending = null; s.runnerCad = target;
            if (s.standby) s.standby.port = 33081;
            toast('Runner cadangan ' + s.name + ': ' + target); rerender();
          }, 4000);
        } });
    },
    'runner-off': function (el) {
      var r = findRunner(el.getAttribute('data-run')), n = containersOf(r.name).length;
      modal({ title: 'Nonaktifkan ' + esc(r.name) + '?', confirm: 'Nonaktifkan', danger: 1,
        body: '<ul><li>Baris <code>' + esc(r.name) + '</code> di <code>config/runners.conf</code> diberi <code>#</code>; token tidak dihapus.</li><li>Runner ditolak (401) dan tidak mengambil tugas baru. ' + (n ? '<b>' + n + ' container</b> berhenti diuji atau cadangannya berhenti di-restore, sampai dipindah ke runner lain.' : 'Runner ini tidak menangani container.') + '</li><li>Database cadangan di mesin itu tidak dihapus, tetapi tidak lagi di-restore.</li></ul>',
        onConfirm: function () {
          if (S.confRO) { toast('Gagal menulis config/runners.conf; runner tetap aktif'); return; }
          r.state = 'off'; r.since = '30 Okt ' + nowLabel(); r.def = false; r.free = null; toast(r.name + ' dinonaktifkan'); } });
    },
    'rw-create': function () {
      var w = S.runWiz;
      if (!runWizCanNext(w)) return;
      w.confErr = S.confRO;
      if (w.confErr) { render(); return; }
      var r = { name: w.name, host: '—', ip: '—', reg: 'baru saja', ver: '—', state: 'wait', since: '', hb: '—', free: null, freeTxt: '', dlErr: '' };
      S.runners.push(r);
      w.runner = r; w.token = genToken(); w.createdAt = nowLabel(); w.step = 2;
      toast('Token dibuat dan ditambahkan ke runners.conf');
      render();
    },
    'rw-verify': function () {
      var w = S.runWiz;
      if (!w.saved) return;
      w.token = null; w.step = 3;
      var r = w.runner;
      setTimeout(function () {
        if (findRunner(r.name) !== r || r.state !== 'wait') return;
        r.host = S.runnerHostSim; r.state = 'ok'; r.ip = '10.10.9.30'; r.ver = '1.0.0'; r.hb = 'baru saja'; r.free = 88; r.freeTxt = '880 GB dari 1 TB';
        w.finished = true;
        toast(r.name + ' terhubung dan berhasil mengunduh file uji');
        rerender();
      }, 4000);
      render();
    },
    'rw-cancel': function () {
      var w = S.runWiz;
      modal({ title: 'Batalkan pendaftaran ' + esc(w.name) + '?', confirm: 'Batalkan pendaftaran', danger: 1,
        body: '<p>Baris ' + esc(w.name) + ' dihapus dari <code>config/runners.conf</code>, jadi token yang baru dibuat langsung tidak berlaku. Ini hanya berlaku untuk runner yang belum pernah mengirim heartbeat.</p>',
        onConfirm: function () { S.runners.splice(S.runners.indexOf(w.runner), 1); S.runWiz = null; location.hash = '#/runner'; toast('Pendaftaran dibatalkan'); } });
    },
    'bs-edit': function (el) {
      var s = findServer(el.getAttribute('data-srv')), b = s.bsched, cur = b.want || b;
      S.bsEdit = s;
      modal({ title: 'Jadwal backup ' + esc(s.name), confirm: 'Simpan',
        body: BS_KINDS.map(function (x) { return bsControls(x[0], x[1], cur[x[0]]); }).join('') +
          '<p class="small muted" style="margin:0">Waktu memakai WIB. Agen menerima jadwal pada heartbeat berikutnya. Bila gagal diterapkan, crontab lama tetap dipakai.</p><div id="bs-err"></div>',
        onConfirm: function () {
          if (!bsPreview(s)) { document.getElementById('bs-err').innerHTML = '<div class="msg-err">✕ Perbaiki isian yang ditandai.</div>'; return false; }
          var want = { full: bsRead('full'), dump: bsRead('dump'), binlog: bsRead('binlog') }, base = b.want || b, detail = bsChanges(base, want);
          S.bsEdit = null;
          if (!detail) { toast('Tidak ada perubahan'); return; }
          bsSubmit(s, want, detail);
        } });
      bsPreview(s);
    },
    'req-open': function (el) {
      var s = findServer(el.getAttribute('data-srv'));
      var opt = function (v, label, kind) { var busy = reqActive(s, kind); return '<option value="' + esc(v) + '"' + (busy ? ' disabled' : '') + '>' + esc(label) + (busy ? ' (sudah diminta)' : '') + '</option>'; };
      S.reqEdit = s;
      modal({ title: 'Backup sekarang: ' + esc(s.name), confirm: 'Minta backup',
        body: '<div class="field"><label for="br-kind">Yang dibackup</label><select id="br-kind" data-in="br-kind" style="width:100%">' +
          opt('full', 'Full', 'full') + opt('dump', 'Dump semua database', 'dump') +
          s.dbs.map(function (d) { return opt('dump:' + d, 'Dump ' + d, 'dump'); }).join('') + opt('dump:_users', 'Dump _users (akun dan hak akses)', 'dump') +
          opt('binlog', 'Arsip binlog', 'binlog') + '</select></div><div id="br-info"></div>',
        onConfirm: function () {
          var v = document.getElementById('br-kind').value, kind = v.split(':')[0], db = v.split(':')[1] || null;
          if (reqActive(s, kind)) { document.getElementById('br-info').innerHTML = '<div class="msg-err">✕ Masih ada permintaan ' + esc(kind) + ' yang aktif.</div>'; return false; }
          var r = { id: ++reqSeq, server: s.name, kind: kind, db: db, at: '30 Okt ' + nowLabel(), by: 'admin', status: 'Menunggu', note: 'Kedaluwarsa ' + hhmm(new Date(S.clock.getTime() + 900000)) + ' bila agen tidak mengambilnya' };
          S.reqs = [r].concat(S.reqs);
          logControl(s, 'Minta backup', reqKindLabel(r));
          S.reqEdit = null;
          toast('Permintaan dibuat, menunggu agen ' + s.name);
          reqRun(s, r);
        } });
      reqInfo(s);
    },
    'req-cancel': function (el) {
      var r = S.reqs.filter(function (x) { return x.id === +el.getAttribute('data-id'); })[0];
      if (!r || r.status !== 'Menunggu') return;
      r.status = 'Dibatalkan'; r.note = 'Dibatalkan sebelum diambil agen';
      toast('Permintaan dibatalkan');
      render();
    },
    'sched-test': function (el) {
      var s = findServer(el.getAttribute('data-srv')), t = s.sched.test;
      modal({ title: 'Jadwal uji restore ' + esc(s.name), confirm: 'Simpan',
        body: '<label class="check" style="margin-bottom:12px"><input type="checkbox" id="st-on"' + (t.on ? ' checked' : '') + '> Jalankan uji terjadwal</label>' +
          '<div class="grid2"><div class="field"><label for="st-day">Kapan</label>' + daySel('st-day', t.day) + '</div><div class="field"><label for="st-at">Jam (WIB)</label>' + timeSel('st-at', t.at) + '</div></div>' +
          '<div class="field"><label>Yang diuji</label><label class="check" style="display:flex;margin-bottom:6px"><input type="checkbox" id="st-full"' + (t.full ? ' checked' : '') + '> Full backup (seluruh container)</label>' +
          s.dbs.map(function (x) { return '<label class="check" style="display:flex;margin-bottom:6px"><input type="checkbox" class="st-db" value="' + esc(x) + '"' + (!t.dbs || t.dbs.indexOf(x) >= 0 ? ' checked' : '') + '> Dump ' + esc(x) + '</label>'; }).join('') +
          '<div class="hint">Dump <code>_users</code> dan rantai binlog selalu diperiksa.</div><div id="st-err"></div></div>',
        onConfirm: function () {
          var boxes = [].slice.call(document.querySelectorAll('.st-db')), picked = boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
          var full = document.getElementById('st-full').checked, on = document.getElementById('st-on').checked;
          if (on && !full && !picked.length && s.dbs.length) { document.getElementById('st-err').innerHTML = '<div class="msg-err">✕ Pilih minimal full atau satu database.</div>'; return false; }
          s.sched.test = { on: on, day: document.getElementById('st-day').value, at: document.getElementById('st-at').value, full: full, dbs: picked.length === s.dbs.length ? null : picked };
          toast('Jadwal uji ' + s.name + ' disimpan');
        } });
    },
    'sched-standby': function (el) {
      var s = findServer(el.getAttribute('data-srv')), sb = s.sched.standby;
      modal({ title: 'Jadwal database cadangan ' + esc(s.name), confirm: 'Simpan',
        body: '<label class="check" style="margin-bottom:12px"><input type="checkbox" id="sb-on"' + (sb.on ? ' checked' : '') + '> Siapkan database cadangan untuk container ini</label>' +
          '<div class="grid2"><div class="field"><label for="sb-day">Pulihkan full</label>' + daySel('sb-day', sb.day) + '</div><div class="field"><label for="sb-at">Jam (WIB)</label>' + timeSel('sb-at', sb.at) + '</div></div>' +
          '<div class="field"><label for="sb-binlog">Terapkan binlog baru</label><select id="sb-binlog" style="width:100%">' + [['15 menit', 'Tiap 15 menit'], ['1 jam', 'Tiap 1 jam'], ['6 jam', 'Tiap 6 jam'], ['off', 'Hanya saat full dipulihkan']].map(function (o) { return '<option value="' + o[0] + '"' + (sb.binlog === o[0] ? ' selected' : '') + '>' + o[1] + '</option>'; }).join('') + '</select>' +
          '<div class="hint">Binlog baru tersedia setelah terkirim ke NAS, jadi database cadangan selalu tertinggal dari produksi.</div></div>' +
          '<p class="small">Butuh ruang di mesin runner sebesar data asli, ditambah ruang yang sama saat database cadangan baru disiapkan.' + (s.standby ? ' <b>Bila dimatikan, database cadangan yang ada dihapus dari mesin runner.</b>' : '') + '</p>',
        onConfirm: function () {
          var on = document.getElementById('sb-on').checked;
          s.sched.standby = { on: on, day: document.getElementById('sb-day').value, at: document.getElementById('sb-at').value, binlog: document.getElementById('sb-binlog').value };
          if (!on && s.standby) { logRestore(s, 'Hapus database cadangan', 'Full ' + s.standby.full + ' · jadwal dimatikan'); s.standby = null; if (s.name === 'lab-db') S.resolved['cad-lab'] = true; }
          toast('Jadwal restore ' + s.name + ' disimpan');
        } });
    },
    'sb-run': function (el) {
      var s = findServer(el.getAttribute('data-srv')), choices = fullChoices(s), cur = cadRunnerOf(s), pick = runnerFor(s, cur) ? cur.name : S.runners.filter(function (x) { return runnerFor(s, x); })[0].name;
      S.restoreEdit = s;
      modal({ title: 'Restore sekarang: ' + esc(s.name), confirm: 'Mulai restore',
        body: '<div class="field"><label for="rn-full">Full yang dipulihkan</label><select id="rn-full" data-in="rn-full" style="width:100%">' + choices.map(function (c, i) {
            return '<option value="' + i + '">' + esc(c.name) + (c.passed ? ' · lolos uji ' + esc(c.when) : ' · belum diuji') + '</option>';
          }).join('') + '</select></div><div class="field"><label for="rn-runner">Restore di</label><select id="rn-runner" data-in="rn-runner" style="width:100%">' + S.runners.map(function (r) {
            return '<option value="' + esc(r.name) + '"' + (r.name === pick ? ' selected' : '') + (runnerFor(s, r) ? '' : ' disabled') + '>' + esc(r.name) + ' · ' + esc(r.host) + (runnerFor(s, r) ? '' : sameServer(s, r) ? ' · satu server dengan container ini' : ' · tidak tersedia') + '</option>';
          }).join('') + '</select><div class="hint">Untuk restore ke server lain, <a href="#/runner/tambah" data-act="modal-close">tambahkan runner di server itu</a>.</div></div><div id="rn-info"></div>',
        onConfirm: function () {
          var ch = choices[+document.getElementById('rn-full').value], range = binlogRange(s, fullNameKey(ch.name)), until = keyLabel(range.maxKey), runner = document.getElementById('rn-runner').value, moved = runner !== s.runnerCad, port = s.standby && !moved ? s.standby.port : 33090, failSim = S.restoreSim === 'fail';
          S.restoreEdit = null;
          S.standbyRun = { server: s.name, runner: runner };
          logRestore(s, 'Restore sekarang', 'Full ' + ch.name + ' · sampai ' + until + ' · di ' + runner);
          toast('Restore ' + s.name + ' dimulai di ' + runner);
          setTimeout(function () {
            if (!S.standbyRun || S.standbyRun.server !== s.name) return;
            S.standbyRun = null;
            var rec = { server: s.name, runner: runner, when: '30 Okt ' + nowLabel(), full: ch.name, until: failSim ? '—' : until, dur: failSim ? '6 mnt' : '24 mnt', ok: !failSim, manual: true };
            if (failSim) rec.reason = 'Pemeriksaan gagal: tabel utama tidak terbaca. ' + (s.standby ? 'Database cadangan lama tetap dipakai.' : 'Tidak ada database cadangan yang dibuat.');
            else { s.standby = { full: ch.name, until: until, port: port }; s.runnerCad = runner; if (s.name === 'lab-db') S.resolved['cad-lab'] = true; }
            S.standby = [rec].concat(S.standby);
            toast(failSim ? 'Restore ' + s.name + ' gagal' : 'Restore ' + s.name + ' selesai');
            rerender();
          }, 3000);
        } });
      restoreInfo(s, choices);
    },
    'cad-promote': function (el) {
      var s = findServer(el.getAttribute('data-srv')), r = cadRunnerOf(s), n = 2;
      while (findServer(s.name + '-' + n)) n++;
      var others = containersOf(r.name).filter(function (x) { return x !== s && x.status !== 'off'; });
      var nextRunner = runnerForPromoted(r.host);
      var runnerNote = !nextRunner ? '<b>Belum ada runner di server lain.</b> Container baru belum diuji dan tidak punya cadangan, dan alert aktif sampai runner ditetapkan.'
        : runnerReady(nextRunner) ? '<b>Uji dan cadangannya memakai ' + esc(nextRunner.name) + '</b> di ' + esc(nextRunner.host) + '.'
        : '<b>Uji dan cadangannya memakai ' + esc(nextRunner.name) + ', yang sedang bermasalah.</b> Uji baru berjalan setelah runner itu pulih, atau setelah diganti.';
      modal({ title: 'Naikkan container cadangan ' + esc(s.name) + ' ke production?', confirm: 'Naikkan ke production', danger: 1,
        body: '<div class="field"><label for="pm-name">Nama container baru</label><input type="text" id="pm-name" data-in="pm-name" value="' + esc(s.name + '-' + n) + '" autocomplete="off" spellcheck="false" style="width:100%"><div class="hint">Token dan folder NAS terpisah dari ' + esc(s.name) + ', supaya file backup tidak tercampur bila container lama hidup lagi.</div><div id="pm-msg"></div></div>' +
          '<ul><li><b>Data hanya sampai ' + esc(s.standby.until) + '</b>. Perubahan sesudah itu tidak ada di sini.</li>' +
          '<li><b>Tidak bisa dibatalkan.</b> Container ini keluar dari siklus restore dan menjadi container production di <b>' + esc(r.host) + '</b>, dengan full backup langsung dijalankan' + (nasDown() ? '. NAS sedang bermasalah, jadi full baru tersimpan setelah NAS pulih' : '') + '.</li>' +
          '<li>' + runnerNote + ' Tugas container lain di ' + esc(r.name) + ' tetap berjalan' + (others.length ? ' (' + others.map(function (x) { return esc(x.name); }).join(', ') + ')' : '') + ', dan ikut memakai disk serta CPU server ini.</li>' +
          '<li>LumbungDB tidak memindahkan aplikasi. Admin membuka port, mengarahkan aplikasi, dan memastikan ' + esc(s.name) + ' tidak lagi menerima tulisan.</li></ul>',
        onConfirm: function () {
          var name = document.getElementById('pm-name').value.trim();
          if (nameError(name)) return false;
          // Container baru butuh baris di agents.conf; bila gagal ditulis, tidak ada yang berubah.
          if (S.confRO) { document.getElementById('pm-msg').innerHTML = '<div class="msg-err">✕ Tidak bisa menulis <code>config/agents.conf</code>. Container cadangan tidak dinaikkan dan tidak ada yang berubah; perbaiki izin file lalu coba lagi.</div>'; return false; }
          var at = '30 Okt ' + nowLabel(), full = s.standby.full, until = s.standby.until, port = s.standby.port, sizeFull = (fullRows(s)[0] || [])[4] || '—';
          var c = containerFromSource(s, name, r.host, r.ip, { from: s.name, until: until, port: port });
          s.standby = null; s.sched.standby.on = false;
          if (s.name === 'lab-db') S.resolved['cad-lab'] = true;
          S.servers.push(c);
          logRestore(s, 'Naikkan ke production', 'Full ' + full + ' · data sampai ' + until + ' → ' + name + ' di ' + r.host);
          toast(name + ' menjadi production di ' + r.host);
          location.hash = '#/container/' + encodeURIComponent(name);
          firstFullSim(c, sizeFull);
        } });
    },
    nonaktifkan: function (el) {
      var s = findServer(el.getAttribute('data-srv'));
      modal({ title: 'Nonaktifkan ' + esc(s.name) + '?', confirm: 'Nonaktifkan', danger: 1,
        body: '<ul><li>Baris <code>' + esc(s.name) + '</code> di <code>config/agents.conf</code> diberi <code>#</code>; token tidak dihapus.</li><li>Agen ditolak (401), jadi heartbeat dan upload berhenti. Alert untuk container ini juga berhenti (' + alertsFor(s.name).length + ' alert aktif).</li><li><b>File di NAS dan riwayat tidak dihapus</b> dan tetap ikut aturan retensi.</li></ul><p class="small muted">Untuk mengaktifkan lagi, hapus <code>#</code> di baris itu lalu restart LumbungDB.</p>',
        onConfirm: function () {
          if (S.confRO) { toast('Gagal menulis config/agents.conf; container tetap aktif'); return; }
          s.status = 'off'; s.offSince = '30 Okt 2026'; if (S.wizard && S.wizard.server === s) S.wizard = null; toast(s.name + ' dinonaktifkan'); } });
    },
    'restore-open': function (el) {
      var srv = el.getAttribute('data-srv');
      modal({ title: 'Jalankan uji restore ' + esc(srv), confirm: 'Mulai uji',
        body: (findServer(srv).sched.test.full ? '<div class="field"><label for="rs-file">Full backup</label><select id="rs-file" style="width:100%">' +
            (srv === 'lab-db' ? '<option value="2026-10-28_0200">Terbaru · 2026-10-28_0200</option><option value="2026-10-27_0200">2026-10-27_0200</option>' : '<option value="terbaru">Terbaru</option>') + '</select></div>' : '') +
          '<p class="small">Cakupan mengikuti jadwal: ' + esc(scopeLabel(findServer(srv))) + ', ditambah <code>_users</code> dan rantai binlog. Dijalankan oleh <b>' + esc(findServer(srv).runner) + '</b>.' + '</p>' +
          '<p class="small muted">Dipulihkan ke container sementara <code>lumbung-restore-*</code> di mesin runner, lalu dihapus. Tidak menyentuh produksi.</p>',
        onConfirm: function () {
          var fileEl = document.getElementById('rs-file'), full = fileEl ? fileEl.value : '—';
          var fail = srv === 'lab-db' && full === '2026-10-27_0200';
          // Rantai dihitung sejak full terakhir yang lolos: full baru yang lolos melewati nomor yang hilang.
          var srvObj = findServer(srv), gap = fail && srvObj.binlogGap ? srvObj.binlogGap.no : null;
          var binlog = '…–terbaru';
          var scope = JSON.parse(JSON.stringify({ full: srvObj.sched.test.full, dbs: srvObj.sched.test.dbs }));
          var steps = restoreSteps(findServer(srv), full, binlog, scope);
          var list = steps.filter(function (st) { return !st.g; });
          S.restoreRun = { server: srv, runner: srvObj.runner, scope: scope, full: full, binlog: binlog, steps: steps, step: 0, fail: fail, gap: gap };
          var tick = setInterval(function () {
            var run = S.restoreRun;
            run.step++;
            // Full gagal di --prepare: sisa langkah full dilewati, uji lanjut ke dump.
            if (run.fail && run.step > FULL_FAIL_AT) while (run.step < list.length && list[run.step].k === 'full') run.step++;
            if (run.step >= list.length) { clearInterval(tick); finishRestore(run, !run.fail); return; }
            rerender();
          }, 1100);
          location.hash = '#/container/' + encodeURIComponent(srv) + '/restore';
        } });
    },
    print: function () { window.print(); },
    'tl-cell': function (el) {
      var s = findServer(el.getAttribute('data-srv')), day = +el.getAttribute('data-day'), c = tlCell(s, day), rows = tlRows(s, day);
      modal({ title: esc(s.name) + ' · ' + day + ' Okt 2026', cancelText: 'Tutup', confirm: 'Buka riwayat container',
        onConfirm: function () { location.hash = '#/container/' + encodeURIComponent(s.name) + '/backup'; },
        body: '<p class="small muted">' + c.d + '</p>' + (rows.length ? tbl(['Jenis', 'File', ['Ukuran', 'num'], 'Diterima'], rows.map(function (r) {
          return '<tr><td>' + r[2] + '</td><td class="mono">' + esc(r[3]) + '</td><td class="num">' + r[4] + '</td><td>' + r[5] + '</td></tr>';
        })) : '<div class="empty"><b>Tidak ada file diterima</b>Periksa log backup container ini.</div>') });
    },
    'open-lap': function (el) { location.hash = '#/laporan/' + el.getAttribute('data-v'); },
    'notif-retry': function () {
      S.notif.forEach(function (n) { if (!n.ok) { n.ok = true; n.tries++; } });
      S.notifDown = false; toast('Semua pesan di antrean terkirim'); render();
    },
    'notif-test': function () {
      S.notif.unshift({ at: nowLabel(), msg: 'Pesan uji dari LumbungDB', tries: 1, ok: !S.notifDown });
      toast(S.notifDown ? 'Telegram tidak terjangkau · pesan uji masuk antrean' : 'Pesan uji terkirim'); render();
    }
  };
  function reqInfo(s) {
    var box = document.getElementById('br-info'), kind = document.getElementById('br-kind').value.split(':')[0], m = D.metrics[s.name], h = '';
    if (kind === 'full') h += '<div class="banner banner-warn"><b>Full berjalan di mesin produksi, sekarang pukul ' + nowLabel() + ' WIB.</b>' + (m ? ' Full terakhir ' + num(m.fullSize[0], 1) + ' GB' + (s.fullDur && s.fullDur !== '—' ? ', sekitar ' + esc(s.fullDur) : '') + '.' : '') + ' Sesudah berhasil, arsip binlog lokal yang sudah terkirim dan tercakup full ini dihapus.</div>';
    if (kind === 'dump') h += '<p class="small muted">Dump memakai <code>--single-transaction</code>, tetapi tetap menambah beban baca selama berjalan.</p>';
    if (kind === 'binlog') h += '<p class="small muted">Menutup binlog aktif dan mengarsipkannya, misalnya sebelum pemeliharaan.</p>';
    if (!agentAlive(s)) h += '<div class="banner banner-bad"><b>Agen tidak merespons.</b> Permintaan menunggu, dan kedaluwarsa bila tidak diambil dalam 15 menit.</div>';
    h += '<p class="small muted" style="margin-bottom:0">Selesai berarti script berhasil dan semua file hasilnya tersimpan di NAS. Permintaan yang tidak selesai tidak diulang otomatis.</p>';
    box.innerHTML = h;
  }
  function restoreInfo(s, choices) {
    var ch = choices[+document.getElementById('rn-full').value], range = binlogRange(s, fullNameKey(ch.name)), r = findRunner(document.getElementById('rn-runner').value), old = cadRunnerOf(s), sb = s.sched.standby, h = '';
    if (!ch.passed) h += '<div class="banner banner-warn"><b>Full ini belum pernah lolos uji restore.</b> Runner tetap memeriksa hasilnya sebelum dipakai.</div>';
    if (range.stopped) h += '<div class="banner banner-bad"><b>Binlog ' + esc(range.stopped) + ' hilang.</b> Data hanya sampai ' + keyLabel(range.maxKey) + '.</div>';
    h += '<ul><li>Dipulihkan di <b>' + esc(r.name) + '</b> (' + esc(r.host) + ') ke volume baru, dari full lalu binlog sampai <b>' + keyLabel(range.maxKey) + '</b>.</li>' +
      '<li><b>Database produksi tidak disentuh.</b> Hasilnya database cadangan <code>read_only</code>; memindahkan aplikasi tetap keputusan admin.</li>' +
      (s.standby ? '<li>Setelah lolos pemeriksaan, hasilnya menggantikan database cadangan yang ada (data sampai ' + esc(s.standby.until) + ')' + (old && old !== r ? ', dan yang lama di ' + esc(old.name) + ' dihapus' : '') + '. Bila gagal, database cadangan lama tetap dipakai.</li>' : '<li>Butuh ruang di mesin runner sebesar data asli.</li>') +
      (sb.on ? '<li>Restore terjadwal berikutnya tetap berjalan, dan bisa menggantinya dengan full yang lebih baru.</li>' : '<li>Tidak terjadwal, jadi hasilnya tidak di-restore otomatis.</li>') + '</ul>';
    document.getElementById('rn-info').innerHTML = h;
  }
  function finishRestore(run, ok) {
    S.restoreRun = null;
    var rec = { server: run.server, runner: run.runner, scope: run.scope, when: '30 Okt ' + nowLabel(), full: run.full, binlog: run.binlog, dur: ok ? '12 mnt' : '8 mnt', ok: ok, fail: ok ? null : 'full', binlogGap: run.gap };
    S.restores = [rec].concat(S.restores);
    S.restoreSel = null;
    var s = findServer(run.server); if (s) s.restore = { ok: ok, date: '30 Okt' };
    if (run.server === 'lab-db') {
      S.resolved['restore-lab'] = ok;
      if (ok) { S.resolved['binlog-lab'] = true; if (s) s.binlogGap = null; }
    }
    toast('Uji restore ' + run.server + (ok ? ' lolos' : ' gagal'));
    rerender();
  }

  // Pilihan data di Pengaturan › Simulasi prototype; selalu mulai dari data contoh asli.
  function applySim(kind) {
    var b = JSON.parse(BASE);
    S.sim = kind; S.wizard = null; S.resolved = {}; S.restoreSel = null; S.notifDown = true;
    S.servers = b.servers; S.restores = b.restores; S.integrity = b.integrity; S.notif = b.notif; S.restoreLog = b.restoreLog; S.standby = b.standby; S.standbyRun = null;
    S.reqs = b.reqs; S.ctrlLog = b.ctrlLog; S.manualFiles = []; S.rm = b.rm; S.rmForm = null;
    applyRunnerSim(kind === 'kosong' ? 'none' : kind === 'sehat' ? 'ok' : 'normal');
    D.metrics = b.metrics; D.matrix = b.matrix;
    if (kind === 'kosong') { S.servers = []; S.restores = []; S.notif = []; S.restoreLog = []; S.standby = []; S.reqs = []; S.ctrlLog = []; S.rm = []; S.notifDown = false; }
    if (kind !== 'sehat') return;
    S.alerts.forEach(function (x) { S.resolved[x.key] = true; });
    S.notifDown = false;
    S.notif.forEach(function (n) { n.ok = true; });
    S.integrity.forEach(function (it) { it.reviewed = it.reviewed || '30 Okt · admin'; });
    S.restores = S.restores.filter(function (r) { return r.ok; });
    S.standby = S.standby.filter(function (r) { return r.ok; });
    S.reqs = S.reqs.filter(function (r) { return r.status === 'Selesai'; });
    S.servers.forEach(function (s) {
      if (s.status !== 'active') return;
      s.binlogGap = null; s.hb = '1 menit lalu';
      if (s.standby && s.name === 'lab-db') { s.standby.full = '2026-10-30_0200'; s.standby.until = '30 Okt 08:05'; }
      (s.dumps || []).forEach(function (d) { if (d[2] !== 'ok') { d[1] = s.dump; d[2] = 'ok'; d[3] = ''; } });
      s.upload = { pending: 0, lastOk: '6 menit lalu', err: null };
      s.log = s.log.split('\n').filter(function (l) { return l[0] !== '!'; }).join('\n');
      var last = S.restores.filter(function (r) { return r.server === s.name; })[0];
      if (last) s.restore = { ok: true, date: last.when.split(' ').slice(0, 2).join(' ') };
    });
    var lab = D.metrics['lab-db'], sim = D.metrics['simrs-utama'], lf = S.servers.filter(function (s) { return s.name === 'lab-db'; })[0];
    lf.full = '6 jam lalu'; lf.files.full = 30;
    lab.diskBackup = [52, 0.05]; lab.fullSize = [26.6, 26.6]; lab.fullDrop = false; lab.bp = [99, 0, 0.1];
    sim.conn = [300, 0, 18]; sim.rejected = [0, 0, 0]; sim.slow = [700, 0, 40]; sim.deadlock = [0, 0, 0.5];
    D.metrics['farmasi-db'].stale = false; D.metrics['kepegawaian-db'].partial = false;
    D.matrix['simrs-utama'][0][0] = '300/500'; D.matrix['simrs-utama'][2][0] = '700'; D.matrix['simrs-utama'][3][0] = '0';
    D.matrix['lab-db'][1][0] = '99,0%'; D.matrix['lab-db'][6][0] = '52%'; D.matrix['lab-db'][7][0] = 'normal';
    Object.keys(D.matrix).forEach(function (k) { D.matrix[k].forEach(function (c) { c[1] = 'ok'; }); });
  }

  var INPUT = {
    'rm-full': function (el) { S.rmForm.full = el.value; rmCheckForm(); },
    'rm-binlog': function (el) { S.rmForm.binlog = el.value; rmCheckForm(); },
    'rm-hours': function (el) { S.rmForm.hours = +el.value; },
    'rm-name': function (el) { S.rmForm.name = el.value.trim(); rmCheckForm(); },
    'rm-port': function (el) { S.rmForm.port = el.value.trim(); rmCheckForm(); },
    'sim-rm': function (el) { S.rmSim = el.value; render(); },
    'wiz-name': function (el) {
      var w = S.wizard; w.name = el.value.trim();
      var err = w.name ? nameError(w.name) : '';
      el.classList.toggle('invalid', !!err);
      document.getElementById('wiz-path').textContent = '/lumbungdb/' + (w.name || '<nama>') + '/';
      document.getElementById('wiz-msg').innerHTML = w.name ? (err ? '<div class="msg-err">✕ ' + err + '</div>' : '<div class="msg-ok">✓ Nama tersedia</div>') : '';
      document.getElementById('wiz-next1').disabled = !wizCanNext(w);
    },
    'bs-input': function () { if (S.bsEdit) bsPreview(S.bsEdit); },
    'br-kind': function () { if (S.reqEdit) reqInfo(S.reqEdit); },
    'sim-req': function (el) { S.reqSim = el.value; render(); },
    'pm-name': function (el) {
      var err = nameError(el.value.trim());
      el.classList.toggle('invalid', !!err);
      document.getElementById('pm-msg').innerHTML = err ? '<div class="msg-err">✕ ' + err + '</div>' : '';
      document.querySelector('#modal-actions [data-act="modal-ok"]').disabled = !!err;
    },
    'rn-full': function () { if (S.restoreEdit) restoreInfo(S.restoreEdit, fullChoices(S.restoreEdit)); },
    'rn-runner': function () { if (S.restoreEdit) restoreInfo(S.restoreEdit, fullChoices(S.restoreEdit)); },
    'sim-restore': function (el) { S.restoreSim = el.value; render(); },
    'sim-host': function (el) { S.hostSim = el.value; render(); },
    'sim-runner-host': function (el) { S.runnerHostSim = el.value; render(); },
    'sim-agent': function (el) { S.agentReject = el.value === 'reject'; render(); },
    'rw-name': function (el) {
      var w = S.runWiz; w.name = el.value.trim();
      var err = w.name ? runnerNameError(w.name) : '';
      el.classList.toggle('invalid', !!err);
      document.getElementById('rw-msg').innerHTML = w.name ? (err ? '<div class="msg-err">✕ ' + err + '</div>' : '<div class="msg-ok">✓ Nama tersedia</div>') : '';
      document.getElementById('rw-next1').disabled = !runWizCanNext(w);
    },
    'rw-saved': function (el) { S.runWiz.saved = el.checked; document.getElementById('rw-next2').disabled = !el.checked; },
    'wiz-port': function (el) { S.wizard.port = el.value; },
    'riw-q': function (el) {
      S.riw.q = el.value; S.riw.page = 0;
      document.getElementById('riw-table').innerHTML = riwTable(findServer(el.getAttribute('data-srv')));
    },
    'riw-kind': function (el) { S.riw.kind = el.value; S.riw.page = 0; render(); },
    'lap-jenis': function (el) { location.hash = '#/laporan/' + el.value; },
    'lap-arsip': function (el) {
      if (el.value === 'na') { location.hash = '#/laporan/na'; return; }
      if (S.lapTab === 'na') { location.hash = '#/laporan/h'; }
      if (el.value !== '0') { toast('Prototype: hanya laporan terbaru yang berisi contoh'); el.value = '0'; }
    },
    'sim-nas': function (el) {
      var prev = S.nas.state, next = el.value, t = nowLabel();
      if (prev === next) return;
      if (prev !== 'ok') {
        D.recovered.unshift([NAS_COND[prev], 'semua', '30 Okt ' + S.nas.since, '30 Okt ' + t, 'NAS']);
        S.notif.unshift({ at: t, msg: 'Pulih: ' + NAS_COND[prev], tries: 1, ok: !S.notifDown });
      }
      if (next !== 'ok') S.notif.unshift({ at: t, msg: NAS_COND[next], tries: 1, ok: !S.notifDown });
      S.nas = { state: next, since: t };
      toast(next === 'ok' ? 'NAS kembali normal' : NAS_COND[next]);
      render();
    },
    'sim-state': function (el) { applySim(el.value); render(); },
    'sim-runner': function (el) { applyRunnerSim(el.value); render(); },
    'sim-ai': function (el) { S.aiDown = el.value === 'down'; render(); },
    'sim-conf': function (el) { S.confRO = el.value === 'ro'; render(); },
    'sim-login': function (el) { S.noLogin = el.value === 'off'; render(); },
    'alert-f': function (el) { S.alertF[el.getAttribute('data-k')] = el.value; render(); },
    'tl-range': function (el) { S.tlRange = +el.value; render(); },
    'h-range': function (el) { S.hRange = +el.value; render(); },
    integ: function (el) {
      var it = S.integrity[+el.getAttribute('data-i')];
      it.reviewed = el.checked ? '30 Okt · admin' : null;
      if (it.key === 'sum-siak') S.resolved['sum-siak'] = el.checked;
      render();
    }
  };

  // ---------- Tema ----------
  // Nilai awal dipasang skrip di <head> agar tidak berkedip; pilihan pengguna disimpan, tanpa pilihan ikut sistem.
  function setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    var b = document.getElementById('theme-btn'), label = t === 'dark' ? 'Ganti ke tema terang' : 'Ganti ke tema gelap';
    b.setAttribute('aria-label', label);
    b.title = label;
  }
  setTheme(document.documentElement.getAttribute('data-theme') || 'light');
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function (e) {
      var saved = null;
      try { saved = localStorage.getItem('lumbung-theme'); } catch (err) { /* ikut sistem */ }
      if (!saved) setTheme(e.matches ? 'dark' : 'light');
    });
  }

  // ---------- Event ----------
  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-act]');
    if (el && ACT[el.getAttribute('data-act')]) {
      if (el.tagName === 'A') e.preventDefault();
      ACT[el.getAttribute('data-act')](el);
      return;
    }
    if (e.target.closest('a[href="#"]')) { e.preventDefault(); }
    document.querySelectorAll('details.row-menu[open]').forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute('open'); });
  });
  document.addEventListener('input', function (e) {
    var k = e.target.getAttribute && e.target.getAttribute('data-in');
    if (k && INPUT[k] && (e.target.type === 'text' || e.target.type === 'search')) INPUT[k](e.target);
  });
  document.addEventListener('change', function (e) {
    var k = e.target.getAttribute && e.target.getAttribute('data-in');
    if (k && INPUT[k] && e.target.type !== 'text' && e.target.type !== 'search') INPUT[k](e.target);
  });
  document.addEventListener('keydown', function (e) {
    var open = !document.getElementById('modal-back').hidden;
    if (e.key === 'Escape' && open) closeModal();
    if (e.key === 'Escape') document.querySelectorAll('details.row-menu[open]').forEach(function (d) { d.removeAttribute('open'); });
    if (e.key === 'Tab' && open) {
      var f = document.querySelectorAll('.modal a[href], .modal button:not([disabled]), .modal select, .modal input');
      if (f.length && e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (f.length && !e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
    if (e.key === 'Enter' && e.target.id === 'wiz-name') ACT['wiz-create']();
    if (e.key === 'Enter' && e.target.id === 'rw-name') ACT['rw-create']();
  });
  document.getElementById('modal-back').addEventListener('click', function (e) { if (e.target.id === 'modal-back') closeModal(); });
  window.addEventListener('hashchange', render);
  // Token tampil sekali: ingatkan sebelum halaman ditutup atau dimuat ulang.
  window.addEventListener('beforeunload', function (e) { if (hasOpenToken()) e.preventDefault(); });

  // Jam simulasi + penyegaran 60 detik (hanya label; state sudah langsung)
  function tick() {
    S.clock = new Date(S.clock.getTime() + 1000);
    S.countdown--;
    if (S.countdown <= 0) { S.countdown = 60; S.lastRefresh = new Date(S.clock.getTime()); }
    var r = route();
    // Batas waktu Restore manual berjalan mengikuti jam, juga saat halamannya sedang tidak dibuka.
    var expired = S.rm.filter(function (x) { return rmActive(x) && x.expires && S.clock.getTime() >= x.expires; });
    expired.forEach(rmExpire);
    if (expired.length) rerender();
    document.querySelectorAll('.rm-left').forEach(function (el) { var x = rmFind(+el.getAttribute('data-id')); if (x && rmActive(x)) el.textContent = rmLeft(x); });
    // Wizard tidak ikut disegarkan supaya token yang tampil sekali tidak hilang.
    var paused = ((r[0] === 'container' || r[0] === 'runner') && r[1] === 'tambah') || (r[0] === 'container' && r[2] === 'manual' && (r[3] === 'baru' || S.rm.some(function (x) { return x.token; })));
    document.getElementById('refresh').innerHTML = paused ? 'Penyegaran dijeda' : '<span class="refresh-word">Disegarkan </span>' + hhmm(S.lastRefresh) + ' WIB · ' + S.countdown + ' dtk';
  }
  setInterval(tick, 1000);
  tick();
  render();
})();
