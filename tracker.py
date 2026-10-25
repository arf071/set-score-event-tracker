import cv2, sys, time, threading, serial, serial.tools.list_ports
from collections import deque

# ---------------- settings ----------------
USE_SERIAL = True
CAM_INDEX = 0
W, H = 960, 540         # requested; the real size is read back below
PROC_SCALE = 0.5          # detect on a half-size copy (much faster)
EXPOSURE = -6             # shorter exposure = less blur. Try -5 if too dark, -7 if still blurry. None = leave auto
MIN_AREA = 50             # in processed (scaled) pixels
CLOSE_K = 11
GROUND_FRAMES = 2         # frames on the floor before it counts
REARM_MARGIN = 60         # ball must rise this many px before the next point
COOLDOWN = 1.5

YEL_LO, YEL_HI = (20, 100, 100), (35, 255, 255)
BLU_LO, BLU_HI = (95, 100, 50), (125, 255, 255)
# ------------------------------------------

def find_port():
    for p in serial.tools.list_ports.comports():
        if p.device.startswith(("/dev/cu.usbmodem", "/dev/cu.usbserial")) \
           or p.device.upper().startswith("COM"):
            return p.device
    return None

class Camera:
    """Reads frames in a background thread so the main loop always gets the newest one."""
    def __init__(self):
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_DSHOW
        self.cap = cv2.VideoCapture(CAM_INDEX, backend)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(3, W); self.cap.set(4, H)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        if EXPOSURE is not None:
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)   # 0.25 = manual on DirectShow
            self.cap.set(cv2.CAP_PROP_EXPOSURE, EXPOSURE)
        self.frame, self.id = None, 0
        self.lock = threading.Lock()
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.running:
            ok, f = self.cap.read()
            if ok:
                with self.lock:
                    self.frame, self.id = f, self.id + 1

    def read(self):
        with self.lock:
            return self.id, self.frame

    def release(self):
        self.running = False
        time.sleep(0.1)
        self.cap.release()

ser = None
if USE_SERIAL:
    port = find_port()
    if not port:
        sys.exit("No Arduino found. Check USB cable and close the Serial Monitor.")
    print("Using", port)
    ser = serial.Serial(port, 115200, timeout=0)
    time.sleep(2)

cam = Camera()
if not cam.cap.isOpened():
    sys.exit("Camera failed. Try CAM_INDEX = 1 and close other apps using it.")
t0 = time.time()
while cam.read()[1] is None and time.time() - t0 < 5:
    time.sleep(0.01)
if cam.read()[1] is None:
    sys.exit("Camera opened but no frames arrived.")

H, W = cam.read()[1].shape[:2]
print("Resolution:", W, "x", H)
NET_X = W // 2             # adjust live with A / D keys
FLOOR_Y = int(H * 0.9)     # adjust live with W / S keys

kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (CLOSE_K, CLOSE_K))
score = [0, 0]             # [Team A, Team B]
armed, ground_count, last_t = True, 0, 0
trail = deque(maxlen=12)
last_id, t_prev, fps = 0, time.time(), 0.0

while True:
    fid, raw = cam.read()
    if raw is None or fid == last_id:      # no new frame yet
        time.sleep(0.002)
        continue
    last_id = fid
    now = time.time()
    fps = 0.9 * fps + 0.1 * (1 / max(now - t_prev, 1e-3))
    t_prev = now

    frame = cv2.flip(raw, 1)
    small = cv2.resize(frame, None, fx=PROC_SCALE, fy=PROC_SCALE, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2HSV)

    mask = cv2.inRange(hsv, YEL_LO, YEL_HI) | cv2.inRange(hsv, BLU_LO, BLU_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.dilate(cv2.erode(mask, None, 1), None, 1)

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    ball = None
    if cnts:
        c = max(cnts, key=cv2.contourArea)
        if cv2.contourArea(c) > MIN_AREA:
            (bx, by), br = cv2.minEnclosingCircle(c)
            ball = (bx / PROC_SCALE, by / PROC_SCALE, br / PROC_SCALE)   # back to full-size pixels

    if ball:
        x, y, r = ball
        trail.append((int(x), int(y)))
        cv2.circle(frame, (int(x), int(y)), int(r), (0, 255, 0), 2)

        bottom = y + r
        ground_count = ground_count + 1 if bottom >= FLOOR_Y else 0

        if armed and ground_count >= GROUND_FRAMES and now - last_t > COOLDOWN:
            team = 1 if x < NET_X else 0     # landed left -> Team B scores, right -> Team A
            score[team] += 1
            last_t, armed = now, False
            print("Point:", "AB"[team], score)
            if ser:
                ser.write(f"S,{score[0]},{score[1]},{team}\n".encode())

        if not armed and bottom < FLOOR_Y - REARM_MARGIN:
            armed = True
    else:
        ground_count = 0
        trail.clear()

    for i in range(1, len(trail)):
        cv2.line(frame, trail[i - 1], trail[i], (255, 0, 255), 2)

    cv2.line(frame, (NET_X, 0), (NET_X, H), (255, 255, 255), 2)       # net
    cv2.line(frame, (0, FLOOR_Y), (W, FLOOR_Y), (0, 0, 255), 2)       # floor
    cv2.putText(frame, f"Team A: {score[0]}  -  Team B: {score[1]}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
    cv2.putText(frame, f"{fps:.0f} fps", (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    cv2.imshow("Tracker", frame)
    cv2.imshow("Mask", mask)

    k = cv2.waitKey(1) & 0xFF
    if k == ord('q'): break
    elif k == ord('r'):
        score, armed = [0, 0], True
        if ser: ser.write(b"R\n")
    elif k == ord('a'): NET_X -= 5
    elif k == ord('d'): NET_X += 5
    elif k == ord('w'): FLOOR_Y -= 5
    elif k == ord('s'): FLOOR_Y += 5

cam.release()
if ser: ser.close()
cv2.destroyAllWindows()