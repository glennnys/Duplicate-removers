import cv2
from PIL import Image
from PIL import ExifTags 
import os
from pathlib import Path
import subprocess
import json
import hashlib
from skimage.metrics import structural_similarity as ssim
import numpy as np
import imagehash


def image_hamming_distance(h1, h2):
        if h1 is None or h2 is None:
            return None
                
        return h1 - h2  

def video_hamming_distance(h1, h2):
        if len(h1) == 0 or len(h2) == 0:
            return None
        
        sum = 0
        for i in range(min(len(h1), len(h2))):
            sum += image_hamming_distance(h1[i], h2[i])
        return sum // min(len(h1), len(h2))

def zero_phash(hash_size=8):
    # phash default is 8x8 = 64 bits
    return imagehash.ImageHash(np.zeros((hash_size, hash_size), dtype=bool))

    
def get_image_hash(image_path):
    if not image_path or not os.path.exists(image_path) or os.path.getsize(image_path) == 0:
        return zero_phash()
        # GIF support: If the file is a GIF, hash the first frame only.
    try:
        if image_path.lower().endswith('.gif'):
            with Image.open(image_path) as img:
                img.seek(0)  # First frame
                img = img.convert('RGBA')
                try:
                    return imagehash.phash(img)
                except:
                    img.thumbnail((1000, 1000))
                    return imagehash.phash(img)

        image = oriented_image(image_path)
        try: 
            return imagehash.phash(image)
        except:
            image.thumbnail((1000, 1000))
            return imagehash.phash(image)
        
    except Exception as e:
        print(f"file {image_path} couldn't be hashed: {e}")
        return zero_phash()

def get_video_hashes(video_path, frame_interval=24, max_hashes=3):
    if not video_path or not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
        return [zero_phash() for _ in range(max_hashes)]
    
    try:
        rotation = get_video_rotation(video_path)
        cap = cv2.VideoCapture(video_path)
        video_hashes = []
        frame_count = 0
        hash_count = 0
        
        while cap.isOpened() and hash_count < max_hashes:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_count % frame_interval == 0:
                frame = rotate_frame(frame, rotation)

                pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            
                video_hashes.append(imagehash.phash(pil_img))
                hash_count += 1
            
            frame_count += 1

        if cap.isOpened():
            cap.release()
    
        # pad with zero-hashes if we didn't get enough
        while len(video_hashes) < max_hashes:
            video_hashes.append(zero_phash())

    except Exception as e:
        print(f"file {video_path} couldn't be hashed: {e}")
        video_hashes = [zero_phash() for _ in range(max_hashes)]

    return video_hashes

def advanced_compare_videos(path1:str, path2:str, threshold:float) -> bool:
    """
    Perform ssim between file1 at path1 and file2 at path2. threshold should be between 0 and 1
    returns (similarity >= threshold)
    """
    return True # to be implemented

def advanced_compare_images(path1: str, path2: str, threshold: float) -> bool:
    """
    Compare two images using ORB feature matching.
    threshold should be between 0 and 1 (fraction of good matches over keypoints).
    Returns (similarity >= threshold).
    """
    try:
        def load_image(path):
            # Try OpenCV first
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                return img

            # Fallback to Pillow
            try:
                pil_img = Image.open(path).convert("L")
                return np.array(pil_img)
            except Exception as e:
                raise ValueError(f"Could not load image {path}: {e}")

        img1 = load_image(path1)
        img2 = load_image(path2)

        # Initialize ORB detector
        orb = cv2.ORB_create(500)  # up to 500 features

        # Find keypoints and descriptors
        kp1, des1 = orb.detectAndCompute(img1, None)
        kp2, des2 = orb.detectAndCompute(img2, None)

        if des1 is None or des2 is None:
            return False  # no features to compare

        # Match descriptors using Hamming distance
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = bf.match(des1, des2)

        if not matches:
            return False

        # Similarity = fraction of matches relative to keypoints
        similarity = len(matches) / max(len(kp1), len(kp2))

        return similarity >= threshold

    except Exception as e:
        print(f"Unable to properly check similarity for {path1} and {path2}. Reason: {e}")
        return False


def file_hash(*paths: list[str], hash_func="sha256", chunk_size=8192):
    """
    Compute hashes for one or more file paths.
    Returns a dict: {path: hash}
    """
    results = {}
    for path in paths:
        if not os.path.isfile(path):
            continue  # skip non-files
        h = hashlib.new(hash_func)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                h.update(chunk)
        results[path] = h.hexdigest()
    return results


def bytewise_compare_files(path1:str, path2:str, chunk_size=8192):
    """
    Return True if two files are byte-for-byte identical.
    """
    if os.path.getsize(path1) != os.path.getsize(path2):
        return False  # different sizes → cannot be identical

    with open(path1, "rb") as f1, open(path2, "rb") as f2:
        while True:
            b1 = f1.read(chunk_size)
            b2 = f2.read(chunk_size)
            if b1 != b2:
                return False
            if not b1:  # end of file
                return True
            
def alpha_sort(a: str, b: str):
        """
        returns the shortest string, or the alphabetically sorted first string when length is identical
        """
        if len(a) > len(b):
            return b
        elif len(b) > len(a):
            return a
        elif a > b:
            return b
        else:
            return a

def filter_file(file, files, extensions, exclude=False):
            if exclude:
                if os.path.splitext(file)[1].lower() in extensions: return
            else:
                if os.path.splitext(file)[1].lower() not in extensions: return

            files.append(file)

def is_in_path(file_path:str, base_path:str):
    file_path = Path(file_path).resolve()
    base_path = Path(base_path).resolve()
    return base_path in file_path.parents

def get_video_rotation(path:str):
        """Extract rotation from video metadata using ffprobe."""
        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream_tags=rotate',
                '-of', 'json', path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
        
            data = json.loads(result.stdout)
            rotate = int(data['streams'][0]['tags'].get('rotate', 0))
        except Exception:
            rotate = 0
        return rotate

def rotate_frame(frame, rotation:int):
    """Physically rotate frame."""
    if rotation == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rotation == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    elif rotation == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame

def resize_frame(frame, width:int):
    """Resize while keeping aspect ratio."""
    h, w = frame.shape[:2]
    aspect = h / w
    new_height = int(width * aspect)
    return cv2.resize(frame, (width, new_height), interpolation=cv2.INTER_AREA)

def oriented_image(image_path:str):
    """open image oriented based on EXIF data"""
    # Step 1: Apply EXIF orientation
    img = Image.open(image_path)
    if img.mode != 'RGBA' or img.mode != 'RGB':
        img = img.convert('RGBA')

    try:
        for orientation in ExifTags.TAGS.keys():
            if ExifTags.TAGS[orientation] == 'Orientation':
                break

        exif = img._getexif()
        if exif is not None:
            orientation_value = exif.get(orientation)

            if orientation_value == 3:
                img = img.rotate(180, expand=True)
            elif orientation_value == 6:
                img = img.rotate(270, expand=True)
            elif orientation_value == 8:
                img = img.rotate(90, expand=True)
    except (AttributeError, KeyError, IndexError):
        # No EXIF data or orientation tag
        pass

    return img
