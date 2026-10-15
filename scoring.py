"""Face "score" from photo proportions (MediaPipe Face Mesh + OpenCV).

This is a home-made metric (symmetry + a few classic proportions), NOT a real PSL.
Photos are decoded from bytes in memory only; nothing is written to disk.
"""
import asyncio

import cv2
import numpy as np

# ---- thresholds (tune on real photos) ----
MIN_SIDE = 200            # px, smallest allowed image side
MIN_BRIGHTNESS = 50       # mean gray of the face crop, 0..255
MAX_BRIGHTNESS = 215
MIN_SHARPNESS = 50.0      # variance of Laplacian on the 256x256 face crop
MIN_FACE_SHARE = 0.15     # face width / image width
MAX_TURN = 0.10           # allowed head turn (nose offset from the center)

# ---- score shape (tune on real photos) ----
SYM_TOL = 0.035           # asymmetry (share of face width) that drops symmetry to ~37%
SCORE_BASE = 2.0
SCORE_RANGE = 8.0         # score = BASE + RANGE * quality, clipped to 1..10

# FaceMesh landmark indices
TOP, CHIN = 10, 152
BROW_MID, NOSE_TIP, NOSE_BASE = 9, 1, 2
FACE_L, FACE_R = 234, 454
EYE_OUT_L, EYE_IN_L, EYE_IN_R, EYE_OUT_R = 33, 133, 362, 263
NOSTRIL_L, NOSTRIL_R = 98, 327
MOUTH_L, MOUTH_R = 61, 291
MIDLINE = [10, 9, 168, 1, 2, 0, 17, 152]
SYM_PAIRS = [(33, 263), (133, 362), (159, 386), (145, 374), (61, 291), (234, 454),
             (98, 327), (70, 300), (105, 334), (172, 397)]


class PhotoError(Exception):
    """Photo rejected. The text is shown to the user (Russian)."""


def _dist(p, a, b) -> float:
    return float(np.linalg.norm(p[a] - p[b]))


def _closeness(value: float, ideal: float, tol: float) -> float:
    return max(0.0, 1.0 - abs(value - ideal) / tol)


def _decode(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise PhotoError("Не получилось открыть фото. Отправь обычное фото (не файлом).")
    h, w = img.shape[:2]
    if min(h, w) < MIN_SIDE:
        raise PhotoError("Фото слишком маленькое. Нужно хотя бы 200 пикселей по короткой стороне.")
    return img


def _face_landmarks(img: np.ndarray) -> np.ndarray:
    """Returns 468x2 landmarks in pixels. Raises PhotoError if there is not exactly one face."""
    import mediapipe as mp  # imported lazily: heavy, and not needed for the pure math below

    h, w = img.shape[:2]
    with mp.solutions.face_mesh.FaceMesh(
        static_image_mode=True, max_num_faces=2,
        refine_landmarks=False, min_detection_confidence=0.6,
    ) as mesh:
        result = mesh.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    faces = result.multi_face_landmarks or []
    if not faces:
        gray_mean = float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean())
        if gray_mean < MIN_BRIGHTNESS:
            raise PhotoError("Фото слишком тёмное. Включи свет и сфотографируй лицо ещё раз.")
        raise PhotoError("Не вижу лица. Сфотографируй лицо крупно, анфас, без маски и очков.")
    if len(faces) > 1:
        raise PhotoError("На фото больше одного лица. Нужно фото только с тобой.")
    return np.array([[p.x * w, p.y * h] for p in faces[0].landmark], dtype=np.float64)


def check_face_quality(img: np.ndarray, pts: np.ndarray) -> None:
    """Brightness / contrast / blur / size of the face area."""
    h, w = img.shape[:2]
    x0, y0 = np.floor(pts.min(axis=0)).astype(int)
    x1, y1 = np.ceil(pts.max(axis=0)).astype(int)
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
    if x1 - x0 < 10 or y1 - y0 < 10:
        raise PhotoError("Лицо не видно целиком. Отойди чуть дальше и держи лицо в кадре.")
    if (x1 - x0) / w < MIN_FACE_SHARE:
        raise PhotoError("Лицо слишком маленькое в кадре. Сфотографируй ближе.")

    gray = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    if mean < MIN_BRIGHTNESS:
        raise PhotoError("Фото слишком тёмное. Включи свет и сфотографируй ещё раз.")
    if mean > MAX_BRIGHTNESS:
        raise PhotoError("Фото пересвечено. Уйди от яркого света и сфотографируй ещё раз.")

    sharp = cv2.Laplacian(cv2.resize(gray, (256, 256)), cv2.CV_64F).var()
    if sharp < MIN_SHARPNESS:
        raise PhotoError("Фото смазанное. Держи телефон ровно и сфотографируй ещё раз.")


def _level(pts: np.ndarray) -> np.ndarray:
    """Rotates the face so that its midline is vertical (removes head tilt)."""
    mid = pts[MIDLINE]
    centered = mid - mid.mean(axis=0)
    direction = np.linalg.svd(centered)[2][0]
    if direction[1] < 0:  # make it point from forehead to chin
        direction = -direction
    angle = np.arctan2(direction[0], direction[1])
    c, s = np.cos(angle), np.sin(angle)
    rot = np.array([[c, -s], [s, c]])
    return (pts - mid.mean(axis=0)) @ rot.T


def score_from_landmarks(pts: np.ndarray) -> float:
    """Pure math: 468x2 landmarks -> score 1..10. Raises PhotoError if the head is turned."""
    p = _level(pts)
    width = _dist(p, FACE_L, FACE_R)
    if width < 1e-6:
        raise PhotoError("Не получилось разобрать лицо. Попробуй другое фото.")

    # head turn: the nose tip should sit in the middle between the face edges
    turn = abs((p[NOSE_TIP][0] - p[FACE_L][0]) / (p[FACE_R][0] - p[FACE_L][0]) - 0.5)
    if turn > MAX_TURN:
        raise PhotoError("Голова повёрнута. Смотри прямо в камеру.")

    # symmetry: mirrored landmarks must be equally far from the midline and at the same height
    asym = []
    for a, b in SYM_PAIRS:
        asym.append(abs(abs(p[a][0]) - abs(p[b][0])) / width)   # distance to midline
        asym.append(abs(p[a][1] - p[b][1]) / width)             # height difference
    symmetry = float(np.exp(-(np.mean(asym) / SYM_TOL) ** 2))

    # classic proportions: (value, ideal, tolerance)
    eye_w = (_dist(p, EYE_OUT_L, EYE_IN_L) + _dist(p, EYE_IN_R, EYE_OUT_R)) / 2
    inner = _dist(p, EYE_IN_L, EYE_IN_R)
    nose_w = _dist(p, NOSTRIL_L, NOSTRIL_R)
    mouth_w = _dist(p, MOUTH_L, MOUTH_R)
    mid_face = _dist(p, BROW_MID, NOSE_BASE)
    low_face = _dist(p, NOSE_BASE, CHIN)
    proportions = [
        _closeness(inner / eye_w, 1.0, 0.35),        # eyes are one eye-width apart
        _closeness(nose_w / inner, 1.0, 0.35),       # nose width ~ distance between eyes
        _closeness(mouth_w / nose_w, 1.5, 0.5),      # mouth ~ 1.5 nose widths
        _closeness(mid_face / low_face, 1.0, 0.3),   # middle and lower thirds are equal
    ]
    quality = 0.5 * symmetry + 0.5 * float(np.mean(proportions))
    return float(np.clip(SCORE_BASE + SCORE_RANGE * quality, 1.0, 10.0))


def score_photo(data: bytes) -> float:
    """Blocking. bytes -> score of one photo, or PhotoError."""
    img = _decode(data)
    pts = _face_landmarks(img)
    check_face_quality(img, pts)
    return score_from_landmarks(pts)


async def score_photo_async(data: bytes) -> float:
    return await asyncio.to_thread(score_photo, data)


def average_score(scores: list[float]) -> float:
    """Final score of a battle = mean of the photos, 1 decimal (what the players see)."""
    return round(sum(scores) / len(scores), 1)
