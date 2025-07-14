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
    Response, jsonify, flash,session
)
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from dotenv import load_dotenv
from werkzeug.utils import secure_filename


load_dotenv()

app = Flask(__name__)
app.secret_key = 'a0888150287'

# Config
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB
UPLOAD_FOLDER = os.path.join('static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# สร้างโฟลเดอร์ถ้ายังไม่มี
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov', 'avi', 'pdf'}


# MongoDB setup
MONGO_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)
db = client['your_database_name']  # เปลี่ยนชื่อ DB ตามจริง
uploads_collection = db['uploads']
cameras_collection = db['cameras']  # คอลเลกชันเก็บกล้อง
complaints_collection = db['complaints']
posts_collection = db['posts']  # ✅ เพิ่มบรรทัดนี้





def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


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


# สร้างตัวแปร global cache สำหรับเก็บสถานะกล้อง (True = online, False = offline)
camera_status_cache = {}

# ฟังก์ชัน background thread เช็คสถานะกล้องเป็นระยะ
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

# เรียก start thread ตอนเริ่ม app
status_thread = threading.Thread(target=check_camera_status_periodically, daemon=True)
status_thread.start()

# --- Flask routes ---
@app.route('/')
def opening():
    if 'user' not in session:
        return redirect(url_for('login'))

    num_food = uploads_collection.count_documents({"category": "ร้านอาหาร"})
    num_tourism = uploads_collection.count_documents({"category": "สถานที่ท่องเที่ยว"})
    cameras = load_cameras()
    num_cameras = len(cameras)

    return render_template('opening.html',
                           num_food=num_food,
                           num_tourism=num_tourism,
                           num_cameras=num_cameras)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == '1234':
            session['user'] = username
            return redirect(url_for('opening'))  # ไปหน้า /
        else:
            flash('ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('ออกจากระบบแล้ว', 'info')
    return redirect(url_for('login'))


@app.route('/complaints', methods=['GET', 'POST'])
def complaints():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        uploader = request.form.get('uploader')

        media_urls = []
        files = request.files.getlist('files[]')  # ต้องเป็น files[] ตามฟอร์ม

        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # เพิ่ม timestamp หรือ unique id เพื่อป้องกันชื่อไฟล์ซ้ำ
                unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{filename}"
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
                file.save(save_path)

                # สร้าง URL สำหรับแสดงในเว็บ
                media_url = url_for('static', filename=f'uploads/{unique_name}')
                media_urls.append(media_url)

        # สร้างข้อมูลโพสต์
        post_data = {
            'title': title,
            'content': content,
            'uploader': uploader,
            'media_urls': media_urls,
            'created_at': datetime.now()
        }

        # สมมติ posts_collection เป็น MongoDB collection ของคุณ
        posts_collection.insert_one(post_data)

        return redirect('/complaints')

    # ถ้าเป็น GET ดึงโพสต์มาแสดง
    posts = list(posts_collection.find().sort('created_at', -1))
    return render_template('complaints.html', posts=posts)




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

    # ใช้สถานะจาก cache แทนเช็คจริงทุกครั้ง
    for cam in cameras:
        cam_id = cam.get('id')
        if cam.get('rtsp_url'):
            cam['status'] = "ออนไลน์" if camera_status_cache.get(cam_id, False) else "ออฟไลน์"
        else:
            cam['status'] = "ไม่มี URL"

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