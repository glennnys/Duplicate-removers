import threading
import cv2
from PIL import Image
import imagehash
import metadata_extractor as pe
import os
from collections import namedtuple
import random
import pickle
import math
from pathlib import Path
import shutil
import time

VPTreeNode = namedtuple('VPTreeNode', ['point', 'threshold', 'left', 'right'])
    
class HashStorage:
    def __init__(self, threshold=0.9, extract_meta=True, phash_res=8):
        self.threshold = threshold
        self.extract_meta = extract_meta

        self.reset()

        self.lock1 = threading.Lock()
        self.lock2 = threading.Lock()
        self.phash_res = phash_res
        self.data_handling = "1"


        if threshold == 1.0:
            self.advanced_comparison = False
        else:
            self.advanced_comparison = True

        self.real_threshold = math.ceil((phash_res**2) * (1-threshold))

    def reset(self):
        self.json_files = {}
        self.images = {}
        self.videos = {}
        self.new_images = {}
        self.new_videos = {}
        self.higher_res = []
        self.checked_nodes = 0

        self.verified = {}


    def save_items(self):
        if self.existing_folder == "": return
        serialized = []
        base_path = os.path.abspath(self.existing_folder)
        combination = self.images | self.videos
        for key, item in combination.items():
            path1 = os.path.abspath(key)
            if path1.startswith(base_path):
                if not isinstance(item[0], list):
                    serialized.append((key, str(item[0]), item[2]))
                else:
                    hashhex=[str(i) for i in item[0]]
                    serialized.append((key, hashhex, item[2]))

        filepath = os.path.join(self.existing_folder, "!prehashed.cache")
        with open(filepath, 'wb') as f: 
            pickle.dump(serialized, f)


    def load_items(self):
        self.reset()
        filepath = os.path.join(self.existing_folder, "!prehashed.cache")
        if not os.path.exists(filepath): return
        with open(filepath, 'rb') as f:
            serialized = pickle.load(f)

        for path, hash_str, res in serialized:
            if os.path.exists(path):
                if isinstance(hash_str, list):
                    hash_val = [imagehash.hex_to_hash(hash_st) for hash_st in hash_str]
                    self.videos[path] = [hash_val, None, res, False]
                else:
                    hash_val = imagehash.hex_to_hash(hash_str)
                    self.images[path] = [hash_val, None, res, False]

    
    def hash_image(self, image, is_new=False):
        start = time.time()
        if image not in self.images:
            item = self.get_image_hash(image)
            if is_new:
                with self.lock1:
                    self.new_images[image] = item
            with self.lock1:
                self.images[image] = item
        self.logger.add_time(time.time()-start, "Hash image")

    def hash_video(self, video, is_new=False):
        start = time.time()
        if video not in self.videos:
            item = self.get_video_hashes(video)
            if is_new:
                with self.lock2:
                    self.new_videos[video] = item
            with self.lock2:
                self.videos[video] = item
        self.logger.add_time(time.time()-start, "Hash video")
                

    def build_image_tree(self):
        start = time.time()
        self.image_tree = self.build_vptree(self.images)
        self.logger.add_time(time.time()-start, "Build image tree")

    def build_video_tree(self):
        start = time.time()
        self.video_tree = self.build_vptree(self.videos)
        self.logger.add_time(time.time()-start, "Build video tree")

    ### This function is used to calculate the similarity between two hashes
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
        
    
    def disable_node(self, path, is_image=True):
        if is_image:
            self.new_images[path][3] = True
        else:
            self.new_videos[path][3] = True
    

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
        if result is None:
            try:
                query_hash, _, query_dims, _ = new_items[query_path]
                query_res = query_dims[0] * query_dims[1]
                result = (query_res, None, query_path, "new")
            except:
                result = (0, None, query_path, "new")
        if tree is None or result[3] == "low-res duplicate":
            return result
        
        self.checked_nodes += 1
        
        try:
            query_hash, _, query_dims, _ = new_items[query_path]
            query_res = query_dims[0] * query_dims[1]
            
            candidate_path = tree.point
            candidate_hash, _, candidate_dims, _ = items[candidate_path]

            candidate_res = candidate_dims[0] * candidate_dims[1]
        except:
            #skip nodes with problems
            return result

        if candidate_path != query_path:
            d = self.hamming_distance(query_hash, candidate_hash)

            if d < self.real_threshold:
                # If candidate is from old folder
                if self.is_in_path(candidate_path, self.existing_folder) and self.existing_folder != "":
                    if candidate_res >= query_res:
                        return (candidate_res, candidate_path, query_path, "low-res duplicate")
                    else:
                        result = (query_res, candidate_path, query_path, "high-res duplicate")

                else:
                    if candidate_res > query_res:
                        return (candidate_res, result[1], query_path, "low-res duplicate")
                    else:
                        if query_res > candidate_res or self.alpha_sort(query_path, candidate_path) == query_path:
                            if result[3] == "high-res duplicate":
                                result = (query_res, result[1], query_path, result[3])
                            else:
                                result = (query_res, result[1], query_path, "best new duplicate")
                            self.disable_node(candidate_path, is_images)
                        else:
                            return (candidate_res, candidate_path, query_path, "low-res duplicate")

        d = self.hamming_distance(query_hash, candidate_hash)

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
    

    def alpha_sort(self, a, b):
        if len(a) > len(b):
            return b
        elif len(b) > len(a):
            return a
        elif a > b:
            return b
        else:
            return a


    def get_image_hash(self, image_path):
        if os.path.getsize(image_path) == 0:
            return [None, None, (0, 0), False]
        #if self.advanced_comparison:
        try:
            image = Image.open(image_path)
            try: 
                return [imagehash.phash(image), None, self.get_image_size(image), False]
            except:
                image.thumbnail((1000, 1000))
                return [imagehash.phash(image), None, self.get_image_size(image), False]
        except:
            im = Image.new(mode="RGB", size=(200, 200))
            return [imagehash.phash(im), None, (0, 0), False]


    def get_video_hashes(self, video_path, frame_interval=24, max_hashes=5):
        if os.path.getsize(video_path) == 0:
            return [None, None, (0, 0), False]
        #if self.advanced_comparison:
        try:
            cap = cv2.VideoCapture(video_path)
            video_hashes = []
            frame_count = 0
            hash_count = 0
            while cap.isOpened() and hash_count < max_hashes:
                ret, frame = cap.read()
                if not ret:
                    break
                if frame_count % frame_interval == 0:
                    frame = Image.fromarray(frame)

                    video_hashes.append(imagehash.phash(frame))
                    hash_count += 1
                
                frame_count += 1

            item = [video_hashes, None, self.get_video_size(cap), False]

            if cap.isOpened():
                cap.release()
        
        except:
            im = Image.new(mode="RGB", size=(200, 200))
            video_hashes = [imagehash.phash(im) for i in range(max_hashes)]
            item = [video_hashes, None, (0, 0), False]
    
        return item


    
    #### This function is used to check if the image is duplicate or not
    def check_duplicates(self, item, items, new_items):
        start1 = time.time()
        if item[1][0] is None:
            result = (item[0], None, None, 'error')
        elif item[1][3]:
            result = (item[0], None, None, 'low-res duplicate')
        else:
            if type(item[1][0]) != list:
                start2 = time.time()            
                result = self.search_vptree(self.image_tree, item[0], items, new_items)
                self.logger.add_time(time.time()-start2, "Search images")
            else:
                start2 = time.time() 
                result = self.search_vptree(self.video_tree, item[0], items, new_items, is_images=False)
                self.logger.add_time(time.time()-start2, "Search videos")
      
        
        if result is None:
            destination = self.err_dest

        elif result[3] == "low-res duplicate":
            destination = self.dupe_dest

        elif result[3] in ["best new duplicate", "new"]:
            destination = self.new_dest

        elif result[3] == "high-res duplicate":
            destination = self.high_res_dupe_dest
        else:
            destination = self.err_dest

        self.logger.add_time(time.time()-start1, "Searching duplicates")
        
        start = time.time()
        dest_file = self.copy_file(item[0], None, destination)
        self.logger.add_time(time.time()-start, "Copy item")

        if result[3] == "high-res duplicate" and dest_file is not None:
            self.higher_res.append((result[1], dest_file))

    def is_in_path(self, file_path, base_path):
        file_path = Path(file_path).resolve()
        base_path = Path(base_path).resolve()
        return base_path in file_path.parents
    

    ### This function is used to copy the file to the destination folder and perform the metadata extraction
    def copy_file(self, file_path, file, dest_folder, higher_res=False):
        # if duplicates shouldn't be moved, return
        if self.data_handling in ["2", "4"] and dest_folder != self.new_dest and not higher_res: 
            self.verified[file_path] = ("No action", dest_folder)
            return

        if self.data_handling in ["6"] and dest_folder not in [self.dupe_dest, self.high_res_dupe_dest]: 
            self.verified[file_path] = ("No action", dest_folder)
            return

        if self.data_handling in ["5"] and not higher_res:
            if dest_folder != self.new_dest:
                # if it is a duplicate. remove it
                try:
                    os.remove(file_path)
                    return file_path
                
                except Exception as e:
                    self.logger.add_error(file_path, e)
                    return None
            else:
                if self.extract_meta:
                    pe.process_file(file_path, file_path, self.json_files, self.logger, file, self.json_handling)
                
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
                pe.process_file(dest_path, file_path, self.json_files, self.logger, file, self.json_handling)

            #verification
            if os.path.exists(dest_path) and (os.path.getsize(dest_path)>0 or dest_folder == self.err_dest):
                self.verified[file_path] = ("Moved or Copied", dest_path) 
            else:
                self.verified[file_path] = ("Error", dest_path)

            return dest_path
    
        except Exception as e:
            self.logger.add_error(file_path, e)
            self.verified[file_path] = ("Error", dest_path)
            return None
        
        
    def verify(self, files):
        problems = []
        for file in files:
            if file not in self.verified or self.verified[file] == "Error":
                problems.append(file)
        
        return problems
        

    def rename_file(self, file_path, new_name):
        dir_name = os.path.dirname(file_path)
        ext = os.path.splitext(file_path)[1]
        new_path = os.path.join(dir_name, f"{new_name}")
        os.rename(file_path, new_path)
        return new_path
    

    def swap_files(self, path1, path2):
        """
        Swap two files, renaming them if a collision exists (e.g., the same name already exists in the destination folder).
        
        Args:
            path1 (str): Full path to the first file.
            path2 (str): Full path to the second file.
        """
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

    
    def set_json_files(self, json_files):
        self.json_files = json_files

    def set_destination_folders(self, folder1, folder2, folder3, data_handling, json_handling):
        if data_handling not in ["5"]:
            new_dest = os.path.normpath(os.path.join(folder3, "!New"))
            dupe_dest = os.path.normpath(os.path.join(folder3, "!Duplicate"))
            high_res_dupe_dest = os.path.normpath(os.path.join(folder3, "!Higher res duplicate"))
            err_dest = os.path.normpath(os.path.join(folder3, "!Error"))

        else:
            new_dest = "new"
            dupe_dest = "dupe"
            high_res_dupe_dest = "hrdupe"
            err_dest = "err"
        

        self.new_dest = new_dest
        self.dupe_dest = dupe_dest
        self.high_res_dupe_dest = high_res_dupe_dest
        self.err_dest = err_dest
        self.existing_folder = folder1
        self.new_folder = folder2
        self.data_handling = data_handling
        self.json_handling = json_handling

    def set_logger(self, logger):
        self.logger = logger