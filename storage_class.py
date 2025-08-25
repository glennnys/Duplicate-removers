import threading
import cv2
from PIL import Image, ImageFile
from PIL import ExifTags 
import imagehash
import metadata_extractor as pe
import os
from collections import namedtuple, defaultdict
import random
import pickle
import math
from pathlib import Path
import shutil
import time
import subprocess
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from skimage.metrics import structural_similarity as ssim
import numpy as np
import logkeeper as lk

Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True

VPTreeNode = namedtuple('VPTreeNode', ['point', 'threshold', 'left', 'right'])
    
class HashStorage:
    def __init__(self, stop_event, enable_threading=True, threshold=0.9, extract_meta=True, phash_res=8, advanced_comparison=True, logger=None, tracker=None):
        self.stop_event = stop_event
        self.enable_threading=enable_threading
        self.threshold = threshold
        self.extract_meta = extract_meta
        self.advanced_comparison = advanced_comparison

        if logger is None:
            self.logger = lk.LogKeeper()

        if tracker is None:
            self.tracker = {
                "progress": 0,
                "process": None,
                "time remaining":  0,
                "i": 0,
                "total": 0
            }

        self.reset()

        self.phash_res = phash_res
        self.data_handling = "1"
        self.duplicate_handling = "1"

        self.real_threshold = math.ceil((phash_res**2) * (1-threshold))

    def reset(self):
        self.json_files = {}
        self.old_images = {}
        self.old_videos = {}
        self.new_images = {}
        self.new_videos = {}
        self.name_index_images = defaultdict(list)
        self.name_index_videos = defaultdict(list)
        self.higher_res_to_compare = []
        self.duplicates_to_compare = []
        self.checked_nodes = 0
        self.cache_name = None

        self.verified = {}

##### SAVE AND LOAD #####
    def save_items(self):
        if self.existing_folder == "": return
        serialized = []
        abs_path = os.path.abspath(self.existing_folder)
        
        serialized.append(abs_path) # identifier when opening

        combination = self.old_images | self.old_videos | self.new_images | self.new_videos

        #existing files
        for key, item in combination.items():
            try:
                if not isinstance(item[1], (int, float)) or not isinstance(item[2], (int, float)):
                    continue

                if not isinstance(item[0], list):
                    serialized.append((key, str(item[0]), item[1], item[2]))
                else:
                    hashhex=[str(i) for i in item[0]]
                    serialized.append((key, hashhex, item[1], item[2]))
            
            except Exception as e:
                print(f"Couldn't save {key}. Reason: {e}")
        
        filepath = os.path.join(os.getcwd(), "hash cache", self.cache_name) if self.cache_name is not None else self.safe_rename(os.path.join(os.getcwd(), "hash cache", "dupe.cache"))
        self.cache_name = os.path.basename(filepath)
        print(f"Saving hashes to {filepath}")
        with open(filepath, 'wb') as f: 
            pickle.dump(serialized, f)

    def load_items(self, new_files=[]):
        caches_path = os.path.join(os.getcwd(), "hash cache")
        for filename in os.listdir(caches_path):
            filepath = os.path.join("hash cache", filename)
            with open(filepath, 'rb') as f:
                serialized = pickle.load(f)

                if serialized[0] != os.path.abspath(self.existing_folder):
                    continue
                
                print(f"Loading saved hashes from {filepath}")
                self.cache_name = filename

                for path, hash_str, ar, size in serialized[1:]:
                    if not isinstance(path, str) or not isinstance(ar, (float, int)) or not isinstance(size, (int, float)): 
                        continue
                    try:
                        if os.path.exists(path) and os.path.getsize(path) == size:

                            item = []
                            if path.startswith(self.existing_folder):
                                if isinstance(hash_str, list):
                                    hash_val = [imagehash.hex_to_hash(hash_st) for hash_st in hash_str]
                                    item = [hash_val, ar, size, False, None]
                                    self.old_videos[path] = item
                                    self.add_video_name_index(item, path)
                                else:
                                    hash_val = imagehash.hex_to_hash(hash_str)
                                    item = [hash_val, ar, size, False, None] 
                                    self.old_images[path] = item
                                    self.add_image_index(item, path)

                            elif path in new_files:
                                if isinstance(hash_str, list):
                                    hash_val = [imagehash.hex_to_hash(hash_st) for hash_st in hash_str]
                                    item = [hash_val, ar, size, False, None]
                                    self.new_videos[path] = item
                                else:
                                    hash_val = imagehash.hex_to_hash(hash_str)
                                    item = [hash_val, ar, size, False, None] 
                                    self.new_images[path] = item

                    except Exception as e:
                        print(f"Couldn't load {path}. Reason: {e}")  
 
                break

##### DO THE THING #####
    def check_if_previously_processed(self, file):
        rel_path = os.path.relpath(file, self.new_folder)
        destination_folders = [self.new_dest, self.unsorted_dest, self.dupe_dest, self.high_res_dupe_dest, self.err_dest]
        possible_destinations = [os.path.join(destination_folder, rel_path) for destination_folder in destination_folders]

        for possible_dest in possible_destinations:
            if os.path.exists(possible_dest) and self.files_are_identical(file, possible_dest):
                self.verified[file] = ("Already done", possible_dest)
                return
        
        return file

    def fast_search(self, original_files, new_files, process_name="fast searching files", only_compare_to_originals=True, allow_comparison=True):
        original_size_dict = defaultdict(list)
        new_size_dict = defaultdict(list)
        new_files_for_deeper_check = []

        self.tracker["progress"] = 0
        self.tracker["time remainig"] = 0
        self.tracker["i"] = 0
        
        # skip files that were already processed
        files_to_process = []
        results = self.run_task(self.check_if_previously_processed, args=new_files, process_name=f"{process_name}: checking if already processed", allow_saving=False)
        if self.stop_event.is_set(): return 
        for result in results:
            if result: files_to_process.append(result)

        if len(files_to_process) == 0: return []

        # Store as lists (mutable): [name, is_new, hash, is_duplicate]
        for file in original_files:
            original_size_dict[os.path.getsize(file)].append([file, False, None, False])

        for file in files_to_process:
            new_size_dict[os.path.getsize(file)].append([file, True, None, False])                  

        args = []
        for size, files in new_size_dict.items():
            for idx, new_file in enumerate(files):
                file_range = files[:idx] + files[idx+1:]
                if not only_compare_to_originals: file_range += original_size_dict[size]
                if file_range != []:
                    args.append((new_file, file_range))
                else:
                    new_files_for_deeper_check.append(new_file[0])
            
        self.tracker["total"] = len(args)

        def check_file(new_file, file_range):
                """Return (is_duplicate, new_file_path, original/better dupe)"""
                for other_file in file_range:
                    if self.stop_event.is_set():
                        return None
                    if self.fast_compare(new_file, other_file):
                        if self.files_are_identical(new_file[0], other_file[0]):
                            self.handle_result((0, other_file[0], new_file[0], "low-res duplicate"), allow_comparison)
                            return
                        
                return new_file[0]

            
        results = self.run_task(function=check_file, args=args, process_name=process_name, allow_saving=False)
        if self.stop_event.is_set(): return 
        for result in results:
            if result is not None: new_files_for_deeper_check.append(result)

        return new_files_for_deeper_check
          
    def build_image_tree(self):
        start = time.time()

        self.image_tree = self.build_vptree(self.old_images | self.new_images)
        self.logger.add_time(time.time()-start, "Build image tree")

    def build_video_tree(self):
        start = time.time()
        self.video_tree = self.build_vptree(self.old_videos | self.new_videos)
        self.logger.add_time(time.time()-start, "Build video tree")
   
    def build_vptree(self, items_dict):
        def _build(paths):
            if not paths:
                return None

            vantage_idx = random.randint(0, len(paths) - 1)
            vantage_path = paths[vantage_idx]
            vantage_hash = items_dict[vantage_path][0]
            rest = paths[:vantage_idx] + paths[vantage_idx+1:]

            if not rest:
                return VPTreeNode(point=vantage_path, threshold=0, left=None, right=None)

            distances = [
                (p, self.hamming_distance(vantage_hash, items_dict[p][0])) for p in rest
            ]
            distances.sort(key=lambda x: x[1])
            median = distances[len(distances) // 2][1]
            left_paths = [p for p, d in distances if d <= median]
            right_paths = [p for p, d in distances if d > median]

            return VPTreeNode(
                point=vantage_path,
                threshold=median,
                left=_build(left_paths),
                right=_build(right_paths)
            )

        return _build(list(items_dict.keys()))

    def search_vptree(self, tree, query_path, items, new_items, result=None, is_images=True):
        start = time.time()
        if result is None:
            try:
                query = new_items[query_path]
                query_hash = query[0]
                query_ar = query[1]
                query_res = query[2]
                result = (query_res, None, query_path, "new")
            except:
                result = (0, None, query_path, "error")
                return result
            
        if tree is None or result[3] == "low-res duplicate": # end condition
            return result
        
        self.checked_nodes += 1
        
        try:
            query = new_items[query_path]
            query_hash = query[0]
            query_ar = query[1]
            query_res = query[2]

            if query[3]: #disabled node
                return (0, query[4], query_path, "low-res duplicate")
            
            candidate_path = tree.point
            candidate = items[candidate_path]
            candidate_hash = candidate[0]
            candidate_ar = candidate[1]
            candidate_res = candidate[2]
        except:
            #skip nodes with problems
            return (0, candidate_path, query_path, "error")
        
        self.logger.add_time(time.time()-start, "tree search prep")
        start = time.time()
        
        d = self.hamming_distance(query_hash, candidate_hash)

        if candidate_path != query_path:

            if d < self.real_threshold and self.ar_similarity(candidate_ar, query_ar) >= self.threshold:
                
                if not is_images or (is_images and self.advanced_comparison and self.is_really_duplicate(candidate_path, query_path)):
                    self.logger.add_time(time.time()-start, "dupe calculation: succes")
                    if self.is_in_path(candidate_path, self.existing_folder) and self.existing_folder != "":
                        if candidate_res >= query_res:
                            return (candidate_res, candidate_path, query_path, "low-res duplicate")
                        else:
                            result = (query_res, candidate_path, query_path, "high-res duplicate")

                    else:
                        if candidate_res > query_res:
                            return (candidate_res, candidate_path, query_path, "low-res duplicate")
                        else:
                            if query_res > candidate_res or self.alpha_sort(query_path, candidate_path) == query_path:
                                if result[3] == "high-res duplicate":
                                    result = (query_res, candidate_path, query_path, "high-res duplicate")
                                else:
                                    result = (query_res, candidate_path, query_path, "best new duplicate")
                                self.disable_node(candidate_path, query_path, is_images)
                            else:
                                return (candidate_res, candidate_path, query_path, "low-res duplicate")
                            
                self.logger.add_time(time.time()-start, "dupe calculation: advanced fail")

            self.logger.add_time(time.time()-start, "dupe calculation: basic fail")

        go_left = d - self.real_threshold <= tree.threshold
        go_right = d + self.real_threshold >= tree.threshold

        if go_left and go_right:
            if d < tree.threshold:
                result = self.search_vptree(tree.left, query_path, items, new_items, result, is_images)
                result = self.search_vptree(tree.right, query_path, items, new_items, result, is_images)
            else:
                result = self.search_vptree(tree.right, query_path, items, new_items, result, is_images)
                result = self.search_vptree(tree.left, query_path, items, new_items, result, is_images)
        elif go_left:
            result = self.search_vptree(tree.left, query_path, items, new_items, result, is_images)
        elif go_right:
            result = self.search_vptree(tree.right, query_path, items, new_items, result, is_images)

        return result

    def precheck_same_names(self, item, is_image=True):
        start = time.time()
        if is_image:
            name_matches = self.name_index_images.get(os.path.basename(item[0]), [])
        else:
            name_matches = self.name_index_videos.get(os.path.basename(item[0]), [])
        
        for match in name_matches:
            if self.hamming_distance(match[1], item[1][0]) < self.real_threshold and match[2]>=item[1][2]:
                self.logger.add_time(time.time()-start, "prechecking: duplicate")    
                return (0, match[0], item[0], 'low-res duplicate')

        self.logger.add_time(time.time()-start, "prechecking: no duplicate") 
        return (0, None, item[0], None)

    def check_duplicates(self, files1, files2):
        if not isinstance(files1, (list, tuple)):
            raise TypeError(f"Expected list, got {type(files1).__name__}")
        if not isinstance(files2, (list, tuple)):
            raise TypeError(f"Expected list or tuple, got {type(files2).__name__}")

        start = time.time()
        self.reset()
        # Separate images and videos from other files for folder 1
        images1 = [file for file in files1 if os.path.splitext(file)[1].lower() in [".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"]]
        videos1 = [file for file in files1 if os.path.splitext(file)[1].lower() in [".mp4", ".mov", ".avi", ".mkv", ".m4v"]]
        remaining_files1 = [file for file in files1 if file not in images1 and file not in videos1]
        if self.stop_event.is_set(): return
        
        # Separate images and videos from other files for folder 2
        images2 = [file for file in files2 if os.path.splitext(file)[1].lower() in [".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"]]
        videos2 = [file for file in files2 if os.path.splitext(file)[1].lower() in [".mp4", ".mov", ".avi", ".mkv", ".m4v"]]
        jsons = [file for file in files2 if os.path.splitext(file)[1].lower() in [".json"]]
        jsons_dict = {}

        for file_path in jsons:
            with open(file_path, 'r') as file:
                data = json.load(file)
                jsons_dict[os.path.join(os.path.dirname(file_path), data['title'])] = os.path.abspath(file_path)

        remaining_files2 = [file for file in files2 if file not in images2 and file not in videos2 and file not in jsons]
        if self.stop_event.is_set(): return

        self.logger.add_time(time.time()-start, "Setup")

        results = self.fast_search(remaining_files1, remaining_files2, process_name="comparing non-image and video files", only_compare_to_originals=True, allow_comparison=False)
        if self.stop_event.is_set(): return
        for result in results:
            if result is not None:
                self.handle_result((0, None, result, ""))

        simple_search = self.threshold == 1

        results = self.fast_search(images1, images2, process_name="fast searching images", only_compare_to_originals=False)
        if self.stop_event.is_set(): return
        potentially_new_images = []
        for result in results:
            if result is not None:
                if simple_search:
                    self.handle_result((None, None, result, "new"))
                else:   
                    potentially_new_images.append(result)

        results = self.fast_search(videos1, videos2, process_name="fast searching videos", only_compare_to_originals=False)
        if self.stop_event.is_set(): return
        potentially_new_videos = []
        for result in results:
            if result is not None:
                if simple_search:
                    self.handle_result((None, None, result, "new"))
                else:   
                    potentially_new_videos.append(result)
        
        if not simple_search:
            start = time.time()
            self.tracker["progress"] = 0
            self.tracker["process"] = "loading saved hashes"
            self.tracker["time remaining"] = 0
            self.load_items(potentially_new_images + potentially_new_videos)
            if self.stop_event.is_set(): return
            self.logger.add_time(time.time()-start, "Load hashes")

            if len(potentially_new_images) > 0:
                random.shuffle(potentially_new_images)
                self.run_task(process_name="Hashing old images", function=self.hash_image, args=[(image, False) for image in images1])
                if self.stop_event.is_set(): return
                self.run_task(process_name="Hashing new images", function=self.hash_image, args=[(image, True) for image in potentially_new_images])
                if self.stop_event.is_set(): return
                self.save_items()
                if self.stop_event.is_set(): return
                self.build_image_tree()
                if self.stop_event.is_set(): return
                self.run_task(process_name="finding duplicate images", function=self.find_duplicates, args=[(image, self.old_images | self.new_images, self.new_images) for image in self.new_images.items()])
                if self.stop_event.is_set(): return

            if len(potentially_new_videos) > 0:    
                random.shuffle(potentially_new_videos)
                self.run_task(process_name="Hashing old videos", function=self.hash_video, args=[(video, False) for video in videos1])
                if self.stop_event.is_set(): return
                self.run_task(process_name="Hashing new videos", function=self.hash_video, args=[(video, True) for video in potentially_new_videos])
                if self.stop_event.is_set(): return
                self.save_items()
                if self.stop_event.is_set(): return
                self.build_video_tree()
                if self.stop_event.is_set(): return
                self.run_task(process_name="finding duplicate videos", function=self.find_duplicates, args=[(video, self.old_videos | self.new_videos, self.new_videos) for video in self.new_videos.items()])
                if self.stop_event.is_set(): return
            
        files_unverified = self.verify(files2)
        if len(files_unverified) == 0:
            self.tracker["progress"] = 0
            self.tracker["process"] = "Verified all files"
            print("All files verified")
            self.tracker["total"] = None
            time.sleep(2)
        else:
            self.tracker["progress"] = 0
            self.tracker["process"] = "Unable to verify all files, check terminal for problems"
            self.tracker["total"] = None
            print(f"{files_unverified}. These files were unable to be verified for some reason or another")
            time.sleep(5)
        if self.stop_event.is_set(): return
        
    def handle_result(self, result, allow_comparison=True): 
        """Result looks like (resolution, path, better dupe path, what kind of dupe)"""
        if result is None:
            return

        elif result[3] == "low-res duplicate":
            destination = self.dupe_dest
        
        elif result[3] in ["best new duplicate", "new"]:
            destination = self.new_dest

        elif result[3] == "high-res duplicate":
            destination = self.high_res_dupe_dest
            
        elif result[3] == "error":
            destination = self.err_dest

        else:
            destination = self.unsorted_dest
        
        # copy file handles different destination based on data handling settings
        start = time.time()
        dest_file = self.copy_file(result[2], destination)
        self.logger.add_time(time.time()-start, "Copy item")

        # to open window for comparison
        if allow_comparison and self.duplicate_handling != "3" and result[3] == "high-res duplicate" and dest_file is not None:
            self.higher_res_to_compare.append((result[1], dest_file))

        if allow_comparison and destination == self.dupe_dest and self.duplicate_handling == "2":
            self.duplicates_to_compare.append((result[1], dest_file)) 
                 

##### PSEUDO-HELPERS #####
    def hash_image(self, image, is_new=False):
        start = time.time()
        if is_new:
            if image not in self.new_images:
                item = self.get_image_hash(image)
                self.new_images[image] = item
        else:
            if image not in self.old_images:
                item = self.get_image_hash(image)
                self.old_images[image] = item
                self.add_image_index(item, image)

        self.logger.add_time(time.time()-start, "Hash image")

    def hash_video(self, video, is_new=False):
        start = time.time()
        if is_new:
            if video not in self.new_videos:
                item = self.get_video_hashes(video)
                self.new_videos[video] = item

        else:
            if video not in self.old_videos:  
                item = self.get_video_hashes(video)
                self.old_videos[video] = item
                self.add_video_name_index(item, video)

        self.logger.add_time(time.time()-start, "Hash video")
      
    def get_image_hash(self, image_path):
        if os.path.getsize(image_path) == 0:
            im = Image.new(mode="RGB", size=(200, 200))
            return [imagehash.phash(im), 1, 0, False, None]
        # GIF support: If the file is a GIF, hash the first frame only.
        try:
            if image_path.lower().endswith('.gif'):
                with Image.open(image_path) as img:
                    img.seek(0)  # First frame
                    img = img.convert('RGBA')
                    w,h = img.size
                    ar = w/h
                    try:
                        return [imagehash.phash(img), ar, os.path.getsize(image_path), False, None]
                    except:
                        img.thumbnail((1000, 1000))
                        return [imagehash.phash(img), ar, os.path.getsize(image_path), False, None]
            else:
                image = self.oriented_image(image_path)
            try: 
                w,h = image.size
                ar = w/h
                return [imagehash.phash(image), ar, os.path.getsize(image_path), False, None]
            except:
                image.thumbnail((1000, 1000))
                w,h = image.size
                ar = w/h
                return [imagehash.phash(image), ar, os.path.getsize(image_path), False, None]
        except Exception as e:
            print(f"file {image_path} couldn't be hashed: {e}")
            im = Image.new(mode="RGB", size=(200, 200))
            return [imagehash.phash(im), 1, 0, False, None]

    def get_video_hashes(self, video_path, frame_interval=24, max_hashes=3):
        if os.path.getsize(video_path) == 0:
            im = Image.new(mode="RGB", size=(200, 200))
            return [imagehash.phash(im), 1, 0, False, None]
        
        #if self.advanced_comparison:
        try:
            rotation = self.get_video_rotation(video_path)
            cap = cv2.VideoCapture(video_path)
            video_hashes = []
            frame_count = 0
            hash_count = 0
            ar = 1
            
            while cap.isOpened() and hash_count < max_hashes:
                ret, frame = cap.read()
                if not ret:
                    break
                if frame_count % frame_interval == 0:
                    frame = self.rotate_frame(frame, rotation)

                    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                
                    video_hashes.append(imagehash.phash(pil_img))
                    hash_count += 1

                    if hash_count == max_hashes:
                        w,h = pil_img.size
                        ar = w/h
                
                frame_count += 1

            item = [video_hashes, ar, os.path.getsize(video_path), False, None]

            if cap.isOpened():
                cap.release()
        
        except Exception as e:
            print(f"file {video_path} couldn't be hashed: {e}")
            im = Image.new(mode="RGB", size=(200, 200))
            video_hashes = [imagehash.phash(im) for i in range(max_hashes)]
            item = [video_hashes, 1, 0, False, None]
    
        return item

    def find_duplicates(self, item, all_items, new_items):
        start = time.time()
        if item[1][0] is None: # problemo
                result = (None, None, item[0], None)

        elif item[1][3]: # disabled node
            result = (None, item[1][4], item[0], 'low-res duplicate')

        else:
            if type(item[1][0]) != list: # list means video hashes, else image hash
                result = self.precheck_same_names(item, is_image=True)
                if result[3] != 'low-res duplicate':
                    start2 = time.time()
                    result = self.search_vptree(self.image_tree, item[0], all_items, new_items, is_images=True)
                    self.logger.add_time(time.time()-start2, "searching tree")

            else:
                result = self.precheck_same_names(item, is_image=False)
                if result[3] != 'low-res duplicate': 
                    start2 = time.time()
                    result = self.search_vptree(self.video_tree, item[0], all_items, new_items, is_images=False)
                    self.logger.add_time(time.time()-start2, "searching tree")
        
        self.logger.add_time(time.time()-start, "searching duplicates")
        self.handle_result(result)
        
   
##### HELPERS #####

## FAST ##
    def is_really_duplicate(self, img1_path, img2_path):
        try:
            def load_image(path, size=(256, 256)):
                # Try OpenCV first
                img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    return cv2.resize(img, size)

                # Fallback to Pillow
                try:
                    pil_img = Image.open(path).convert("L")
                    pil_img = pil_img.resize(size)
                    return np.array(pil_img)
                except Exception as e:
                    raise ValueError(f"Could not load image {path}: {e}")
        
            img1 = load_image(img1_path)
            img2 = load_image(img2_path)
            score, _ = ssim(img1, img2, full=True)
            return score >= self.threshold
        except Exception as e:
            print(f"Unable to properly check similarity for {img1_path} and {img2_path}. Reason: {e}")
            return True

    def file_hash(self, *paths, hash_func="sha256", chunk_size=8192):
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
    
    def files_are_identical(self, path1, path2, chunk_size=8192):
        """Return True if two files are byte-for-byte identical."""
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

    def fast_compare(self, file1, file2):
        if file1[0] == file2[0]:
            return False
        if file1[3]:
            return True
        
        if file1[2] is None:
            file1[2] = self.file_hash(file1[0])[file1[0]]
        if file2[2] is None:
            file2[2] = self.file_hash(file2[0])[file2[0]]

        if file1[2] == file2[2]:          
            if not file2[1]:
                file1[3] = True
                return True
            
            elif self.alpha_sort(file1[0], file2[0]) == file1[0]:
                file2[3] = True
                return False

            else:
                file1[3] = True
                return True
        
        return False


## FILES ##
    def get_video_rotation(self, path):
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

    def rotate_frame(self, frame, rotation):
        """Physically rotate frame."""
        if rotation == 90:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif rotation == 180:
            return cv2.rotate(frame, cv2.ROTATE_180)
        elif rotation == 270:
            return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame

    def resize_frame(self, frame, width):
        """Resize while keeping aspect ratio."""
        h, w = frame.shape[:2]
        aspect = h / w
        new_height = int(width * aspect)
        return cv2.resize(frame, (width, new_height), interpolation=cv2.INTER_AREA)
    
    def oriented_image(self, image_path):
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
  
    def get_image_size(self, image):
        if isinstance(image, Image.Image):
            if image.size is None:
                return (0, 0)
            else:
                return image.size
        else:
            return(0, 0)
    
    def get_video_size(self, video):
        if isinstance(video, cv2.VideoCapture):
            res = int(video.get(cv2.CAP_PROP_FRAME_WIDTH)), int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if res is None:
                return (0, 0)
            else:
                return res
        else:
            return(0, 0)


## PRE ##
    def add_image_index(self, item, path):
        self.name_index_images[os.path.basename(path)].append((path, item[0], item[2]))

    def add_video_name_index(self, item, path):
        self.name_index_videos[os.path.basename(path)].append((path, item[0], item[2]))


## TREE ##
    def ar_similarity(self, ar1, ar2):
        if ar1==ar2: return 1
        similarity = min(ar1, ar2) / max(ar1, ar2)
        return similarity

    def hamming_distance(self, h1, h2):
        if h1 is None or h2 is None:
            return self.phash_res**2 
        
        if type(h1) == list and type(h2) == list:
            if len(h1) == 0 or len(h2) == 0:
                return self.phash_res**2
            
            sum = 0
            for i in range(min(len(h1), len(h2))):
                sum += self.hamming_distance(h1[i], h2[i])
            return sum // min(len(h1), len(h2))
        
        return h1 - h2  

    def disable_node(self, path, other_path, is_image=True):
        if is_image:
                self.new_images[path][3] = True
                self.new_images[path][4] = other_path
        else:
                self.new_videos[path][3] = True
                self.new_videos[path][4] = other_path


## GENERAL ##
    def alpha_sort(self, a, b):
        if len(a) > len(b):
            return b
        elif len(b) > len(a):
            return a
        elif a > b:
            return b
        else:
            return a

    def run_task(self, function, args, process_name, allow_saving=True):
        if self.stop_event.is_set():
                return
        
        start = time.time()
        self.tracker["progress"] = 0
        self.tracker["process"] = process_name
        self.tracker["time remaining"] = 0
        self.tracker["total"] = len(args)

        results = []
        if self.enable_threading:            
            with ThreadPoolExecutor() as executor:
                # Submit lazily with as_completed, not all at once
                futures = {executor.submit(function, *(arg if isinstance(arg, (tuple, list)) else (arg,))): arg for arg in args}

                for i, future in enumerate(as_completed(futures)):
                    self.tracker["i"] = i
                    if self.stop_event.is_set():
                        # Cancel remaining futures that haven't started
                        for f in futures:
                            f.cancel()
                        break

                    try:
                        future.result()
                        results.append(future.result())
                    except Exception as e:
                        print("Task error:", e)

                    if i % 10 == 0:
                        self.tracker["progress"] = i / self.tracker["total"]
                        self.tracker["time remaining"] = (time.time() - start) * (self.tracker["total"] - i) / (i + 1)
            
        else:
            for i, arg in enumerate(args): 
                self.tracker["i"] = i
                if self.stop_event.is_set():
                    break
                if not isinstance(arg, (tuple, list)):
                    arg = (arg,)
                results.append(function(*arg))
                if i % 10 == 0:
                        self.tracker["progress"] = i / self.tracker["total"]
                        self.tracker["time remaining"] = (time.time()-start)*(self.tracker["total"]-i)/(i+1)

        if self.stop_event.is_set():
            if allow_saving:
                self.save_items()
            return
        
        self.logger.add_time(time.time()-start, process_name)
        return results

    def is_in_path(self, file_path, base_path):
        file_path = Path(file_path).resolve()
        base_path = Path(base_path).resolve()
        return base_path in file_path.parents


##### DATA MANAGEMENT #####
    def move_file(self, file_path, current_folder, dest_folder):
        try:
            file_name = os.path.relpath(file_path, start=current_folder)
            dest_path = os.path.join(dest_folder, file_name)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            # if the same file name already exists, add a number at the end
            dest_path = self.safe_rename(dest_path)
            os.makedirs(dest_folder, exist_ok=True)
            os.rename(file_path, dest_path)
        
        except Exception as e:
            self.logger.add_error(file_path, e)
            self.verified[file_path] = ("Error", dest_path)
            print(f"failed to move {file_name} from {current_folder} to {dest_folder}. Error: {e}")

        return dest_path

    def copy_file(self, file_path, dest_folder, higher_res=False):
        # if duplicates shouldn't be moved, return
        if self.data_handling in ["2", "4"] and dest_folder not in [self.unsorted_dest, self.new_dest] and not higher_res: 
            self.verified[file_path] = ("No action", dest_folder)
            return file_path

        if self.data_handling in ["6"] and dest_folder not in [self.dupe_dest, self.high_res_dupe_dest]: 
            self.verified[file_path] = ("No action", dest_folder)
            return None


        if self.data_handling in ["5"] and not higher_res:
            if dest_folder not in [self.unsorted_dest, self.new_dest]:
                # if it is a duplicate. remove it
                try:
                    os.remove(file_path)
                    self.verified[file_path] = ("Removed", dest_folder)
                    return file_path
                
                except Exception as e:
                    print(f"Unable to remove {file_path}. Reason: {e}")
                    self.verified[file_path] = ("Error", dest_folder)
                    return None
            else:
                if self.extract_meta:
                    pe.process_file(file_path=file_path, original_path=file_path, jsons=self.json_files, logge=self.logger, remove_jsons=self.json_handling)
                
                if os.path.exists(file_path) and os.path.getsize(file_path)>0:
                    self.verified[file_path] = ("No action", dest_folder)
                return file_path
        if higher_res:
            file_name = os.path.relpath(file_path, start=self.high_res_dupe_dest)
        else:
            file_name = os.path.relpath(file_path, start=self.new_folder)
        dest_path = os.path.join(dest_folder, file_name)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        # if the same file name already exists, add a number at the end
        dest_path = self.safe_rename(dest_path)

        try:
            #copy or move it
            if self.data_handling in ["1", "2"] and not higher_res:
                os.makedirs(dest_folder, exist_ok=True)
                with open(file_path, 'rb') as src, open(dest_path, 'wb') as dest:
                    dest.write(src.read())                 

            elif self.data_handling in ["3", "4", "6"] or higher_res:
                os.makedirs(dest_folder, exist_ok=True)
                os.rename(file_path, dest_path)
                      
            if self.extract_meta and dest_folder == self.new_dest:
                pe.process_file(file_path=dest_path, original_path=file_path, jsons=self.json_files, logge=self.logger, remove_jsons=self.json_handling)

            #verification
            if os.path.exists(dest_path) and (os.path.getsize(dest_path)>0 or dest_folder == self.err_dest):
                self.verified[file_path] = ("Moved or Copied", dest_path) 
            else:
                self.verified[file_path] = ("Error", dest_path)

            return dest_path
    
        except Exception as e:
            self.logger.add_error(file_path, e)
            self.verified[file_path] = ("Error", dest_path)
            print(f"failed to copy {file_path} to {dest_folder}. Error: {e}")
            return None
            
    def verify(self, files):
        problems = []
        for file in files:
            if file not in self.verified or self.verified[file] == "Error":
                problems.append((file, f"{"Not found" if file not in self.verified else "Error"}"))
        
        return problems
        
    def rename_file(self, file_path, new_name):
        try:
            dir_name = os.path.dirname(file_path)
            ext = os.path.splitext(file_path)[1]
            new_path = os.path.join(dir_name, f"{new_name}")
            os.rename(file_path, new_path)
            return new_path
        
        except Exception as e:
            print(f"failed to rename {file_path} to {new_name}. Error: {e}")
            return file_path
    
    def swap_files(self, path1, path2):
        """
        Swap two files, renaming them if a collision exists (e.g., the same name already exists in the destination folder).
        
        Args:
            path1 (str): Full path to the first file.
            path2 (str): Full path to the second file.
        """

        try:

            if not (os.path.isfile(path1) and os.path.isfile(path2)):
                raise ValueError("Both files must exist.")
        
            # Get the directories and file names
            dir1, name1 = os.path.split(path1)
            dir2, name2 = os.path.split(path2)

            # Step 1: Handle renaming the destination if the names are the same
            new_path1 = os.path.join(dir2, name1)  # Target location for path1 (which is in path2's folder)
            new_path2 = os.path.join(dir1, name2)  # Target location for path2 (which is in path1's folder)

            # cleanly swap them if they have the same name
            if new_path1 == path2:
                temp_name = os.path.join(os.path.dirname(path1), "temp_swap_file")
                os.rename(path1, temp_name)
                first_file_name = os.path.basename(path1)
                second_file_name = os.path.basename(path2)
                first_file_new_path = os.path.join(os.path.dirname(path2), first_file_name)
                second_file_new_path = os.path.join(os.path.dirname(path1), second_file_name)
                os.rename(path2, second_file_new_path)
                os.rename(temp_name, first_file_new_path)
            
            # if name is different, make sure there are no other files with the same name
            else:
                if os.path.exists(new_path1):
                    new_path1 = self.safe_rename(new_path1)  # Rename if a conflict occurs

                if os.path.exists(new_path2):
                    new_path2 = self.safe_rename(new_path2)  # Rename if a conflict occurs

                # Step 2: Swap files
                shutil.move(path1, new_path1)  # Move path1 to path2's folder (possibly renamed)
                shutil.move(path2, new_path2)  # Move path2 to path1's folder (possibly renamed)

            return new_path1, new_path2
        
        except Exception as e:
            print(f"failed to swap {path1} and {path2}. Error: {e}")
            return None
            
    def safe_rename(self, destination):
        """
        Generate a safe file name by adding (1), (2), etc., if the file already exists.
        """
        base_name, ext = os.path.splitext(destination)
        counter = 1
        new_destination = destination

        while os.path.exists(new_destination):
            new_destination = f"{base_name}({counter}){ext}"
            counter += 1

        return new_destination


##### SETTERS #####
    def set_json_files(self, json_files):
        self.json_files = json_files

    def set_destination_folders(self, folder1, folder2, folder3, data_handling, duplicate_handling, json_handling):
        if data_handling not in ["5"]:
            new_dest = os.path.normpath(os.path.join(folder3, "!New"))
            dupe_dest = os.path.normpath(os.path.join(folder3, "!Duplicate"))
            high_res_dupe_dest = os.path.normpath(os.path.join(folder3, "!Higher res duplicate"))
            unsorted_dest = os.path.normpath(os.path.join(folder3, "!Unsorted"))
            err_dest = os.path.normpath(os.path.join(folder3, "!Error"))

        else:
            new_dest = "new"
            dupe_dest = "dupe"
            unsorted_dest = "unsort"
            high_res_dupe_dest = "hrdupe"
            err_dest = "err"
        

        self.new_dest = new_dest
        self.dupe_dest = dupe_dest
        self.unsorted_dest = unsorted_dest
        self.high_res_dupe_dest = high_res_dupe_dest
        self.err_dest = err_dest
        self.existing_folder = folder1
        self.new_folder = folder2
        self.data_handling = data_handling
        self.duplicate_handling = duplicate_handling
        self.json_handling = json_handling

    def set_logger(self, logger):
        self.logger = logger

    def set_tracker(self, tracker):
        self.tracker = tracker
