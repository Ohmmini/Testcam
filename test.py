import os
import time
import uuid
import threading
import requests
import cv2
import numpy as np
from datetime import datetime, timezone
from flask import (
    Flask, render_template, request, redirect, url_for,
    Response, jsonify, flash
)
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = 'a0888150287'

# Config
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov'}

# MongoDB setup
MONGO_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)
db = client['your_database_name']  # เปลี่ยนชื่อ DB ตามจริง
uploads_collection = db['uploads']
cameras_collection = db['cameras']  # คอลเลกชันเก็บกล้อง


RECORD_FOLDER = 'recordings'        # --- add (A1)
os.makedirs(RECORD_FOLDER, exist_ok=True)  # --- add (A2)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS




@app.route('/peopleShareing', methods=['GET', 'POST'])
def peopleShareing():
    if request.method == 'POST':
        file = request.files.get('file')
        uploader = request.form.get('uploader', '').strip() or None
        name = request.form.get('name', '').strip()
        category = request.form.get('category', '').strip()
        description = request.form.get('description', '').strip()

        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')

        try:
            lat = float(latitude) if latitude else None
            lng = float(longitude) if longitude else None
        except ValueError:
            lat = None
            lng = None

        if file and allowed_file(file.filename):
            original_name = secure_filename(file.filename)
            ext = original_name.rsplit('.', 1)[1].lower()
            filename = f"{uuid.uuid4().hex}.{ext}"

            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

            media_url = url_for('static', filename=f'uploads/{filename}')

            uploads_collection.insert_one({
                "filename": filename,
                "original_name": original_name,
                "media_url": media_url,
                "uploader": uploader,
                "upload_time": datetime.now(timezone.utc),
                "name": name,
                "category": category,
                "description": description,
                "latitude": lat,
                "longitude": lng
            })

            return redirect(url_for('peopleShareing'))

    profiles = list(uploads_collection.find().sort('upload_time', -1))
    num_shops = uploads_collection.count_documents({"category": "ร้านค้า"})
    num_tourism = uploads_collection.count_documents({"category": "สถานที่ท่องเที่ยว"})

    return render_template(
        "peopleShareing.html",
        profiles=profiles,
        num_shops=num_shops,
        num_tourism=num_tourism
    )


def test_rtsp_connection(rtsp_url):
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        return False
    ret, _ = cap.read()
    cap.release()
    return ret


def get_next_camera_id():
    last_camera = cameras_collection.find_one(sort=[("id", -1)])
    return (last_camera["id"] + 1) if last_camera else 1


@app.route('/add_camera', methods=['POST'])
def add_camera():
    name = request.form.get('name')
    rtsp_url = request.form.get('rtsp_url')
    latitude = request.form.get('latitude')
    longitude = request.form.get('longitude')

    try:
        lat = float(latitude) if latitude else None
        lng = float(longitude) if longitude else None
    except ValueError:
        lat = None
        lng = None

    if not rtsp_url:
        flash('❌ กรุณากรอก RTSP URL', 'error')
        return redirect(url_for('camera'))

    if test_rtsp_connection(rtsp_url):
        new_id = get_next_camera_id()
        cameras_collection.insert_one({
            'id': new_id,
            'name': name or f"Camera {new_id}",
            'rtsp_url': rtsp_url,
            'latitude': lat,
            'longitude': lng
        })
        flash('✅ เพิ่มกล้องสำเร็จ', 'success')
    else:
        flash('❌ ไม่สามารถเชื่อมต่อกล้องได้ (URL หรือรหัสผ่านผิด)', 'error')

    return redirect(url_for('camera'))


def is_camera_online(rtsp_url, timeout=2):
    cap = cv2.VideoCapture(rtsp_url)
    start = time.time()
    while time.time() - start < timeout:
        success, frame = cap.read()
        if success and frame is not None:
            cap.release()
            return True
    cap.release()
    return False


class CameraStream:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.cap = None
        self.frame = None
        self.lock = threading.Lock()
        self.running = True
        self.retry_count = 0
        self.max_retries = 5
        self.retry_delay = 5  # วินาที
        self.thread = threading.Thread(target=self.update_frame, daemon=True)
        self.thread.start()

    def open_stream(self):
        if self.cap:
            self.cap.release()
        self.cap = cv2.VideoCapture(self.rtsp_url)
        self.retry_count = 0

    def update_frame(self):
        self.open_stream()
        while self.running:
            if not self.cap.isOpened():
                print(f"[WARN] Stream closed. Reconnecting {self.rtsp_url}")
                self.retry_count += 1
                if self.retry_count > self.max_retries:
                    print(f"[ERROR] Max retries reached for {self.rtsp_url}. Stop trying.")
                    self.running = False
                    break
                time.sleep(self.retry_delay)
                self.open_stream()
                continue

            success, frame = self.cap.read()
            if success and frame is not None:
                self.retry_count = 0
                with self.lock:
                    self.frame = frame
            else:
                print(f"[WARN] Frame read failed. Reconnecting {self.rtsp_url}")
                self.retry_count += 1
                if self.retry_count > self.max_retries:
                    print(f"[ERROR] Max retries reached for {self.rtsp_url}. Stop trying.")
                    self.running = False
                    break
                time.sleep(self.retry_delay)
                self.open_stream()

    def get_frame(self):
        with self.lock:
            return self.frame

    def stop(self):
        self.running = False
        self.thread.join()
        if self.cap:
            self.cap.release()


camera_streams = {}


def get_camera_stream(camera_id, rtsp_url):
    if camera_id not in camera_streams:
        camera_streams[camera_id] = CameraStream(rtsp_url)
    return camera_streams[camera_id]


def generate_frames(camera_id, rtsp_url, fps=25):
    stream = get_camera_stream(camera_id, rtsp_url)
    interval = 1.0 / fps
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 65]

    while True:
        frame = stream.get_frame()
        if frame is None:
            # แสดง offline frame
            blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(blank_frame, 'OFFLINE', (50, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 4, cv2.LINE_AA)
            ret, buffer = cv2.imencode('.jpg', blank_frame, encode_param)
            if not ret:
                time.sleep(interval)
                continue
        else:
            ret, buffer = cv2.imencode('.jpg', frame, encode_param)
            if not ret:
                time.sleep(interval)
                continue

        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

        time.sleep(interval)


def load_cameras():
    cameras = list(cameras_collection.find().sort('id', 1))
    for cam in cameras:
        cam['_id'] = str(cam['_id'])  # แปลง ObjectId เป็น string สำหรับใช้งานใน template หรือ API
    return cameras
camera_status_cache = {}

def check_camera_status_periodically():
    while True:
        cameras = list(cameras_collection.find())
        for cam in cameras:
            rtsp_url = cam.get('rtsp_url')
            cam_id = cam.get('id')
            status = False
            if rtsp_url:
                status = is_camera_online(rtsp_url, timeout=1)  # timeout น้อยลง
            camera_status_cache[cam_id] = status
        time.sleep(20)  # เช็คทุก 20 วินาที

def record_camera_stream(camera_id, rtsp_url):   # --- add (B1)
    """
    อัดสตรีม RTSP เป็นไฟล์ MP4 แยกตาม ‘วัน-ชั่วโมง’
    """
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')

    while True:
        # สร้างชื่อไฟล์ใหม่ทุก 1 ชม.
        now = datetime.now()
        date_part = now.strftime("%Y-%m-%d")
        hour_part = now.strftime("%H")
        filename = f"{RECORD_FOLDER}/cam{camera_id}_{date_part}_{hour_part}.mp4"

        cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            print(f"[ERR] cam{camera_id}: open RTSP failed – retry in 10 s")
            cap.release()
            time.sleep(10)
            continue

        # อ่านความละเอียดจริงจากสตรีม
        width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)  or 640)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
        fps    = cap.get(cv2.CAP_PROP_FPS)
        if fps is None or fps <= 1:
            fps = 15                      # เซ็ต fps เริ่มต้นถ้าอ่านไม่ได้

        out = cv2.VideoWriter(filename, fourcc, fps, (width, height))
        start_ts = time.time()

        while time.time() - start_ts < 3600:  # **บันทึก 1 ชั่วโมง**
            ret, frame = cap.read()
            if not ret or frame is None:
                print(f"[WARN] cam{camera_id}: read frame fail – restart chunk")
                break
            out.write(frame)

        out.release()
        cap.release()
        print(f"[INFO] cam{camera_id}: saved {filename}")
        time.sleep(1)  # รอ 1 วินาทีก่อนเริ่มรอบใหม่


def start_recording_all_cameras():   # --- add (C1)
    # เรียกหลัง Mongo เชื่อมต่อเสร็จแล้ว
    cameras = list(cameras_collection.find())  # ใช้คอลเลกชันเดียวกับระบบหลัก
    for cam in cameras:
        cam_id   = cam.get('id')
        rtsp_url = cam.get('rtsp_url')
        if not cam_id or not rtsp_url:
            continue
        threading.Thread(
            target=record_camera_stream,
            args=(cam_id, rtsp_url),
            daemon=True
        ).start()
        print(f"[INFO] cam{cam_id}: recording thread started")

# เรียก start thread ตอนเริ่ม app
status_thread = threading.Thread(target=check_camera_status_periodically, daemon=True)
status_thread.start()
start_recording_all_cameras()        # --- add (D1)
# --- Flask routes ---
@app.route('/')
def opening():
    num_food = uploads_collection.count_documents({"category": "ร้านอาหาร"})
    num_tourism = uploads_collection.count_documents({"category": "สถานที่ท่องเที่ยว"})
    cameras = load_cameras()
    num_cameras = len(cameras)

    return render_template('opening.html',
                           num_food=num_food,
                           num_tourism=num_tourism,
                           num_cameras=num_cameras)


@app.route('/weather')
def weather():
    return render_template('weather.html')


@app.route('/Energy')
def Energy():
    return render_template('Energy.html')


@app.route('/Environment')
def Environment():
    return render_template('Environment.html')


@app.route('/Living')
def Living():
    cameras = load_cameras()
    for cam in cameras:
        cam_id = cam.get('id')
        cam['status'] = "ออนไลน์" if camera_status_cache.get(cam_id, False) else "ออฟไลน์"
    return render_template('Living.html', cameras=cameras)


@app.route('/camera', methods=['GET', 'POST'])
def camera():
    cameras = load_cameras()

    if request.method == 'POST':
        rtsp_url = request.form.get('rtsp_url')
        name = request.form.get('name') or f"Camera {len(cameras) + 1}"
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')

        if rtsp_url:
            try:
                lat = float(latitude) if latitude else None
                lng = float(longitude) if longitude else None
            except (TypeError, ValueError):
                lat, lng = None, None

            new_id = get_next_camera_id()
            cameras_collection.insert_one({
                'id': new_id,
                'rtsp_url': rtsp_url,
                'name': name,
                'latitude': lat,
                'longitude': lng
            })

        return redirect(url_for('camera'))

    # อัปเดตสถานะกล้อง
    for cam in cameras:
        cam['status'] = "ออนไลน์" if cam.get('rtsp_url') and is_camera_online(cam['rtsp_url']) else "ออฟไลน์" if cam.get('rtsp_url') else "ไม่มี URL"

    return render_template('camera.html', cameras=cameras)


@app.route('/delete_camera/<int:camera_id>', methods=['POST'])
def delete_camera(camera_id):
    cameras_collection.delete_one({'id': camera_id})

    if camera_id in camera_streams:
        camera_streams[camera_id].stop()
        del camera_streams[camera_id]

    return redirect(url_for('camera'))


@app.route('/video/<int:camera_id>')
def video(camera_id):
    cam = cameras_collection.find_one({'id': camera_id})
    if not cam or not cam.get('rtsp_url'):
        return "Camera not found or no RTSP URL", 404
    return Response(generate_frames(camera_id, cam['rtsp_url'], fps=25),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/api/cameras')
def get_cameras():
    cameras = load_cameras()
    return jsonify(cameras)


API_KEY = "ee85efef-c46c-4b37-8ec3-18111ce94cb7"


@app.route('/api/maehongson_stations')
def get_maehongson_stations():
    state = "Mae Hong Son"
    country = "Thailand"

    cities_url = "http://api.airvisual.com/v2/cities"
    params = {
        "state": state,
        "country": country,
        "key": API_KEY
    }

    try:
        cities_resp = requests.get(cities_url, params=params, timeout=10)
        cities_resp.raise_for_status()
        cities_data = cities_resp.json()

        if cities_data.get("status") != "success":
            return jsonify({"error": "Failed to get cities", "details": cities_data}), 500

        city_list = [c["city"] for c in cities_data.get("data", [])]
        stations_data = []

        for city in city_list:
            city_url = "http://api.airvisual.com/v2/city"
            city_params = {
                "city": city,
                "state": state,
                "country": country,
                "key": API_KEY
            }
            try:
                city_resp = requests.get(city_url, params=city_params, timeout=10)
                city_resp.raise_for_status()
                city_data = city_resp.json()
                if city_data.get("status") == "success":
                    d = city_data["data"]
                    stations_data.append({
                        "city": d["city"],
                        "state": d["state"],
                        "country": d["country"],
                        "aqius": d["current"]["pollution"]["aqius"],
                        "main_pollutant": d["current"]["pollution"]["mainus"],
                        "temperature": d["current"]["weather"]["tp"],
                        "humidity": d["current"]["weather"]["hu"],
                        "wind_speed": d["current"]["weather"]["ws"]
                    })
                else:
                    print(f"[WARN] Failed to get AQI for city {city}: {city_data}")
            except Exception as e:
                print(f"[ERROR] Exception fetching AQI for city {city}: {e}")

        return jsonify({
            "state": state,
            "country": country,
            "stations": stations_data
        })

    except Exception as e:
        return jsonify({"error": "Failed to fetch cities or stations", "details": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
