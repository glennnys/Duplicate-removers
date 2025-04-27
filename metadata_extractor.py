
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

def extract_json_exif(json_file, files):
    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}}
    metadata = {}

    # Get the JSON data (if a corresponding JSON file exists)
    json_data = None
    if json_file in files:
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

def process_exif(file_path, json_exif, exif_type, file=None):
    """
    Process and modify EXIF data of an image.
    """

    # Open the image if not provided
    image = file if file else Image.open(file_path)
    try:
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
    except:
        pass


def process_meta(file_path, json_data, type, file=None):
    # Open the image
    if file is None:
        image = Image.open(file_path)
    else:
        image = file

    metadata = PngImagePlugin.PngInfo()

    for key, value in image.info.items():
        if isinstance(value, str):  # Only process text metadata
            if key in json_data:
                metadata.add_text(key, json_data[key])
            else:
                metadata.add_text(key, value)

    # Save the image with the new metadata
    image.save(file_path, type, pnginfo=metadata)


def process_no_meta(file_path, json_data, file=None): # simply set the creation time of the file to the "Creation time"

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



def process_atoms(file_path, json_data, file=None):
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
    

def process_file(file_path, original_path, jsons, file=None):
    json_file = []
    json_file.append(original_path + ".json")
    json_file.append(original_path + ".json")
    file_name3, _ = os.path.splitext(original_path)
    json_file.append(file_name3 + ".json")
    json_file.append(original_path[:-1] + ".json")
    json_file.append(original_path[:-2] + ".json")
    json_file.append(original_path[:-3] + ".json")
    json_file.append(original_path[:-4] + ".json")
    json_file.append(original_path[:-5] + ".json")
    json_file.append(original_path[:-6] + ".json")

    first_existing = next((file for file in json_file if file in jsons), None)


    if not first_existing:
        #problem solving
        match = re.search(r'\((\d+)\)(?!.*\(\d+\))', json_file[0])
        if match:
            # Extract the base name and pattern
            base_name = re.sub(r'\((\d+)\)(?!.*\(\d+\))', '', json_file[0])  # Remove the last occurrence of (x)
            bracket_part = match.group(0)  # The matched (x) part
            # Insert the bracket part right before the .json extension
            first_existing = f"{base_name.rstrip('.json')}{bracket_part}.json"

            if first_existing not in jsons:
                print(f'{file_path} does not appear to have a JSON file')
                return

        else:
            print(f'{file_path} does not appear to have a JSON file')
            return

    json_exif, json_meta = extract_json_exif(first_existing, jsons)

    with open(file_path, 'rb') as f:
        magic_number = f.read(magic_number_length)

    try:
        # case statement for different extensions
        for magic, ext in magic_numbers.items():
            if bool(re.search(magic, magic_number)):
                if ext in ["JPEG", "HEIF", "WEBP"]:
                    process_exif(file_path, json_exif, ext, file)
                elif ext == "PNG":
                    process_meta(file_path, json_meta, ext, file)
                elif ext == "GIF":
                    process_no_meta(file_path, json_meta, file)
                elif ext in ["MP4", "MOV"]:
                    process_atoms(file_path, json_meta, file)
                break
        else:
            print(file_path, "Unknown extension", magic_number)
    except Exception as e:
        print(file_path, e, magic_number)
        
