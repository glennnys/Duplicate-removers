import os
import ctypes
import time
from PIL import Image as PILImage
from PIL import ImageTk, ExifTags  # Ensure PIL.Image is imported for image operations
import pillow_heif
from concurrent.futures import ThreadPoolExecutor
import threading
from tkinter import *
from tkinter import ttk
from tkinter import filedialog
from tkinter import messagebox
from tkinter.font import Font
import sv_ttk
import storage_class as fs
import platform
import cv2
import json
import logkeeper
import math
import hashlib

pillow_heif.register_heif_opener()

################################## Helper functions ##################################
def show_comparison_dialog(window, paths, logger, stop_event):
    global processing_complete

    if stop_event.is_set():
        return

    def select_all(value=1):
         for i in range(len(selection_vars)):
              selection_vars[i].set(value)

    def prev_next(value=0):
        nonlocal active_page
        nonlocal root
        nonlocal loaded_pages

        if stop_event.is_set():
            return
        
        # Determine the target page
        target_page = active_page
        if value == 0 and active_page > 0:
            target_page -= 1
        elif value == 1 and active_page < page_count - 1:
            target_page += 1# No more pages in that direction

        # If the page is loading, retry after a short delay
        if loading_pages[target_page]:
            root.after(200, lambda: prev_next(value))
            return

        # If the page hasn't been loaded, start loading it
        if not loaded_pages[target_page]:
            load_page_images(target_page)
            root.after(200, lambda: prev_next(value))  # Retry once loading starts
            return

        # Page is ready — switch to it
        if target_page != active_page:
            pages[active_page].pack_forget()
            active_page = target_page
            pages[active_page].pack(fill=BOTH, side=LEFT, expand=True)     
            page_counter.config(text=f"Page {active_page+1}/{page_count}")

        root.update()
        scrollbar.config(command=pages[active_page].yview)
        pages[active_page].configure(scrollregion=pages[active_page].bbox("all"))
        pages[active_page].configure(width=mainframes[active_page].winfo_width())

        # start the canvas at the top
        pages[active_page].yview_moveto(0)
        pages[active_page].xview_moveto(0)

    def resize_with_orientation(img, max_width):
        # Step 1: Apply EXIF orientation
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

        # Step 2: Resize
        size = img.size
        new_size = (max_width, int(size[1]*(max_width/size[0])))
        img = img.resize(new_size)

        return img
        
         
    def on_closing(save_selection=False):
        for i in range(len(selection_vars)):
            if save_selection:
                selection[i] = selection_vars[i].get()
            else:
                    selection[i] = 0

            # Unbind mousewheel to prevent callbacks after widget is destroyed
        if platform.system() == 'Windows' or platform.system() == 'MAC':
            root.unbind_all("<MouseWheel>")
        elif platform.system() == 'Linux':
            root.unbind_all("<Button-4>")
            root.unbind_all("<Button-5>")
        root.destroy()

    def enable_scroll():
        if platform.system() == 'Windows' or platform.system() == 'MAC':
            root.bind_all("<MouseWheel>", on_mousewheel_windows)
        elif platform.system() == 'Linux':
            root.bind_all("<Button-4>", on_mousewheel_linux_up)
            root.bind_all("<Button-5>", on_mousewheel_linux_down)


     ### canvas part ###
    def on_mousewheel_windows(event):   
        nonlocal active_page  
        pages[active_page].yview_scroll(int(-1*(event.delta/120)), "units")
    def on_mousewheel_linux_up(event):    
        nonlocal active_page
        pages[active_page].yview_scroll(-1, "units")
    def on_mousewheel_linux_down(event):  
        nonlocal active_page
        pages[active_page].yview_scroll(1, "units")

    if len(paths)>0:
        old_paths, new_paths = zip(*paths)
    else: return

    images_per_page = 30
    active_page = 0
    page_count = math.ceil(len(paths)/images_per_page)
    pages = [ttk.Frame()]*page_count
    mainframes = [ttk.Frame()]*page_count
    loaded_pages = [False]*page_count
    loading_pages = [False]*page_count
    images = [] # prevent garbage collection
    
    selection_vars = [IntVar(value=0) for _ in old_paths]
    selection = [0] * len(old_paths)
    root = Toplevel(window)  # Create a new window
    root.title("Duplicate Image Comparison")

    root.protocol("WM_DELETE_WINDOW", lambda: on_closing(False))

    enable_scroll()

    stationary_frame = ttk.Frame(root)
    stationary_frame.pack()

    label = ttk.Label(stationary_frame, text="Duplicates where the new one is a higher resolution than the old one.\nChoose which ones to replace the old ones.\nThe old one will be placed in the duplicates folder.", justify="center")
    label.pack(pady=10)

    buttons_frame = ttk.Frame(stationary_frame)
    buttons_frame.pack(pady=10)

    deselect_all_button = ttk.Button(buttons_frame, text="Select all old", command=lambda: select_all(0))
    select_all_button = ttk.Button(buttons_frame, text="Select all new", command=lambda: select_all(1))
    both_all_button = ttk.Button(buttons_frame, text="Select all both", command=lambda: select_all(2))
    confirm_button = ttk.Button(buttons_frame, text="Confirm", command=lambda: on_closing(True))
    deselect_all_button.pack(side='left',padx=10)
    select_all_button.pack(side='left',padx=10)
    both_all_button.pack(side='left',padx=10)
    confirm_button.pack(side='left',padx=10)

    next_prev_frame = ttk.Frame(stationary_frame)
    next_prev_frame.pack(pady=10)

    prev_button = ttk.Button(next_prev_frame, text="Previous", command=lambda: prev_next(0))
    next_button = ttk.Button(next_prev_frame, text="Next", command=lambda: prev_next(1))
    page_counter = ttk.Label(next_prev_frame, text=f"page 1/{page_count}")
    loading_label = ttk.Label(stationary_frame, text="")
    prev_button.pack(side='left',padx=10)
    next_button.pack(side='left',padx=10)
    page_counter.pack(side='left',padx=10)
    loading_label.place(x=500, y=145)

    label_frame = ttk.Frame(stationary_frame)
    label_frame.pack(pady=10)

    old_label = ttk.Label(label_frame, text="Old image, lower resolution")
    old_label.pack(side="left", padx=100)
    new_label = ttk.Label(label_frame, text="new image, higher resolution")
    new_label.pack(side="right", padx=100)

    canvas = ttk.Frame(root)
    canvas.pack(fill=Y, expand=True)
    scrollbar = ttk.Scrollbar(canvas,
                            orient=VERTICAL)
    scrollbar.pack(side=RIGHT, fill=Y)
    
    def load_page_images(page_index):
        nonlocal loaded_pages
        nonlocal loading_pages

        if loading_pages[page_index] or loaded_pages[page_index]:
            return

        loading_label.config(text=f"Loading page {page_index+1}")

        loading_pages[page_index] = True
        def background():
            # heavy lifting happens here
            prepared_widgets = []
            for i in range(page_index * images_per_page, min(len(old_paths), (page_index + 1) * images_per_page)):
                if stop_event.is_set():
                    return
                old_path = old_paths[i]
                new_path = new_paths[i]
                # your image loading and resizing logic
                # but store widget creation details in a list
                try:
                    # Handle image
                    img1 = PILImage.open(old_path)
                    res1 = img1.size
                    img1 = resize_with_orientation(img1, 400)
                    img2 = PILImage.open(new_path)
                    res2 = img2.size
                    img2 = resize_with_orientation(img2, 400)
                    
                except:
                    try:
                        # Handle video: extract the first frame as an image
                        video1 = cv2.VideoCapture(old_path)
                        video2 = cv2.VideoCapture(new_path)
                        success, frame1 = video1.read()
                        success, frame2 = video2.read()
                        video1.release()
                        video2.release()
                        if success:
                            img1 = PILImage.fromarray(cv2.cvtColor(frame1, cv2.COLOR_BGR2RGB))
                            res1 = img1.size
                            img1 = resize_with_orientation(img1, 400)
                            img2 = PILImage.fromarray(cv2.cvtColor(frame2, cv2.COLOR_BGR2RGB))
                            res2 = img2.size
                            img2= resize_with_orientation(img2, 400)
                        else:
                            img1 = PILImage.new("RGB", (400, 400), "black")  # Fallback to a blank image if frame extraction fails
                            res1 = img1.size
                            img2 = PILImage.new("RGB", (400, 400), "black")  # Fallback to a blank image if frame extraction fails
                            res2 = img2.size
                    except:
                        img1 = PILImage.new("RGB", (400, 400), "black")  # Fallback to a blank image if frame extraction fails
                        res1 = img1.size
                        img2 = PILImage.new("RGB", (400, 400), "black")  # Fallback to a blank image if frame extraction fails
                        res2 = img2.size
    

                tk_img1 = ImageTk.PhotoImage(img1)
                tk_img2 = ImageTk.PhotoImage(img2)

                images.append(tk_img1)
                images.append(tk_img2)
                prepared_widgets.append((tk_img1, tk_img2, res1, res2, old_path, new_path))  # Store relevant UI elements

            # Now schedule UI update on main thread
            root.after(0, display_page_images, page_index, prepared_widgets)

        threading.Thread(target=background).start()

    def display_page_images(page_index, widgets):
        nonlocal loaded_pages
        nonlocal loading_pages

        if stop_event.is_set():
            return
        # Actually create and pack widgets on the UI
        pages[page_index] = Canvas(canvas, takefocus=False, highlightthickness=0)
        pages[page_index].pack(fill=BOTH, side=LEFT, expand=True)

        pages[page_index].configure(yscrollcommand=scrollbar.set)

        # Mainframe
        mainframes[page_index] = ttk.Frame(pages[page_index], takefocus=False)
        mainframes[page_index].pack(fill=BOTH, side=LEFT)
        pages[page_index].create_window((0, 0), window=mainframes[page_index], anchor=NW)

        mainframes[page_index].bind("<FocusIn>", lambda e, a=page_index: pages[a].focus_set())

        i = page_index*images_per_page
        for tk_img1, tk_img2, res1, res2, old_path, new_path in widgets:
            selection_frame = ttk.Frame(mainframes[page_index])
            selection_frame.pack()
            
            # Check if the file is a video or an image
            
            left_frame = Frame(selection_frame)
            left_frame.pack(side="left")
            
            # Radiobutton for Old
            old_button = ttk.Radiobutton(
                left_frame,
                image=tk_img1,
                variable=selection_vars[i],
                value=0
            )
            old_button.pack(padx=10, pady=10)

            ttk.Label(left_frame, text=old_path, wraplength=400).pack(padx=20)
            ttk.Label(left_frame, text=f'resolution: ({res1[0]}x{res1[1]})').pack(padx=20)

            right_frame = Frame(selection_frame)
            right_frame.pack(side="left")

            # Radiobutton for New
            new_button = ttk.Radiobutton(
                right_frame,
                image=tk_img2,
                variable=selection_vars[i],
                value=1
            )
            new_button.pack(padx=10, pady=10)

            ttk.Label(right_frame, text=new_path, wraplength=400).pack(padx=20)
            ttk.Label(right_frame, text=f'resolution: ({res2[0]}x{res2[1]})').pack(padx=20)

            both_frame = Frame(selection_frame)
            both_frame.pack(side="left")
            
            # Radiobutton for True
            both_button = ttk.Radiobutton(
                both_frame,
                text="Both",
                variable=selection_vars[i],
                value=2
            )
            both_button.pack(padx=10, pady=10)

            i+=1

        if active_page != page_index:
            pages[page_index].pack_forget()
        
        loading_label.config(text=f"")
        loaded_pages[page_index] = True
        loading_pages[page_index] = False

        if page_index + 1 < page_count and not (loaded_pages[page_index + 1] or loading_pages[page_index + 1]):
            root.after(1000, lambda: load_page_images(page_index + 1))
         
    prev_next(0)

    root.wait_window()  # Wait for the dialog to close

    for result in zip(selection, old_paths, new_paths):
        if result[0] == 1:
            seen_hashes.swap_files(result[1], result[2])
        elif result[0] == 2:
            seen_hashes.copy_file(result[2], None, seen_hashes.new_dest, True)

    processing_complete.set()


################################## Main processing function ##################################
def process_folder(folder1_path, folder2_path, folder3_path, data_handling, json_handling, stop_event):
    global seen_hashes
    global processing_complete
    global progress
    global process
    global time_remaining
    global i
    global total

    logger = logkeeper.LogKeeper()

    startiest_time = time.time()
    files1 = []
    files2 = []
    jsons = []

    # Create destination folders
    if folder3_path != "":
        for folder in ["!Duplicate", "!New", "!Error", "!Unsorted"]:
            dest_folder = os.path.join(folder3_path, folder)
            if not os.path.exists(dest_folder):
                os.makedirs(dest_folder, exist_ok=True)

    seen_hashes.set_destination_folders(folder1_path, folder2_path, folder3_path, data_handling, json_handling)
    seen_hashes.set_logger(logger)

    start = time.time()
    # if folder1_path is None, only compare files in folder2_path
    if folder1_path is not None:
        for root, _, file_names in os.walk(folder1_path):
            for file in file_names:
                files1.append(os.path.join(root, file))

        # Separate images and videos from other files for folder 1
        images1 = [file for file in files1 if os.path.splitext(file)[1].lower() in [".jpg", ".jpeg", ".png", ".heic", ".webp"]]
        videos1 = [file for file in files1 if os.path.splitext(file)[1].lower() in [".mp4", ".mov", ".avi"]]
        remaining_files1 = [file for file in files1 if file not in images1 and file not in videos1]

    # get all files in folder 2, store json files separately
    for root, _, file_names in os.walk(folder2_path):
        for file in file_names:
            path = os.path.join(root, file)
            path = os.path.normpath(path)
            files2.append(path)

    # Separate images and videos from other files for folder 2
    images2 = [file for file in files2 if os.path.splitext(file)[1].lower() in [".jpg", ".jpeg", ".png", ".heic", ".webp"]]
    videos2 = [file for file in files2 if os.path.splitext(file)[1].lower() in [".mp4", ".mov", ".avi"]]
    jsons = [file for file in files2 if os.path.splitext(file)[1].lower() in [".json"]]
    jsons_dict = {}

    for file_path in jsons:
        with open(file_path, 'r') as file:
            data = json.load(file)
            jsons_dict[os.path.join(os.path.dirname(file_path), data['title'])] = os.path.abspath(file_path)

    remaining_files2 = [file for file in files2 if file not in images2 and file not in videos2 and file not in jsons]
    logger.add_time(time.time()-start, "Setup")

    # compare remaining files for exact copies
    start = time.time()
    if folder1_path is not None and folder3_path is not None:
        progress = 0
        process = "Comparing non-image and video files for exact copies"
        total = len(remaining_files2)

        i = 0
        for file2 in remaining_files2:
            if stop_event.is_set():
                return
            i += 1
            if file2 in remaining_files1:
                dest_folder = os.path.join(folder3_path, "!Duplicates")
                seen_hashes.copy_file(file2, None, dest_folder)
            else:
                dest_folder = os.path.join(folder3_path, "!Unsorted")
                seen_hashes.copy_file(file2, None, dest_folder)
            progress = i / total

    logger.add_time(time.time()-start, "Compare remaining")

    start = time.time()
    progress = 0
    process = "loading old hashes"
    seen_hashes.load_items()
    logger.add_time(time.time()-start, "Load hashes")

    
    if enable_threads:
        if type_select_var.get() in ["1", "2"]:
            progress = 0
            process = "Hashing old images"
            total = len(images1)
            with ThreadPoolExecutor() as executor:
                futures = [executor.submit(seen_hashes.hash_image, image) for image in images1]
                for i, future in enumerate(futures):
                    if stop_event.is_set():
                        return
                    future.result()  # Wait for the task to complete
                    if i % 10 == 0:  # Update progress every 10 iterations
                        progress = i / total

        if type_select_var.get() in ["1", "3"]:
            progress = 0
            process = "Hashing old videos"
            total = len(videos1)
            with ThreadPoolExecutor() as executor:
                futures = [executor.submit(seen_hashes.hash_video, video) for video in videos1]
                for i, future in enumerate(futures):
                    if stop_event.is_set():
                        return
                    future.result()
                    if i % 10 == 0:
                        progress = i / total

        if type_select_var.get() in ["1", "2"]:
            progress = 0
            process = "Hashing new images"
            total = len(images2)
            with ThreadPoolExecutor() as executor:
                futures = [executor.submit(seen_hashes.hash_image, image, True) for image in images2]
                for i, future in enumerate(futures):
                    if stop_event.is_set():
                        return
                    future.result()
                    if i % 10 == 0:
                        progress = i / total

        if type_select_var.get() in ["1", "3"]:
            progress = 0
            process = "Hashing new videos"
            total = len(videos2)
            with ThreadPoolExecutor() as executor:
                futures = [executor.submit(seen_hashes.hash_video, video, True) for video in videos2]
                for i, future in enumerate(futures):
                    if stop_event.is_set():
                        return
                    future.result()
                    if i % 10 == 0:
                        progress = i / total

    else:
        if type_select_var.get() in ["1", "2"]:
            progress = 0
            process = "Hashing old images"
            total = len(images1)
            for i, image in enumerate(images1): 
                if stop_event.is_set():
                    return
                seen_hashes.hash_image(image)
                if i % 10 == 0:
                        progress = i / total

        if type_select_var.get() in ["1", "3"]:
            progress = 0
            process = "Hashing old video"
            total = len(videos1)
            for i, video in enumerate(videos1): 
                if stop_event.is_set():
                    return
                seen_hashes.hash_video(video)
                if i % 10 == 0:
                        progress = i / total

        if type_select_var.get() in ["1", "2"]:
            progress = 0
            process = "Hashing new images"
            total = len(images2)
            for i, image in enumerate(images2): 
                if stop_event.is_set():
                    return
                seen_hashes.hash_image(image, True)
                if i % 10 == 0:
                        progress = i / total

        if type_select_var.get() in ["1", "3"]:
            progress = 0
            process = "Hashing new videos"
            total = len(videos2)
            for i, video in enumerate(videos2): 
                if stop_event.is_set():
                    return
                seen_hashes.hash_video(video, True)
                if i % 10 == 0:
                        progress = i / total

    progress = 0
    process = "saving hashes for later use"
    seen_hashes.save_items()

    seen_hashes.set_json_files(jsons_dict)

    progress = 0
    if type_select_var.get() in ["1", "2"]:
        process = "Building image tree"
        seen_hashes.build_image_tree()

    if type_select_var.get() in ["1", "3"]:
        process = "Building video tree"
        seen_hashes.build_video_tree()

    if enable_threads:
        if type_select_var.get() in ["1", "2"]:
            progress = 0
            process = "Finding duplicate images"
            total = len(seen_hashes.new_images)
            with ThreadPoolExecutor() as executor:
                    futures = [executor.submit(seen_hashes.check_duplicates, image, seen_hashes.images, seen_hashes.new_images) for image in seen_hashes.new_images.items()]
                    for i, future in enumerate(futures): 
                            if stop_event.is_set():
                                return
                            future.result()
                            if i % 10 == 0:
                                    progress = i / total

        if type_select_var.get() in ["1", "3"]:
            progress = 0
            process = "Finding duplicate videos"
            total = len(seen_hashes.new_videos)
            with ThreadPoolExecutor() as executor:
                    futures = [executor.submit(seen_hashes.check_duplicates, video, seen_hashes.videos, seen_hashes.new_videos) for video in seen_hashes.new_videos.items()]
                    for i, future in enumerate(futures): 
                            if stop_event.is_set():
                                return
                            future.result()
                            if i % 10 == 0:
                                    progress = i / total                    

    else:  
        if type_select_var.get() in ["1", "2"]:
            progress = 0
            process = "Finding duplicate images"
            total = len(seen_hashes.new_images)
            for i, image in enumerate(seen_hashes.new_images.items()): 
                    if stop_event.is_set():                            
                        return
                    seen_hashes.check_duplicates(image, seen_hashes.images, seen_hashes.new_images)
                    if i % 10 == 0:
                            progress = i / total

        if type_select_var.get() in ["1", "3"]:
            progress = 0
            process = "Finding duplicate videos"
            total = len(seen_hashes.new_videos)
            for i, video in enumerate(seen_hashes.new_videos.items()): 
                    if stop_event.is_set():
                        return
                    seen_hashes.check_duplicates(video, seen_hashes.videos, seen_hashes.new_videos)
                    if i % 10 == 0:
                            progress = i / total

    progress = 0
    process = "Verifying process"
    i = None
    total = None
    images_verified = seen_hashes.verify(images2)
    videos_verified = seen_hashes.verify(videos2)

    processing_time = time.time() - startiest_time
    print(f"Processing time: {processing_time:.2f} seconds")

    if len(images_verified) == 0 and len(videos_verified) == 0:
        progress = 0
        process = "Verified all files"
        i = None
        total = None
        time.sleep(1)
    else:
        progress = 0
        process = "Unable to verify all files, check terminal for problems"
        i = None
        total = None
        print(images_verified)
        print(videos_verified)
        time.sleep(5)

    progress = 0
    process = "Finished finding duplicates, opening selection window"
    i = None
    total = None

    if seen_hashes.checked_nodes>0:
        print(f"Checked {seen_hashes.checked_nodes} nodes compared to lazily comparing everything {len(seen_hashes.images)*len(seen_hashes.new_images) + len(seen_hashes.videos)*len(seen_hashes.new_videos)} times. A {(len(seen_hashes.images)*len(seen_hashes.new_images) + len(seen_hashes.videos)*len(seen_hashes.new_videos))/seen_hashes.checked_nodes:.1f}x speed up.")
    print(f"{len(seen_hashes.higher_res)} images or videos have a higher resolution than their pre-existing counterpart")

    window.after(20, show_comparison_dialog, window, seen_hashes.higher_res, logger, stop_event)

    print("total time allocation: ", logger.get_time())
    print("average time per run: ", logger.get_time(avg=True))

    if len(seen_hashes.higher_res)==0 or stop_event.is_set():
        processing_complete.set()  # Set flag to indicate completion

################################## GUI ##################################
total_size = 0
#open prompt window to select folder, start at the path specified in the path variable
window = Tk()
style = ttk.Style()
sv_ttk.set_theme("dark")

def on_closing(only_stop_threads=False):
    # cancel threads
    global processing_complete
    global stop_event
    stop_event.set()

    # Wait for the thread to finish
    if processing_thread is not None and processing_thread.is_alive():
        processing_thread.join(timeout=15)
    
    if not only_stop_threads:
        if messagebox.askokcancel("Quit", "Do you want to quit?"):
            window.quit()  # Stop the main loop
            window.destroy() # Destroy the window
    else:
        # re-enable all buttons
        button1.config(state=NORMAL)
        button2.config(state=NORMAL)
        button3.config(state=NORMAL)
        button4.config(state=NORMAL)
        enable_threads_button.config(state=NORMAL)
        enable_meta_button.config(state=NORMAL)
        threshold_slider.config(state=NORMAL)

        # re-enable entry fields
        entry1.config(state=NORMAL)
        entry2.config(state=NORMAL)
        entry3.config(state=NORMAL)

window.protocol("WM_DELETE_WINDOW", on_closing)

small_font = Font(family='Roboto', size=10, weight='bold')
mid_font = Font(family='Roboto', size=15, weight='bold')
big_font = Font(family='Roboto', size=20, weight='bold')

window.title("File duplicate remover")

seen_hashes = None
processing_thread = None
processing_complete = threading.Event()
stop_event = threading.Event()
progress = 0
process = None
time_remaining = 0
i = 0
total = 0
threshold = 0.9

folder_path1 = StringVar()
folder_path2 = StringVar()
folder_path3 = StringVar()

label0 = Label(window, text="Duplicate remover", width=70 ,font=big_font)
label0.pack(fill=X, expand=True)

###### add dividing line ######
ttk.Separator(window, orient=HORIZONTAL).pack(fill=X, expand=True)

def get_free_space(directory):
    """Returns the free space of the disk where 'directory' is located in bytes."""
    if os.name == 'nt':  # Windows
        return get_free_space_windows(directory)
    else:  # Unix-like (Linux, macOS)
        return get_free_space_unix(directory)

def get_free_space_windows(directory):
    """Returns the free space of the disk where 'directory' is located in bytes (Windows)."""
    free_bytes = ctypes.c_ulonglong(0)
    total_bytes = ctypes.c_ulonglong(0)
    total_free_bytes = ctypes.c_ulonglong(0)
    
    if ctypes.windll.kernel32.GetDiskFreeSpaceExW(directory, ctypes.byref(free_bytes), ctypes.byref(total_bytes), ctypes.byref(total_free_bytes)):
        return free_bytes.value
    else:
        raise OSError("Failed to get disk space information")

def get_free_space_unix(directory):
    """Returns the free space of the disk where 'directory' is located in bytes (Unix-like)."""
    stats = os.statvfs(directory)
    return stats.f_frsize * stats.f_bfree

def format_size(size):
    if size > 1024:
        size /= 1024
        if size > 1024:
            size /= 1024
            if size > 1024:
                size /= 1024
                size = f"{size:.2f} GB"
            else:
                size = f"{size:.2f} MB"
        else:
            size = f"{size} KB"
    else:
        size = f"{size} bytes"
    return size

def on_folder2_selected(path):
    global total_size
    if path == '' or path is None:
        return
    folder_path2.set(path)
    # get total size of files in the folder
    files = []
    for root, _, file_names in os.walk(path):
        for file in file_names:
            files.append(os.path.join(root, file))
    total_size = sum(os.path.getsize(file) for file in files)
    total_size = format_size(total_size)

    on_folder_selected()

def on_folder3_selected(path):
    global seen_hashes
    if path == '' or path is None:
        return
    folder_path3.set(path)
    update_radiobuttons()
    
    on_folder_selected()

def on_folder_selected():
    global total_size

    if data_handling_var.get() in ["3", "4", "6"]:
        label4.config(text=f"No additional space will be taken up on the disk.")
    
    elif data_handling_var.get() in ["5"]:
        label4.config(text=f"Some space might be freed up from the disk.")

    elif folder_path2.get() and folder_path2.get() != '':
        if folder_path3.get() and folder_path3.get() != '':
            # get free space on the disk
            free_space = get_free_space(folder_path3.get())
            free_space = format_size(free_space)
            
            label4.config(text=f"Warning: This process will take up (at most) {total_size}/{free_space} extra on the disk.")
        else:
            #can no longer be accessed
            label4.config(text=f"Warning: This process will take up (at most) {total_size} extra on the disk.")

    elif folder_path3.get() and folder_path3.get() != '':
        free_space = get_free_space(folder_path3.get())
        free_space = format_size(free_space)
        
        label4.config(text=f"{free_space} available on disk.")

    else:
        label4.config(text="Warning: This process will take up extra on the disk.")


frame1 = Frame(window)
frame1.pack(pady=20)
label1 = Label(frame1, text="Select the folder with existing files (can be left empty)", font=mid_font)
label1.pack(fill=X, expand=True)
entry_frame1 = Frame(frame1)
entry_frame1.pack()
entry1 = Entry(entry_frame1, textvariable=folder_path1, font=small_font)
entry1.pack(side=LEFT, ipady=3)  # Adjust ipady to match the button height
button1 = Button(entry_frame1, text="Browse", font=small_font, command=lambda: folder_path1.set(filedialog.askdirectory()))
button1.pack(side=LEFT)
open_folder_button = Button(entry_frame1, text="Open folder", font=small_font, command=lambda: os.startfile(folder_path1.get()) if folder_path1.get() else None)
open_folder_button.pack(side=LEFT)
clear_button1 = Button(entry_frame1, text="Clear", font=small_font, command=lambda: (folder_path1.set(""), on_folder_selected()))
clear_button1.pack(side=LEFT)

frame2 = Frame(window)
frame2.pack(pady=20)
label2 = Label(frame2, text="Select the folder with new files", font=mid_font)
label2.pack(fill=X, expand=True)
entry_frame2 = Frame(frame2)
entry_frame2.pack()
entry2 = Entry(entry_frame2, textvariable=folder_path2, font=small_font)
entry2.pack(side=LEFT, ipady=3)
button2 = Button(entry_frame2, text="Browse", font=small_font, command=lambda: on_folder2_selected(filedialog.askdirectory()))
button2.pack(side=LEFT,)
open_folder_button = Button(entry_frame2, text="Open folder", font=small_font, command=lambda: os.startfile(folder_path2.get()) if folder_path2.get() else None)
open_folder_button.pack(side=LEFT)
clear_button2 = Button(entry_frame2, text="Clear", font=small_font, command=lambda: (folder_path2.set(""), on_folder_selected()))
clear_button2.pack(side=LEFT)


frame3 = Frame(window)
frame3.pack(pady=20)
label3 = Label(frame3, text="Select the destination folder (can be left empty)", font=mid_font)
label3.pack(fill=X, expand=True)
entry_frame3 = Frame(frame3)
entry_frame3.pack()
entry3 = Entry(entry_frame3, textvariable=folder_path3, font=small_font)
entry3.pack(side=LEFT, ipady=3)
button3 = Button(entry_frame3, text="Browse", font=small_font, command=lambda: on_folder3_selected(filedialog.askdirectory()))
button3.pack(side=LEFT)
open_folder_button = Button(entry_frame3, text="Open folder", font=small_font, command=lambda: os.startfile(folder_path3.get()) if folder_path3.get() else None)
open_folder_button.pack(side=LEFT)
clear_button3 = Button(entry_frame3, text="Clear", font=small_font, command=lambda: (folder_path3.set(""), on_folder_selected(), update_radiobuttons()))
clear_button3.pack(side=LEFT)

def update_radiobuttons(*args):
    # Clear current radiobuttons
    for widget in data_radio_frame.winfo_children():
        widget.destroy()

    # Determine which options to show
    if folder_path3.get() == "":
        data_handling_values = {"Keep new files (duplicates will be deleted)" : "5"}
        data_handling_var.set("5")
    else:
        data_handling_values = {"Copy all files" : "1",
                                "Copy new files" : "2",
                                "Move all files" : "3",
                                "Move new files" : "4",
                                "Move duplicates": "6",
                                "Keep new files (duplicates will be deleted)" : "5"}
        data_handling_var.set("1")
        

    # Create new radiobuttons
    for (text, value) in data_handling_values.items():
        ttk.Radiobutton(data_radio_frame, text=text, variable=data_handling_var, value = value).pack(side=LEFT,pady=5)

#
data_radio_frame = Frame(window)
data_radio_frame.pack(pady=20)

data_handling_var = StringVar()
data_handling_values = {"Keep new files (duplicates will be deleted)" : "5"}

for (text, value) in data_handling_values.items():
     ttk.Radiobutton(data_radio_frame, text=text, variable=data_handling_var, value = value).pack(side=LEFT,pady=5)

data_handling_var.set("5")

data_handling_var.trace_add('write', lambda *args: on_folder_selected())

##### add dividing line ######
ttk.Separator(window, orient=HORIZONTAL).pack(fill=X, expand=True)

# browse folder 2 to see how much extra space will be taken up
files = []

# warn user that the process will take up space on the disk
frame4 = Frame(window)
frame4.pack(expand=True, fill=Y)
label4 = Label(frame4, text=f"Warning: This process might take up extra space on the disk.", font=small_font)
label4.pack(fill=X, expand=True, pady=20)

# warn user that the process will take a long time
frame5 = Frame(window)
frame5.pack(pady=20)
label5 = Label(frame5, text="Warning: This process may take a long time to complete based on the total size.", font=small_font)
label5.pack(fill=X, expand=True)

##### add dividing line ######
ttk.Separator(window, orient=HORIZONTAL).pack(fill=X, expand=True)

def start_process():
    global processing_thread
    global seen_hashes
    global stop_event
    global processing_complete

    if not folder_path2.get():
        messagebox.showerror("Error", "Please select the folder with new files")
        return

    progress_frame.pack(fill=X, expand=True)
    # disable all buttons
    button1.config(state=DISABLED)
    button2.config(state=DISABLED)
    button3.config(state=DISABLED)
    button4.config(state=DISABLED)
    enable_threads_button.config(state=DISABLED)
    enable_meta_button.config(state=DISABLED)
    threshold_slider.config(state=DISABLED)

    # disable entry fields
    entry1.config(state=DISABLED)
    entry2.config(state=DISABLED)
    entry3.config(state=DISABLED)
    
    seen_hashes = fs.HashStorage(threshold=threshold, extract_meta=extract_meta)
    stop_event.clear()
    processing_complete.clear()
    processing_thread = threading.Thread(target=process_folder, args=(folder_path1.get(), folder_path2.get(), folder_path3.get(), data_handling_var.get(), delete_jsons_var.get(), stop_event))
    processing_thread.start()

    update_progress_and_process()

# General information
info_frame = Frame(window)
info_frame.pack(pady=20)

info_label = Label(info_frame, text="""Enabling threading will increase speed drastically but will consume all computer resources.
                   \n Enabling metadata extraction searches for json files corresponding to each file containing metadata. Another significant slowdown""", font=small_font, foreground='cyan')
info_label.pack(fill=X, expand=True)

threshold_frame = Frame(window)
threshold_frame.pack(pady=20)

# threshold label
threshold_label = Label(threshold_frame, text="Select similarity between images to mark as duplicates. 0.9 is recommended", font=small_font)
threshold_label.pack(fill=X, expand=True, side=TOP)

# threshold slider
threshold_slider = ttk.Scale(threshold_frame, from_=0, to=1, orient=HORIZONTAL, length=200)
threshold_slider.set(threshold)
threshold_slider.pack(fill=X, expand=True, side=LEFT)

#threshold label
threshold_label = Label(threshold_frame, text=f"Threshold: {threshold:.2f}", font=small_font)
threshold_label.pack(fill=X, expand=True, side=LEFT)


# detect slider changes
def on_threshold_change(value):
    global threshold
    threshold = float(value)
    # truncate to 2 decimal places
    threshold = float(f"{threshold:.2f}")
    threshold_label.config(text=f"Threshold: {threshold:.2f}")

threshold_slider.config(command=on_threshold_change)

#
radio_frame = Frame(window)
radio_frame.pack(pady=20)

type_select_var = StringVar()
type_select_values = {"Images and videos" : "1",
                      "Images only" : "2",
                      "Videos only" : "3"}

for (text, value) in type_select_values.items():
     ttk.Radiobutton(radio_frame, text=text, variable=type_select_var, value = value).pack(side=LEFT,pady=5)

type_select_var.set("1")

delete_jsons_var = BooleanVar()
delete_jsons_check = ttk.Checkbutton(radio_frame, text="Delete json files after metadata extraction", variable=delete_jsons_var)
delete_jsons_check.pack(side=LEFT,pady=5)
delete_jsons_check.pack_forget()

# buttons

buttons_frame = Frame(window)
buttons_frame.pack(pady=20)

button4 = Button(buttons_frame, text="Start process", font=big_font, command=start_process)
button4.pack(fill=X, expand=True, side=LEFT)

cancel_button = Button(buttons_frame, text="Cancel", font=big_font, command=lambda: on_closing(only_stop_threads=True))
cancel_button.pack(fill=X, expand=True, side=LEFT)

def toggle_threads():
    global enable_threads
    enable_threads = not enable_threads
    enable_threads_button.config(text="Disable threading" if enable_threads else "Enable threading")

enable_threads = False
enable_threads_button = Button(buttons_frame, text="Enable threading", font=big_font, command=toggle_threads)
enable_threads_button.pack(fill=X, expand=True, side=LEFT)

def toggle_meta():
    global extract_meta
    extract_meta = not extract_meta
    enable_meta_button.config(text="Disable metadata extraction" if extract_meta else "Enable metadata extraction")
    if extract_meta:
        delete_jsons_check.pack(side=LEFT,pady=5)
    else:
        delete_jsons_check.pack_forget()

extract_meta = False
enable_meta_button = Button(buttons_frame, text="Enable metadata extraction", font=big_font, command=toggle_meta)
enable_meta_button.pack(fill=X, expand=True, side=LEFT)


def update_progress(progress):
    pb["value"] = progress * 100
    window.update()

def update_process_label(process=None, progress=0, time_remaining=0, i=None, total=None):
    if process is None:
        return ""
    
    if i is not None and total is not None:
        if time_remaining > 0:
            return f"Progress: {progress:.2%} - {process} ({i}/{total}) - Time remaining: {time_remaining:.2f} seconds"
        return f"Progress: {progress:.2%} - {process} ({i}/{total})"
    if time_remaining > 0:
        return f"Progress: {progress:.2%} - {process} - Time remaining: {time_remaining:.2f} seconds"
    return f"Progress: {progress:.2%} - {process}"

def update_progress_and_process():
    global progress
    global process
    global time_remaining
    global i
    global total

    if not processing_complete.is_set():
        update_progress(progress)
        process_label["text"] = update_process_label(process, progress, time_remaining, i, total)	
        window.after(100, update_progress_and_process)
    else:
        update_progress(progress)
        process_label["text"] = update_process_label("Finished", 1, time_remaining, None, None)
        on_closing(only_stop_threads=True)

progress_frame = Frame(window)
progress_frame.pack(fill=X, expand=True)

#process labela
process_label = ttk.Label(progress_frame, text=update_process_label(), font=small_font)
process_label.pack(fill=X, expand=True)

# progress bar
pb = ttk.Progressbar(progress_frame, length=200, mode="determinate")
pb.pack(fill=X, expand=True)

progress_frame.pack_forget()

window.update()

window.mainloop()