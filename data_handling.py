import cv2
from PIL import Image, ImageFile
import imagehash
import metadata_extractor as pe
import os
from collections import defaultdict
import random
import pickle
import math
from pathlib import Path
import shutil
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from skimage.metrics import structural_similarity as ssim
import logkeeper as lk
import data_storage as ds
from helper_functions import *
from functools import partial

Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True
    
class HashHandler:
    def __init__(self, stop_event, enable_threading=True, threshold=0.9, extract_meta=True, phash_res=8, logger=None, tracker=None):
        self.stop_event = stop_event
        self.enable_threading=enable_threading
        self.threshold = threshold
        self.adv_threshold = threshold/2
        self.extract_meta = extract_meta

        self.image_extensions = [".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"]
        self.video_extensions = [".mp4", ".mov", ".avi", ".mkv", ".m4v"]

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

        self.storage_unit = ds.DataStorage(tracker, logger, stop_event, threshold=self.real_threshold)

    def reset(self):
        self.json_files = {}
        self.old_images = ds.ItemCollection()
        self.old_videos = ds.ItemCollection()
        self.new_images = ds.ItemCollection()
        self.new_videos = ds.ItemCollection()
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

        combination = self.old_images + self.old_videos + self.new_images + self.new_videos

        #existing files
        for item in combination:
            try:
                if not isinstance(item.hash, list):
                    serialized.append((item.path, str(item.hash), item.size))
                else:
                    hashhex=[str(i) for i in item.hash]
                    serialized.append((item.path, hashhex, item.size))
            
            except Exception as e:
                print(f"Couldn't save {item.path}. Reason: {e}")
        
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

                image_partial = partial(advanced_compare_images, threshold=self.adv_threshold)
                video_partial = partial(advanced_compare_videos, threshold=self.adv_threshold)
                for data in serialized[1:]:
                    path = data[0]
                    hash_str = data[1]
                    size = data[2]
                    if not isinstance(path, str): 
                        continue
                    try:
                        if os.path.exists(path) and os.path.getsize(path) == size:

                            if path.startswith(self.existing_folder):
                                if isinstance(hash_str, list):
                                    hash_val = [imagehash.hex_to_hash(hash_st) for hash_st in hash_str]
                                    item = ds.Item(path, get_video_hashes, video_hamming_distance, adv_comp_func=video_partial, update_result_func=self.item_update, item_hash=hash_val)
                                    self.old_videos.append(item)

                                else:
                                    hash_val = imagehash.hex_to_hash(hash_str)
                                    item = ds.Item(path, get_image_hash, image_hamming_distance, adv_comp_func=image_partial, update_result_func=self.item_update, item_hash=hash_val)
                                    self.old_images.append(item)

                            elif path in new_files:
                                if isinstance(hash_str, list):
                                    hash_val = [imagehash.hex_to_hash(hash_st) for hash_st in hash_str]
                                    item = ds.Item(path, get_video_hashes, video_hamming_distance, adv_comp_func=video_partial, update_result_func=self.item_update, item_hash=hash_val)
                                    self.new_videos.append(item)
                                    
                                else:
                                    hash_val = imagehash.hex_to_hash(hash_str)
                                    item = ds.Item(path, get_image_hash, image_hamming_distance, adv_comp_func=image_partial, update_result_func=self.item_update, item_hash=hash_val)
                                    self.new_images.append(item)

                    except Exception as e:
                        print(f"Couldn't load {path}. Reason: {e}")  
 
                break

##### DO THE THING #####
    def check_if_previously_processed(self, file):
        rel_path = os.path.relpath(file, self.new_folder)
        destination_folders = [self.new_dest, self.unsorted_dest, self.dupe_dest, self.high_res_dupe_dest, self.err_dest]
        possible_destinations = [os.path.join(destination_folder, rel_path) for destination_folder in destination_folders]

        for possible_dest in possible_destinations:
            if os.path.exists(possible_dest) and bytewise_compare_files(file, possible_dest):
                self.verified[file] = ("Already done", possible_dest)
                return True
        
        return False

    def fast_search(self, original_files, new_files, process_name="fast searching files", only_compare_to_originals=True):
        original_size_dict = defaultdict(list)
        new_size_dict = defaultdict(list)
        new_files_for_deeper_check = []
        simple_check = self.threshold == 1
        
        # Store as lists (mutable): [name, is_new, hash, is_duplicate]
        for file in original_files:
            original_size_dict[os.path.getsize(file)].append([file, False, None, False])

        for file in new_files:
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

        def fast_compare(file1, file2):
            if file1[0] == file2[0]:
                return False
            if file1[3]:
                return True
            
            if file1[2] is None:
                file1[2] = file_hash(file1[0])[file1[0]]
            if file2[2] is None:
                file2[2] = file_hash(file2[0])[file2[0]]

            if file1[2] == file2[2]:          
                if not file2[1]:
                    file1[3] = True
                    return True
                
                elif alpha_sort(file1[0], file2[0]) == file1[0]:
                    file2[3] = True
                    return False

                else:
                    file1[3] = True
                    return True
            
            return False

        def check_file(new_file, file_range):
                if self.check_if_previously_processed(new_file[0]): return
                for other_file in file_range:
                    if self.stop_event.is_set():
                        return None
                    if fast_compare(new_file, other_file):
                        if bytewise_compare_files(new_file[0], other_file[0]):
                            item = ds.Item(new_file[0], None, None, None, None)
                            item.result = "low-res duplicate"
                            item.relevant_dupes.append(other_file[0])
                            self.handle_result(item)
                            return
                
                if simple_check:
                    item = ds.Item(new_file[0], None, None, None, None)
                    item.result = "new"
                    self.handle_result(item)

                else:
                    return new_file[0]

            
        results = self.run_task(function=check_file, args=args, process_name=process_name, allow_saving=False)
        if self.stop_event.is_set(): return 
        for result in results:
            if result is not None: new_files_for_deeper_check.append(result)

        return new_files_for_deeper_check
        

    def get_names_index(self, old_items):
        name_indexes = defaultdict(list)
        def make_indexes(item):
            name_indexes[item.name].append(item)
        
        self.run_task(make_indexes, old_items, "indexing names", allow_saving=False)

        return name_indexes

    def precheck_name(self, item, name_indexes):
        matches = name_indexes.get(os.path.basename(item.name), [])
    
        for match in matches:
            if item.dist_func(item.hash, match.hash) < self.real_threshold and item.size <= match.size:
                item.finished = True
                item.result = "low-res duplicate"
                break           

    def check_duplicates(self, files1, files2):
        if not isinstance(files1, (list, tuple)):
            raise TypeError(f"Expected list, got {type(files1).__name__}")
        if not isinstance(files2, (list, tuple)):
            raise TypeError(f"Expected list or tuple, got {type(files2).__name__}")
        
        def filter_file(file, files, extensions, exclude=False):
            if exclude:
                if os.path.splitext(file)[1].lower() not in extensions: 
                    files.append(file)
            else:
                if os.path.splitext(file)[1].lower() in extensions: 
                    files.append(file)


        start = time.time()
        self.reset()
        # Separate images and videos from other files for folder 1
        images1 = []
        self.run_task(process_name="filter images", function=filter_file, args=[(file, images1, self.image_extensions) for file in files1])
        videos1 = []
        self.run_task(process_name="filter videos", function=filter_file, args=[(file, videos1, self.video_extensions) for file in files1])
        remaining_files1 = []
        self.run_task(process_name="filter remaining files", function=filter_file, args=[(file, remaining_files1, self.image_extensions+self.video_extensions, True) for file in files1])
        if self.stop_event.is_set(): return

        if len(images1) + len(videos1) + len(remaining_files1) != len(files1):
            print("Error filtering old files")
            return
        
        # Separate images and videos from other files for folder 2
        images2 = []
        self.run_task(process_name="filter images", function=filter_file, args=[(file, images2, self.image_extensions) for file in files2])
        videos2 = []
        self.run_task(process_name="filter videos", function=filter_file, args=[(file, videos2, self.video_extensions) for file in files2])
        jsons = [file for file in files2 if os.path.splitext(file)[1].lower() in [".json"]]
        jsons_dict = {}

        for file_path in jsons:
            with open(file_path, 'r') as file:
                data = json.load(file)
                jsons_dict[os.path.join(os.path.dirname(file_path), data['title'])] = os.path.abspath(file_path)

        remaining_files2 = [file for file in files2 if file not in images2 and file not in videos2 and file not in jsons]
        if self.stop_event.is_set(): return

        if len(images2) + len(videos2) + len(jsons) + len(remaining_files2) != len(files2):
            print("Error filtering new files")
            return

        self.logger.add_time(time.time()-start, "Setup")

        random.shuffle(remaining_files2)
        results = self.fast_search(remaining_files1, remaining_files2, process_name="comparing non-image and video files", only_compare_to_originals=True)
        if self.stop_event.is_set(): return
        for result in results:
            if result is not None:
                item = ds.Item(result, None, None, None, None)
                item.result = ""
                self.handle_result(item)

        simple_search = self.threshold == 1
        
        potentially_new_images = []
        if len(images2) > 0:
            random.shuffle(images2)
            results = self.fast_search(images1, images2, process_name="fast searching images", only_compare_to_originals=False)
            if self.stop_event.is_set(): return
            
            for result in results:
                if result is not None:
                    if not simple_search:   
                        potentially_new_images.append(result)

        potentially_new_videos = []
        if len(videos2) > 0:
            random.shuffle(videos2)
            results = self.fast_search(videos1, videos2, process_name="fast searching videos", only_compare_to_originals=False)
            if self.stop_event.is_set(): return
            for result in results:
                if result is not None:
                    if not simple_search:
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
                name_indexes = self.get_names_index(self.old_images.items)
                self.run_task(process_name="prechecking image names", function=self.precheck_name, args=[(item, name_indexes) for item in self.new_images], allow_saving=False)
                if self.stop_event.is_set(): return
                self.storage_unit.reset()
                tree = self.storage_unit.build_tree(self.old_images.items + self.new_images.items)
                if self.stop_event.is_set(): return
                self.run_task(process_name="finding duplicate images", function=self.storage_unit.search_vptree, args=[(image, tree) for image in self.new_images])
                if self.stop_event.is_set(): return
                self.run_task(process_name="copying, moving or removing processed images", function=self.handle_result, args=self.new_images.items)
                if self.stop_event.is_set(): return
                self.checked_nodes += self.storage_unit.checked_nodes

            if len(potentially_new_videos) > 0:    
                random.shuffle(potentially_new_videos)
                self.run_task(process_name="Hashing old videos", function=self.hash_video, args=[(video, False) for video in videos1])
                if self.stop_event.is_set(): return
                self.run_task(process_name="Hashing new videos", function=self.hash_video, args=[(video, True) for video in potentially_new_videos])
                if self.stop_event.is_set(): return
                self.save_items()
                name_indexes = self.get_names_index(self.old_videos)
                self.run_task(process_name="prechecking video names", function=self.precheck_name, args=[(item, name_indexes) for item in self.new_videos], allow_saving=False)
                if self.stop_event.is_set(): return
                self.storage_unit.reset()
                tree = self.storage_unit.build_tree(self.old_videos.items + self.new_videos.items)
                if self.stop_event.is_set(): return
                self.run_task(process_name="finding duplicate videos", function=self.storage_unit.search_vptree, args=[(video, tree) for video in self.new_videos])
                if self.stop_event.is_set(): return
                self.run_task(process_name="copying, moving or removing processed videos", function=self.handle_result, args=self.new_videos.items)
                if self.stop_event.is_set(): return
                self.checked_nodes += self.storage_unit.checked_nodes
            
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
        
    def handle_result(self, item:ds.Item): 
        if item is None:
            return

        elif item.result == "low-res duplicate":
            destination = self.dupe_dest
        
        elif item.result in ["best new duplicate", "new"]:
            destination = self.new_dest

        elif item.result == "high-res duplicate":
            destination = self.high_res_dupe_dest
            
        elif item.result == "error":
            destination = self.err_dest

        else:
            destination = self.unsorted_dest
        
        # copy file handles different destination based on data handling settings
        start = time.time()
        dest_file = self.copy_file(item.path, destination)
        self.logger.add_time(time.time()-start, "Copy item")
        
        # to open window for comparison
        if self.duplicate_handling != "3" and item.result == "high-res duplicate" and dest_file is not None:
            self.higher_res_to_compare.append((item.path, item.relevant_dupes[0]))

        if destination == self.dupe_dest and self.duplicate_handling == "2":
            self.duplicates_to_compare.append((item.path, item.relevant_dupes[0])) 


##### PSEUDO-HELPERS #####
    def hash_image(self, image, is_new=False):
        start = time.time()
        
        image_partial = partial(advanced_compare_images, threshold=self.adv_threshold)
    
        if is_new:
            if image not in self.new_images:
                item = ds.Item(image, get_image_hash, image_hamming_distance, adv_comp_func=image_partial, update_result_func=self.item_update)
                self.new_images.append(item)
        else:
            if image not in self.old_images:
                item = ds.Item(image, get_image_hash, image_hamming_distance, adv_comp_func=image_partial, update_result_func=self.item_update)
                self.old_images.append(item)

        self.logger.add_time(time.time()-start, "Hash image")

    def hash_video(self, video, is_new=False):
        start = time.time()

        video_partial = partial(advanced_compare_videos, threshold=self.adv_threshold)

        if is_new:
            if video not in self.new_videos:
                item = ds.Item(video, get_video_hashes, video_hamming_distance, adv_comp_func=video_partial, update_result_func=self.item_update)
                self.new_videos.append(item)

        else:
            if video not in self.old_videos:  
                item = ds.Item(video, get_video_hashes, video_hamming_distance, adv_comp_func=video_partial, update_result_func=self.item_update)
                self.old_videos.append(item)

        self.logger.add_time(time.time()-start, "Hash video")      
   

    def item_update(self, item: ds.Item, other: ds.Item):
        """Assumes these two items were already destined to be duplicates"""
        if is_in_path(other.path, self.existing_folder) and self.existing_folder != "": #if item is new but other isn't

            if other >= item:   # item is lower or same resolution
                with item.lock:
                    item.finished = True
                    item.result = "low-res duplicate"

            else:   # potentially a duplicate with higher resolution
                with item.lock:
                    item.result = "high-res duplicate"

        else:   # both items are new

            if other > item: # item is lower resolution
                self.item_update(other, item)       

            else:

                if item > other or alpha_sort(item.path, other.path) == item.path: # item is higher resolution or has a more favorable name

                    if item.result != "high-res duplicate": # if no duplicates in original files found, make it the best new duplicate
                        with item.lock:
                            item.result = "best new duplicate"                   

                    # disable the other item from being as it is already known to be worse
                    with other.lock:
                        other.finished = True
                        other.result = "low-res duplicate"

                    other.relevant_dupes.append(item.path)

                else:
                    self.item_update(other, item)

        with item.lock:    
            item.relevant_dupes.append(other.path)

##### HELPERS #####        
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
            return file_path


        if self.data_handling in ["5"] and not higher_res:
            if dest_folder not in [self.unsorted_dest, self.new_dest]:
                # if it is a duplicate. remove it
                try:
                    os.remove(file_path)
                    self.verified[file_path] = ("Removed", dest_folder)
                    return None
                
                except Exception as e:
                    print(f"Unable to remove {file_path}. Reason: {e}")
                    self.verified[file_path] = ("Error", dest_folder)
                    return file_path
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
