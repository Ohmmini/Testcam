import os
from dotenv import load_dotenv
import cv2
from flask import Flask, Response, render_template, redirect, url_for,render_template_string,request
from pyngrok import ngrok
import json

load_dotenv()

app = Flask(__name__)

CAMERA_FILE = "cameras.json"

# ✅ Load กล้องจากไฟล์
def load_cameras():
    if not os.path.exists(CAMERA_FILE):
        return []
    with open(CAMERA_FILE, 'r') as f:
        return json.load(f)

# ✅ Save กล้องลงไฟล์
def save_cameras(cameras):
    with open(CAMERA_FILE, 'w') as f:
        json.dump(cameras, f, indent=2)

# อ่านค่าจาก .env
rtsp_url = os.getenv("RTSP_URL")

def generate_frames(rtsp_url):
    cap = cv2.VideoCapture(rtsp_url)
    while True:
        success, frame = cap.read()
        if not success:
            break
        _, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/camera', methods=["GET", "POST"])
def camera():
    cameras = load_cameras()  # ✅ โหลดก่อนใช้งาน

    if request.method == "POST":
        rtsp_url = request.form.get("rtsp_url")
        name = request.form.get("name") or f"Camera {len(cameras)+1}"
        if rtsp_url:
            new_id = max([c["id"] for c in cameras], default=0) + 1
            cameras.append({"id": new_id, "rtsp_url": rtsp_url, "name": name})
            save_cameras(cameras)  # ✅ บันทึกลงไฟล์
        return redirect(url_for('camera'))

    return render_template("camera.html", cameras=cameras)

@app.route('/delete_camera/<int:camera_id>', methods=["POST"])
def delete_camera(camera_id):
    cameras = load_cameras()  # ✅ โหลดก่อน
    cameras = [c for c in cameras if c["id"] != camera_id]
    save_cameras(cameras)  # ✅ บันทึกหลังลบ
    return redirect(url_for('camera'))

@app.route('/video/<int:camera_id>')
def video(camera_id):
    cameras = load_cameras()
    cam = next((c for c in cameras if c["id"] == camera_id), None)
    if not cam:
        return "Camera not found", 404
    return Response(generate_frames(cam["rtsp_url"]),
                    mimetype='multipart/x-mixed-replace; boundary=frame')
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
