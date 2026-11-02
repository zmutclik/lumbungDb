"""Menggantikan app/nas/probe.py di uji: selama <data>/.sim-hang ada, proses tidak kembali, seperti pembaca mount NFS
`hard` saat NAS macet. Sesudah itu menjalankan pemeriksaan yang sama dengan probe asli."""
import json
import os
import sys
import time

data_dir, marker = sys.argv[1], sys.argv[2]
while os.path.exists(os.path.join(data_dir, ".sim-hang")):
    time.sleep(0.2)
result = {"mounted": True, "total": None, "free": None}
if marker and not os.path.exists(os.path.join(data_dir, marker)):
    result["mounted"] = False
else:
    stat = os.statvfs(data_dir)
    result["total"] = stat.f_blocks * stat.f_frsize
    result["free"] = stat.f_bavail * stat.f_frsize
print(json.dumps(result))
