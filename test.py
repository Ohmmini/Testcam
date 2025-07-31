import os
import sys
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
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import subprocess

from checkperformances import performance_bp  # ✅ import blueprint


load_dotenv()




# ตรวจว่าอยู่ใน onefile หรือไม่
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.abspath(".")

app = Flask(
    __name__,
    template_folder=os.path.join(base_path, 'templates'),
    static_folder=os.path.join(base_path, 'static')
)

app.register_blueprint(performance_bp, url_prefix="/")

latest_data = {
    "name": "ยังไม่มีข้อมูล",
    "description": "กำลังรอการอัปโหลด..."
}



def fetch_latest_upload():
    global latest_data
    try:
        latest_upload = uploads_collection.find_one({}, sort=[('_id', -1)])
        if latest_upload:
            latest_data['name'] = latest_upload.get('name', 'ไม่พบข้อมูล')
            latest_data['description'] = latest_upload.get('description', 'ไม่พบข้อมูล')
        else:
            latest_data['name'] = "ไม่พบข้อมูล"
            latest_data['description'] = "ไม่มีรายการอัปโหลดล่าสุด"
    except Exception as e:
        print(f"[ERROR] fetch_latest_upload: {e}")
        latest_data['name'] = "เกิดข้อผิดพลาด"
        latest_data['description'] = str(e)


# เริ่ม scheduler ดึงข้อมูลทุก 30 วินาที
scheduler = BackgroundScheduler()
scheduler.add_job(func=fetch_latest_upload, trigger="interval", seconds=30)
scheduler.start()

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



RECORDINGS_DIR = os.path.join(os.getcwd(), 'static', 'recordings')  # หรือ path ที่คุณใช้จริง

def record_rtsp_stream(rtsp_url, camera_name, duration=3600):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"{camera_name}_{timestamp}.mp4"
    output_path = os.path.join(RECORDINGS_DIR, filename)

    try:
        print(f"[INFO] เริ่มบันทึก {filename} ({rtsp_url})")

        command = [
            'ffmpeg',
            '-rtsp_transport', 'tcp',
            '-i', rtsp_url,
            '-t', str(duration),
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-pix_fmt', 'yuv420p',
            '-an',
            '-y',
            output_path
        ]

        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[INFO] บันทึกสำเร็จ: {filename}")
    except Exception as e:
        print(f"[ERROR] การบันทึกล้มเหลว: {e}")




RECORD_DIR = os.path.join('static', 'recordings')
os.makedirs(RECORD_DIR, exist_ok=True)


def record_all_cameras():
    cameras = list(cameras_collection.find())
    for cam in cameras:
        rtsp_url = cam.get('rtsp_url')
        name = cam.get('name', f"camera_{cam.get('id')}")  # แค่ชื่อกล้อง
        if rtsp_url:
            threading.Thread(
                target=record_rtsp_stream,
                args=(rtsp_url, name, 3600),  # <-- ส่งชื่อกล้องแทน path
                daemon=True
            ).start()

# เพิ่ม Job ให้ scheduler ทำซ้ำทุก 60 นาที
scheduler.add_job(record_all_cameras, trigger="interval", minutes=60)




def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS






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
        files = []
        for key in ['file1', 'file2', 'file3']:
            file = request.files.get(key)
            if file and allowed_file(file.filename):
                files.append(file)
            else:
                files.append(None)

        uploader = request.form.get('uploader', '').strip() or None
        name = request.form.get('name', '').strip()
        category = request.form.get('category', '').strip()
        description = request.form.get('description', '').strip()

        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')
        phone = request.form.get('phone')

        try:
            lat = float(latitude) if latitude else None
            lng = float(longitude) if longitude else None
        except ValueError:
            lat = None
            lng = None

        media_urls = []
        for file in files:
            if file:
                original_name = secure_filename(file.filename)
                ext = original_name.rsplit('.', 1)[1].lower()
                filename = f"{uuid.uuid4().hex}.{ext}"

                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

                media_url = url_for('static', filename=f'uploads/{filename}')
                media_urls.append(media_url)

        uploads_collection.insert_one({
            "filename": media_urls[0] if media_urls else None,
            "original_name": files[0].filename if files[0] else None,
            "media_urls": media_urls,
            "uploader": uploader,
            "upload_time": datetime.now(timezone.utc),
            "name": name,
            "category": category,
            "description": description,
            "latitude": lat,
            "longitude": lng,
            "phone": phone
        })

        return redirect(url_for('peopleShareing'))

    # ดึงข้อมูลทั้งหมด
    profiles = list(uploads_collection.find().sort('upload_time', -1))
    # ตรวจสอบ media_urls ในแต่ละ profile
    for p in profiles:
        if not p.get('media_urls'):
            p['media_urls'] = []

    # ดึงข้อมูลล่าสุด 1 รายการ
    latest_profile = uploads_collection.find_one(sort=[('upload_time', -1)])
    if latest_profile and not latest_profile.get('media_urls'):
        latest_profile['media_urls'] = []

    num_shops = uploads_collection.count_documents({"category": "ร้านค้า"})
    num_tourism = uploads_collection.count_documents({"category": "สถานที่ท่องเที่ยว"})

    return render_template(
        "peopleShareing.html",
        profiles=profiles,
        latest_profile=latest_profile,
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
def root():
    return redirect(url_for('intro'))

@app.route('/intro')
def intro():
    return render_template('intro.html')

@app.route('/opening')
def opening():
    # นับจำนวนเอกสารในแต่ละ category และ posts
    num_food = uploads_collection.count_documents({"category": "ร้านอาหาร"})
    num_tourism = uploads_collection.count_documents({"category": "สถานที่ท่องเที่ยว"})
    num_posts = posts_collection.count_documents({})

    # โหลดกล้อง และนับจำนวน
    cameras = load_cameras()
    num_cameras = len(cameras)

    num_online = 0
    num_offline = 0

    # ตรวจสอบสถานะกล้องจาก cache
    for cam in cameras:
        cam_id = cam.get('id')
        is_online = camera_status_cache.get(cam_id, False)
        cam['status'] = "ออนไลน์" if is_online else "ออฟไลน์"

        if is_online:
            num_online += 1
        else:
            num_offline += 1

    # ดึงข้อมูล upload ล่าสุด
    latest_upload = uploads_collection.find_one(sort=[('upload_time', -1)])

    if latest_upload:
        latest_name = latest_upload.get('name', 'ไม่พบข้อมูล')
        latest_description = latest_upload.get('description', 'ไม่พบข้อมูล')

        media_urls = latest_upload.get('media_urls', [])
        if media_urls and len(media_urls) > 0:
            latest_media_url = media_urls[0]

            # ถ้า media_url ไม่มี /static/ นำหน้า ให้เติมให้
            if not latest_media_url.startswith('/static/'):
                latest_media_url = '/static/' + latest_media_url.lstrip('/')
        else:
            latest_media_url = ''
    else:
        latest_name = 'ไม่พบข้อมูล'
        latest_description = 'ไม่พบข้อมูล'
        latest_media_url = ''

    # ส่งข้อมูลทั้งหมดไปยังเทมเพลต
    return render_template(
        'opening.html',
        num_food=num_food,
        num_tourism=num_tourism,
        num_cameras=num_cameras,
        num_online=num_online,
        num_offline=num_offline,
        latest_name=latest_name,
        latest_description=latest_description,
        latest_media_url=latest_media_url,
        cameras=cameras,
        num_posts=num_posts
    )




@app.route('/complaints', methods=['GET', 'POST'])
def complaints():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        uploader = request.form.get('uploader')

        media_urls = []
        for key in ['file1', 'file2', 'file3']:
            file = request.files.get(key)
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{filename}"
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
                file.save(save_path)

                media_url = url_for('static', filename=f'uploads/{unique_name}')
                media_urls.append(media_url)

        post_data = {
            'title': title,
            'content': content,
            'uploader': uploader,
            'media_urls': media_urls,
            'created_at': datetime.now()
        }
        posts_collection.insert_one(post_data)

        return redirect('/complaints')

    # GET: แสดงโพสต์ทั้งหมด
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

from flask import request, jsonify
from bson import ObjectId

@app.route('/api/posts/vote', methods=['POST'])
def vote_post():
    data = request.get_json()
    post_id = data.get('post_id')
    vote_type = data.get('vote_type')

    if not post_id or vote_type not in ('like', 'dislike'):
        return jsonify({'success': False, 'message': 'ข้อมูลไม่ครบหรือไม่ถูกต้อง'}), 400

    try:
        oid = ObjectId(post_id)
    except Exception:
        return jsonify({'success': False, 'message': 'post_id ไม่ถูกต้อง'}), 400

    update_field = 'likes' if vote_type == 'like' else 'dislikes'

    result = posts_collection.find_one_and_update(
        {'_id': oid},
        {'$inc': {update_field: 1}},
        return_document=True  # คืน document หลังอัพเดต
    )

    if not result:
        return jsonify({'success': False, 'message': 'ไม่พบโพสต์นี้'}), 404

    likes = result.get('likes', 0)
    dislikes = result.get('dislikes', 0)

    return jsonify({'success': True, 'likes': likes, 'dislikes': dislikes})



API_KEY = "ee85efef-c46c-4b37-8ec3-18111ce94cb7"


@app.route('/api/maehongson_stations')
def get_maehongson_stations():
    global cache_data, cache_expire
    now = datetime.utcnow()

    # ถ้ายังไม่หมดอายุ ให้คืน cache เดิม
    if cache_data and now < cache_expire:
        return jsonify(cache_data)

    state = "Mae Hong Son"
    country = "Thailand"
    API_KEY = "ee85efef-c46c-4b37-8ec3-18111ce94cb7"

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
                        "city": d.get("city", "N/A"),
                        "state": d.get("state", "N/A"),
                        "country": d.get("country", "N/A"),
                        "aqius": d.get("current", {}).get("pollution", {}).get("aqius"),
                        "main_pollutant": d.get("current", {}).get("pollution", {}).get("mainus"),
                        "temperature": d.get("current", {}).get("weather", {}).get("tp"),
                        "humidity": d.get("current", {}).get("weather", {}).get("hu"),
                        "wind_speed": d.get("current", {}).get("weather", {}).get("ws")
                    })
                else:
                    print(f"[WARN] Failed to get AQI for city {city}: {city_data}")
            except Exception as e:
                print(f"[ERROR] Exception fetching AQI for city {city}: {e}")

        response_data = {
            "state": state,
            "country": country,
            "stations": stations_data
        }

        # เก็บ cache ไว้ 10 นาที
        cache_data = response_data
        cache_expire = now + timedelta(minutes=10)

        return jsonify(response_data)

    except requests.RequestException as e:
        return jsonify({"error": "Failed to fetch cities or stations", "details": str(e)}), 500


@app.route('/videorecording')
def recordings_page():
    base_dir = os.path.join('static', 'recordings')
    if not os.path.exists(base_dir):
        return "ไม่พบโฟลเดอร์ recordings", 404

    video_files = [f for f in os.listdir(base_dir) if f.endswith('.mp4')]

    recordings = []
    all_dates = set()
    all_times = set()
    all_locations = set()

    for file in sorted(video_files, reverse=True):
        try:
            name_part = file.replace(".mp4", "")
            location, datetime_part = name_part.split("_", 1)
            date_str, time_str = datetime_part.split("_")

            time_str_formatted = time_str.replace("-", ":")  # จาก 02-11 เป็น 02:11

            # รวบรวมตัวเลือก
            all_dates.add(date_str)
            all_times.add(time_str_formatted)
            all_locations.add(location)

            recordings.append({
                'filename': file,
                'location': location,
                'date': date_str,
                'time': time_str_formatted,
                'datetime': f"{date_str} {time_str_formatted}",
                'url': f"/static/recordings/{file}"
            })
        except ValueError:
            continue

      # ✅ วางตรงนี้!
    selected_location = request.args.get('location')
    selected_date = request.args.get('date')
    selected_time = request.args.get('time')

    if selected_location:
        recordings = [r for r in recordings if r['location'] == selected_location]
    if selected_date:
        recordings = [r for r in recordings if r['date'] == selected_date]
    if selected_time:
        recordings = [r for r in recordings if r['time'] == selected_time]

    return render_template(
        'videorecording.html',
        recordings=recordings,
        all_dates=sorted(all_dates),
        all_times=sorted(all_times),
        all_locations=sorted(all_locations)
    )


# @app.route("/performance")
# def get_performance():
#     # ประสิทธิภาพระบบ
#     cpu = psutil.cpu_percent(interval=1)
#     memory = psutil.virtual_memory().percent
#     disk = psutil.disk_usage('/').percent

#     # Storage
#     total, used, free = shutil.disk_usage('/')
#     free_gb = free / (1024**3)

#     # สมมุติกล้องใช้พื้นที่ 100 MB ต่อ 5 นาที (บีบอัดแล้ว)
#     storage_per_camera_per_day_gb = (100 * 12 * 24) / 1024  # = ~28.12 GB

#     # กล้องที่รองรับได้ (โดยไม่เต็มดิสก์)
#     remaining_cameras = int(free_gb / storage_per_camera_per_day_gb)

#     return jsonify({
#         "cpu": cpu,
#         "memory": memory,
#         "disk": disk,
#         "free_gb": round(free_gb, 2),
#         "recommended_camera_count": remaining_cameras
#     })

# @app.route('/performance')
# def get_performance():
#     print(">>> /performance route called <<<")
    



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)