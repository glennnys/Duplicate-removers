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

VPTreeNode = namedtuple('VPTreeNode', ['point', 'threshold', 'left', 'right'])
    
class HashStorage:
    def __init__(self, threshold=0.9, extract_meta=True, phash_res=8):
        self.threshold = threshold
        self.extract_meta = extract_meta

        self.og_path = ""
        self.new_dest = ""
        self.dupe_dest = ""
        self.err_dest = ""
        self.json_files = []
        self.lock1 = threading.Lock()
        self.lock2 = threading.Lock()
        self.phash_res = phash_res
        
        self.images = {}
        self.videos = {}
        self.new_images = {}
        self.new_videos = {}

        self.higher_res = []

        if threshold == 1.0:
            self.advanced_comparison = False
        else:
            self.advanced_comparison = True

        self.real_threshold = math.ceil((phash_res**2) * (1-threshold))


    #TODO: make this work
    def save_items(self, items, file_path):
        serialized = []
        for item in items:
            serialized.append((item[0], str(item[1])))

        with open(file_path, 'wb') as f:
            pickle.dump(serialized, f)


    def load_items(self, file_path):
        with open(file_path, 'rb') as f:
            serialized = pickle.load(f)

        restored = []
        for path, hash_str,in serialized:
            if isinstance(hash_str, tuple):
                hash_val = hash_str
            else:
                hash_val = imagehash.hex_to_hash(hash_str)
            restored.append((path, hash_val))

        return restored
    
    def hash_image(self, image, is_new=False):
        item = self.get_image_hash(image)
        if is_new:
            with self.lock1:
                self.new_images[image] = item
        with self.lock1:
            self.images[image] = item

    def hash_video(self, video, is_new=False):
        item = self.get_video_hashes(video)
        if is_new:
            with self.lock2:
                self.new_videos[video] = item
        with self.lock2:
            self.videos[video] = item
                

    def build_image_tree(self):
        self.image_tree = self.build_vptree(self.images)

    def build_video_tree(self):
        self.video_tree = self.build_vptree(self.videos)

    ### This function is used to calculate the similarity between two hashes
    def hamming_distance(self, h1, h2):
        if type(h1) == list and type(h2) == list:
            if len(h1) == 0 or len(h2) == 0:
                return self.phash_res**2
            sum = 0
            for i in range(min(len(h1), len(h2))):
                sum += self.hamming_distance(h1[i], h2[i])
            return sum // len(h1)
        
        return h1 - h2

    def get_image_size(self, image):
        if isinstance(image, Image.Image):
            return image.size
    

    def get_video_size(self, video):
        if isinstance(video, cv2.VideoCapture):
            return int(video.get(cv2.CAP_PROP_FRAME_WIDTH)), int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
    
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


    def search_vptree(self, tree, query_path, items, new_items, result=(0.0, None, None, None), is_images=True):
        if tree is None:
            return result
        
        query_hash, _, query_dims, _ = new_items[query_path]
        query_res = query_dims[0] * query_dims[1]
        
        candidate_path = tree.point
        candidate_hash, _, candidate_dims, _ = items[candidate_path]
        candidate_res = candidate_dims[0] * candidate_dims[1]

        if candidate_path != query_path:
            d = self.hamming_distance(query_hash, candidate_hash)

            if d < self.real_threshold:
                # If candidate is from old folder (not in self.og_path)
                if not self.is_in_path(candidate_path, self.og_path):
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

            else:
                if result[3] is None:
                    result = (query_res, result[1], query_path, "new")

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
                return [imagehash.phash(im), None, None, False]
        # else:
        #     hash_algo = hashlib.md5()
        #     with open(image_path, 'rb') as f:
        #         for chunk in iter(lambda: f.read(4096), b""):
        #             hash_algo.update(chunk)

        #     return [hash_algo.hexdigest(), None, self.get_image_size(image), False]


    def get_video_hashes(self, video_path, frame_interval=24, max_hashes=5):
        #if self.advanced_comparison:
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

            return item
        
        # else:
        #     hash_algo = hashlib.md5()
        #     with open(video_path, 'rb') as f:
        #         for chunk in iter(lambda: f.read(4096), b""):
        #             hash_algo.update(chunk)

        #     item = [hash_algo.hexdigest(), self.get_video_size(cap), False]

        #     return item

    
    #### This function is used to check if the image is duplicate or not
    def check_duplicates(self, item, items, new_items):
        if item[1][3]:
            result = (item[0], None, None, 'low-res duplicate')
        else:
            if type(item[1][0]) != list:            
                result = self.search_vptree(self.image_tree, item[0], items, new_items)
            else:
                result = self.search_vptree(self.video_tree, item[0], items, new_items, is_images=False)
      
        
        if result is None:
            destination = self.err_dest

        elif result[3] == "low-res duplicate":
            destination = self.dupe_dest

        elif result[3] in ["best new duplicate", "new"]:
            destination = self.new_dest

        elif result[3] == "high-res duplicate":
            destination = self.dupe_dest
        else:
            destination = self.err_dest
        
        dest_file = self.copy_file(item[0], None, destination)

        if result[3] == "high-res duplicate" and dest_file is not None:
            self.higher_res.append((result[1], dest_file))

    def is_in_path(self, file_path, base_path):
        file_path = Path(file_path).resolve()
        base_path = Path(base_path).resolve()
        return base_path in file_path.parents
    

    ### This function is used to copy the file to the destination folder and perform the metadata extraction
    def copy_file(self, file_path, file, dest_folder):
        #file_name = os.path.basename(file_path)
        file_name = os.path.relpath(file_path, start=self.og_path)
        dest_path = os.path.join(dest_folder, file_name)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        i = 1
        # if the same file name already exists, add a number at the end
        while os.path.exists(dest_path):
            file_name, ext = os.path.splitext(file_name)
            if file_name.endswith(")"):
                file_name = file_name[:file_name.rfind("(")].strip()
            file_name = f"{file_name}({i}){ext}"
            dest_path = os.path.join(dest_folder, file_name)
            i += 1

        try:
            os.makedirs(dest_folder, exist_ok=True)
            with open(file_path, 'rb') as src, open(dest_path, 'wb') as dest:
                dest.write(src.read())

            if self.extract_meta and dest_folder == self.new_dest:
                pe.process_file(dest_path, file_path, self.json_files, file)

            return dest_path
        except Exception as e:
            print(e)
            return None

    def rename_file(self, file_path, new_name):
        dir_name = os.path.dirname(file_path)
        ext = os.path.splitext(file_path)[1]
        new_path = os.path.join(dir_name, f"{new_name}")
        os.rename(file_path, new_path)
        return new_path
    
    def set_json_files(self, json_files):
        self.json_files = json_files

    def set_destination_folders(self, new_dest, dupe_dest, err_dest, og_path):
        self.new_dest = new_dest
        self.dupe_dest = dupe_dest
        self.err_dest = err_dest
        self.og_path = og_path