
from PIL import Image, PngImagePlugin
import pillow_heif
import piexif
import json
import datetime
import pywintypes
import win32file
import win32con
from mutagen.mp4 import MP4
import re
import datetime
import subprocess
import os
import time

pillow_heif.register_heif_opener()
enable_threading = False

magic_numbers = {
    b"\x89PNG\r\n\x1a\n": "PNG",
    b"\xFF\xD8\xFF": "JPEG",
    b"ftypheic": "HEIF",
    b"^RIFF....WEBP": "WEBP",
    b"GIF87a": "GIF",
    b"GIF89a": "GIF",
    b"\x42\x4D": "BMP",
    b"ftypqt": "MOV",
    b"ftypisom": "MOV",
    b"ftypmp42": "MP4",
    b"^RIFF....AVI": "AVI",
    b"avc1": "MP4"
}

magic_number_length = 16

def extract_json_exif(json_file):
    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}}
    metadata = {}

    # Get the JSON data (if a corresponding JSON file exists)
    json_data = None
    with open(json_file, 'r') as f:
        json_data = json.load(f)

    # Add JSON data to EXIF (mapping certain fields from JSON to EXIF tags)
    if json_data:
        #If the JSON contains GPS data, add it to the EXIF GPS tags
        if "geoData" in json_data:
            # Extract latitude and longitude
            lat = json_data["geoData"].get("latitude", 0)
            lon = json_data["geoData"].get("longitude", 0)

            # Convert GPS latitude/longitude into EXIF format (degrees, minutes, seconds)
            def to_exif_gps_coord(coord):
                degrees = int(coord)
                minutes = int((coord - degrees) * 60)
                seconds = (coord - degrees - minutes / 60) * 3600
                return ((degrees, 1), (minutes, 1), (int(seconds * 100), 100))

            # Only add GPS data if both latitude and longitude are valid (non-zero)
            if lat != 0 and lon != 0:
                exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = to_exif_gps_coord(lat)
                exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = to_exif_gps_coord(lon)
                exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = "N" if lat > 0 else "S"
                exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = "E" if lon > 0 else "W"

                metadata["Location"] = {"latitude": lat, "longitude": lon}

        # If the JSON contains photoTakenTime, add it to EXIF DateTime tags
        if "photoTakenTime" in json_data:
            # Get timestamp from JSON and convert it to the correct format for EXIF
            timestamp = json_data["photoTakenTime"].get("timestamp", None)
            if timestamp:
                timestamp = int(timestamp)
                formatted_time = datetime.datetime.fromtimestamp(timestamp).strftime("%Y:%m:%d %H:%M:%S")

                # Add to EXIF tags for DateTimeOriginal, DateTimeDigitized, and DateTime
                exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = formatted_time
                exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = formatted_time
                exif_dict["0th"][piexif.ImageIFD.DateTime] = formatted_time

                metadata["Creation Time"] = formatted_time

    return exif_dict, metadata

def process_exif(file_path, json_exif, exif_type, logger, file=None):
    """
    Process and modify EXIF data of an image.
    """

    # Open the image if not provided
    start = time.time()
    image = file if file else Image.open(file_path)
    logger.add_time(time.time()-start, "Opening image")

    try:
        start = time.time()
        # Load existing EXIF data or initialize an empty EXIF structure
        exif = image.info.get("exif", None)
        exif_dict = piexif.load(exif) if exif else {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "Interop": {}}

        # Merge json_exif with exif_dict at the tag level
        for ifd in ("0th", "Exif", "GPS"):
            if ifd in json_exif:
                for tag, value in json_exif[ifd].items():
                    # Update or add the tag only if it's not already present
                    exif_dict[ifd][tag] = value

        # Save the updated EXIF data back to the image
        exif_bytes = piexif.dump(exif_dict)
        image.save(file_path, "jpeg", exif=exif_bytes)
        logger.add_time(time.time()-start, "Insert metadata")
    except:
        pass


def process_meta(file_path, json_data, type, logger, file=None):
    # Open the image
    start = time.time()
    image = file if file else Image.open(file_path)
    logger.add_time(time.time()-start, "Opening image")

    start = time.time()
    metadata = PngImagePlugin.PngInfo()


    # Merge metadata
    all_keys = set(image.info.keys()).union(json_data.keys())
    for key in all_keys:
        original_value = image.info.get(key)
        new_value = json_data.get(key, original_value)
        if isinstance(new_value, str):  # Ensure value is a string
            metadata.add_text(key, new_value)

    # Save the image with the new metadata
    image.save(file_path, type, pnginfo=metadata)
    logger.add_time(time.time()-start, "Insert metadata")


def process_no_meta(file_path, json_data, logger, file=None): # simply set the creation time of the file to the "Creation time"
    start = time.time()
    creation_time = json_data.get("Creation Time", None)
    creation_time = datetime.datetime.strptime(creation_time, "%Y:%m:%d %H:%M:%S") if creation_time else None

    win_filetime = pywintypes.Time(creation_time)
    handle = win32file.CreateFile(
        file_path,
        win32con.GENERIC_WRITE,
        win32con.FILE_SHARE_WRITE,
        None,
        win32con.OPEN_EXISTING,
        0,
        None
    )
    current_times = win32file.GetFileTime(handle)
    win32file.SetFileTime(handle, win_filetime, current_times[1], current_times[2])
    handle.close()
    logger.add_time(time.time()-start, "Insert metadata")



def process_atoms(file_path, json_data, logger, file=None):
    start = time.time()
    # Extract and format the creation time
    creation_time = json_data.get("Creation Time", None)

    # Update metadata using ExifTool    
    command = [
        'exiftool',
        '-overwrite_original',
        '-q',
        f"-QuickTime:MediaCreateDate={creation_time}",
        f"-QuickTime:CreateDate={creation_time}",
        f"-XMP:CreateDate={creation_time}",
        file_path
    ]
    
    # Execute the command
    subprocess.run(command, check=True)
    logger.add_time(time.time()-start, "Insert video metadata")

def remove_suffix(filepath):
    directory, filename = os.path.split(filepath)
    name, ext = os.path.splitext(filename)
    
    # Remove ' (1)' if it appears at the end of the name
    new_name = re.sub(r' \(\d+\)$', '', name)
    
    new_filepath = os.path.join(directory, new_name + ext)
    return new_filepath
    

def process_file(file_path, original_path, jsons, logger, file=None, remove_jsons=False, ):
    potential_names = [original_path, remove_suffix(original_path)]

    first_existing = next((jsons[name] for name in potential_names if os.path.abspath(name) in jsons), None)


    if not first_existing:
        logger.add_error(file_path, "No JSON file")
        return
        
    start = time.time()
    json_exif, json_meta = extract_json_exif(first_existing)
    logger.add_time(time.time()-start, "Extract metadata")

    with open(file_path, 'rb') as f:
        magic_number = f.read(magic_number_length)

    try:
        # case statement for different extensions
        for magic, ext in magic_numbers.items():
            if bool(re.search(magic, magic_number)):
                if ext in ["JPEG", "HEIF", "WEBP"]:
                    process_exif(file_path, json_exif, ext, logger, file)
                elif ext == "PNG":
                    process_meta(file_path, json_meta, ext, logger, file)
                elif ext == "GIF":
                    process_no_meta(file_path, json_meta, logger, file)
                elif ext in ["MP4", "MOV"]:
                    process_atoms(file_path, json_meta, logger, file)
                break
        else:
            logger.add_error(file_path, "Unknown extension")

        if remove_jsons:
            try:
                os.remove(first_existing)
            except:
                pass

    except Exception as e:
        logger.add_error(file_path, e)
        
