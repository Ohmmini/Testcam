import cv2
import threading
import time
import json
import os
from flask import Flask, Response, render_template, redirect, url_for, request

app = Flask(__name__)
CAMERA_FILE = "cameras.json"
camera_streams = {}

# โหลด/บันทึกกล้อง
def load_cameras():
    if not os.path.exists(CAMERA_FILE):
        return []
    with open(CAMERA_FILE, 'r') as f:
        return json.load(f)

def save_cameras(cameras):
    with open(CAMERA_FILE, 'w') as f:
        json.dump(cameras, f, indent=2)

# ตรวจสอบสถานะกล้อง (ออนไลน์/ออฟไลน์)
def is_camera_online(rtsp_url, timeout=2):
    cap = cv2.VideoCapture(rtsp_url)
    start_time = time.time()
    while time.time() - start_time < timeout:
        success, frame = cap.read()
        if success and frame is not None:
            cap.release()
            return True
    cap.release()
    return False

# Stream กล้องแบบ background thread
class CameraStream:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.cap = None
        self.frame = None
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self.update_frame, daemon=True)
        self.thread.start()

    def open_stream(self):
        if self.cap:
            self.cap.release()
        self.cap = cv2.VideoCapture(self.rtsp_url)

    def update_frame(self):
        self.open_stream()
        while self.running:
            if not self.cap.isOpened():
                print(f"[WARN] Stream closed, reconnecting to {self.rtsp_url}")
                time.sleep(2)
                self.open_stream()
                continue

            success, frame = self.cap.read()
            if success and frame is not None:
                with self.lock:
                    self.frame = frame
            else:
                print(f"[WARN] Frame read failed, reconnecting to {self.rtsp_url}")
                time.sleep(1)
                self.open_stream()

    def get_frame(self):
        with self.lock:
            return self.frame

    def stop(self):
        self.running = False
        self.thread.join()
        if self.cap:
            self.cap.release()

# ดึง stream กล้องตาม ID
def get_camera_stream(camera_id, rtsp_url):
    if camera_id not in camera_streams:
        camera_streams[camera_id] = CameraStream(rtsp_url)
    return camera_streams[camera_id]

# Generator สำหรับ streaming
def generate_frames(camera_id, rtsp_url, fps=25):
    stream = get_camera_stream(camera_id, rtsp_url)
    interval = 1.0 / fps
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 65]  # ลด bandwidth

    while True:
        frame = stream.get_frame()
        if frame is None:
            time.sleep(0.1)
            continue

        ret, buffer = cv2.imencode('.jpg', frame, encode_param)
        if not ret:
            time.sleep(0.05)
            continue

        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

        time.sleep(interval)

# ------------------ ROUTES ------------------

@app.route('/')
def opening():
    return render_template('opening.html')

@app.route('/Living')
def Living():
    return render_template('Living.html')


@app.route('/camera', methods=["GET", "POST"])
def camera():
    cameras = load_cameras()
    if request.method == "POST":
        rtsp_url = request.form.get("rtsp_url")
        name = request.form.get("name") or f"Camera {len(cameras)+1}"
        latitude = request.form.get("latitude")
        longitude = request.form.get("longitude")

        if rtsp_url:
            new_id = max([c["id"] for c in cameras], default=0) + 1
            try:
                lat = float(latitude)
                lng = float(longitude)
            except (TypeError, ValueError):
                lat = None
                lng = None

            cameras.append({
                "id": new_id,
                "rtsp_url": rtsp_url,
                "name": name,
                "latitude": lat,
                "longitude": lng
            })
            save_cameras(cameras)

        return redirect(url_for('camera'))

    # เพิ่มสถานะให้แต่ละกล้อง
    for cam in cameras:
        cam["status"] = "ออนไลน์" if is_camera_online(cam["rtsp_url"]) else "ออฟไลน์"

    return render_template("camera.html", cameras=cameras)

@app.route('/delete_camera/<int:camera_id>', methods=["POST"])
def delete_camera(camera_id):
    cameras = load_cameras()
    cameras = [c for c in cameras if c["id"] != camera_id]
    save_cameras(cameras)

    if camera_id in camera_streams:
        camera_streams[camera_id].stop()
        del camera_streams[camera_id]

    return redirect(url_for('camera'))

@app.route('/video/<int:camera_id>')
def video(camera_id):
    cameras = load_cameras()
    cam = next((c for c in cameras if c["id"] == camera_id), None)
    if not cam:
        return "Camera not found", 404
    return Response(generate_frames(camera_id, cam["rtsp_url"], fps=25),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

