from flask import Blueprint, jsonify, render_template
import psutil, shutil
from glob import glob
import os

performance_bp = Blueprint('performance_bp', __name__)

@performance_bp.route("/performance")
def get_performance():
    print(">>> /performance route called <<<")

    cpu = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent

    # ขนาดพื้นที่ในเครื่องจริง
    total, used, free = shutil.disk_usage('/')
    free_gb = free / (1024**3)

    # หาไฟล์วิดีโอทั้งหมดใน static/recordings
    video_files = glob('static/recordings/*.mp4')
    total_size_mb = 0

    for f in video_files:
        try:
            total_size_mb += os.path.getsize(f) / (1024 * 1024)
        except:
            continue

    file_count = len(video_files)
    avg_size_mb = total_size_mb / file_count if file_count > 0 else 0

    # สมมุติว่าบันทึกทุก 1 ชม. เป็นเวลา 15 วัน = 24*15 ไฟล์
    expected_files_per_cam = 24 * 15
    usage_15days_gb = (avg_size_mb * expected_files_per_cam) / 1024 if avg_size_mb > 0 else 0

    max_cameras = int(free_gb / usage_15days_gb) if usage_15days_gb > 0 else 0

    return jsonify({
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "free_gb": round(free_gb, 2),
        "avg_file_size_mb": round(avg_size_mb, 2),
        "expected_gb_per_camera_15d": round(usage_15days_gb, 2),
        "max_camera_supported": max_cameras
    })

@performance_bp.route("/performance_view")
def performance_view():
    return render_template("performance.html")
