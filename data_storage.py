from helper_functions import *
import random
from collections import namedtuple, defaultdict
import threading

VPTreeNode = namedtuple('VPTreeNode', ['point', 'threshold', 'left', 'right'])

class Item:
    def __init__(self, path, hash_func, dist_func, adv_comp_func, update_result_func, end_func, item_hash=None):
        self.path = path
        self.name = os.path.basename(path)
        self.size = os.path.getsize(path)
        self.hash = item_hash if item_hash or not hash_func else hash_func(path)
        self.default_hash = hash_func(None) if hash_func else None
        self.relevant_dupes = []
        self.dest = None
        self.lock = threading.Lock()
        
        self.result = "new"
        self.finished = self.hash == self.default_hash

        self.dist_func = dist_func
        self.adv_comp_func = adv_comp_func
        self.update_result_func = update_result_func
        self.end_func = end_func

    def same_item(self, other) -> bool:
        return isinstance(other, Item) and self.path == other.path

    def __lt__(self, other):
        if isinstance(other, Item):
            return self.size < other.size
        return NotImplemented

    def __le__(self, other):
        if isinstance(other, Item):
            return self.size <= other.size
        return NotImplemented

    def __eq__(self, other):
        if isinstance(other, Item):
            return self.size == other.size
        return NotImplemented

    def __ge__(self, other):
        if isinstance(other, Item):
            return self.size >= other.size
        return NotImplemented

    def __gt__(self, other):
        if isinstance(other, Item):
            return self.size > other.size
        return NotImplemented

    def __ne__(self, other):
        if isinstance(other, Item):
            return self.size != other.size
        return NotImplemented


class ItemCollection:
    def __init__(self, items=None):
        self.items = []
        self.path_index = defaultdict(list)
        if items:
            for item in items:
                self.append(item)

    def append(self, item: Item):
        self.items.append(item)
        self.path_index[item.path].append(item)
        

    # Combine into a new collection (a + b)
    def __add__(self, other):
        if not isinstance(other, ItemCollection):
            return NotImplemented
        return ItemCollection(self.items + other.items)

    # In-place addition (a += b)
    def __iadd__(self, other):
        if not isinstance(other, ItemCollection):
            return NotImplemented

        for item in other.items:
            self.append(item)  # add items from other
        return self
    
    def __iter__(self):
        return iter(self.items)
    
    def __contains__(self, item):
        if isinstance(item, Item):
            return bool(self.path_index.get(item.path))
        # optionally support string lookup by path
        if isinstance(item, str):
            return item in self.path_index
        return False
    
    def __repr__(self):
        return self.items
    
    def __getitem__(self, i):
        if isinstance(i, int):
            return self.items[i]
        if isinstance(i, str):
            return self.path_index.get(i, [])
    
    def __len__(self):
        return len(self.items)

class DataStorage:
    def __init__(self, tracker, logger, stop_event, pause_event=None, threshold=1.0):
        self.tracker = tracker
        self.logger = logger
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.threshold = threshold

        self.reset()

    def reset(self):
        self.tree = None
        self.checked_nodes = 0


    def build_tree(self, items: list[Item]):
        if not all(isinstance(n, Item) for n in items):
            raise TypeError("All items must be the unique type Item from this library")
        self.tree = self.__build_vptree(items)
        return self.tree

   
    def __build_vptree(self, items):
        if not items:
            return None

        vantage_idx = random.randint(0, len(items) - 1)
        vantage_item = items[vantage_idx]
        rest = items[:vantage_idx] + items[vantage_idx+1:]

        if not rest:
            return VPTreeNode(point=vantage_item, threshold=0, left=None, right=None)

        distances = [(rest_item, vantage_item.dist_func(vantage_item.hash, rest_item.hash)) for rest_item in rest]
        distances.sort(key=lambda x: x[1])
        median = distances[len(distances) // 2][1]
        left_paths = [r for r, d in distances if d <= median]
        right_paths = [r for r, d in distances if d > median]

        return VPTreeNode(
            point=vantage_item,
            threshold=median,
            left=self.__build_vptree(left_paths),
            right=self.__build_vptree(right_paths)
        )


    def search_vptree(self, item: Item, tree: VPTreeNode):
        self.__search_vptree(item, tree)
        item.end_func(item)

    def __search_vptree(self, item: Item, tree: VPTreeNode): 
        if tree is None or item.finished: # end condition
            return
        
        self.checked_nodes += 1
        
        candidate = tree.point
      
        d = item.dist_func(item.hash, candidate.hash)

        if candidate.hash != candidate.default_hash and not item.same_item(candidate) and not candidate.path in item.relevant_dupes:
            if d < self.threshold:
                if item.adv_comp_func(item.path, candidate.path):
                    item.update_result_func(item, candidate)

        if item.finished: return

        go_left = d - self.threshold <= tree.threshold
        go_right = d + self.threshold >= tree.threshold

        if go_left and go_right:

            if d < tree.threshold:
                self.__search_vptree(item, tree.left)
                if item.finished: return
                self.__search_vptree(item, tree.right)
                if item.finished: return

            else:
                self.__search_vptree(item, tree.right)
                if item.finished: return
                self.__search_vptree(item, tree.left)
                if item.finished: return

        elif go_left:
            self.__search_vptree(item, tree.left)
            if item.finished: return

        elif go_right:
            self.__search_vptree(item, tree.right)
            if item.finished: return

        return    