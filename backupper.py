import tkinter as tk
from tkinter import ttk
from tkinter import filedialog
from tkinter import messagebox
from tkinter.font import Font
import sv_ttk
import threading
import os

def run():
    window = tk.Tk()
    style = ttk.Style()
    sv_ttk.set_theme("dark")

    pass_values = {
        "hashes": None,
        "processing thread": None,
        "processing complete": threading.Event(),
        "pause event": threading.Event(),
        "stop event": threading.Event(),
        "progress": 0,
        "process": None,
        "time remaining": 0,
        "i": 0,
        "total": 0,
        "only check": False,
        "original folder": tk.StringVar(),
        "backup folder": tk.StringVar()
        }

    window.protocol("WM_DELETE_WINDOW", lambda *args: close(window, pass_values["stop event"], pass_values["processing thread"]))

    small_font = Font(family='Roboto', size=10, weight='bold')
    mid_font = Font(family='Roboto', size=15, weight='bold')
    big_font = Font(family='Roboto', size=20, weight='bold')

    window.title("File backup")
    
    ttk.Label(window, text="Backupper", width=50 ,font=big_font, anchor="center").pack(fill=tk.X, expand=True)

    ###### add dividing line ######
    ttk.Separator(window, orient=tk.HORIZONTAL).pack(fill=tk.X, expand=True)

    original_frame = tk.Frame(window)
    original_frame.pack(pady=20)

    original_label = ttk.Label(original_frame, text="Select the folder with the original files", font=mid_font)
    original_label.pack(fill=tk.X, expand=True)

    original_entry_frame = tk.Frame(original_frame)
    original_entry_frame.pack()
    
    original_entry = ttk.Entry(original_entry_frame, textvariable=pass_values['original folder'], font=small_font, width=50)
    original_entry.pack(side=tk.LEFT, ipady=6)  # Adjust ipady to match the button height

    original_browse = ttk.Button(original_entry_frame, text="Browse", command=lambda: pass_values["original folder"].set(filedialog.askdirectory()))
    original_browse.pack(side=tk.LEFT)

    original_open = tk.Button(original_entry_frame, text="Open folder", font=small_font, command=lambda: os.startfile(pass_values["original folder"].get()) if pass_values["original folder"].get() else None)
    original_open.pack(side=tk.LEFT)

    original_clear = tk.Button(original_entry_frame, text="Clear", font=small_font, command=lambda: (pass_values["original folder"].set("")))
    original_clear.pack(side=tk.LEFT)


    backup_frame = tk.Frame(window)
    backup_frame.pack(pady=20)

    backup_label = ttk.Label(backup_frame, text="Select the folder with the backup files", font=mid_font)
    backup_label.pack(fill=tk.X, expand=True)

    backup_entry_frame = tk.Frame(backup_frame)
    backup_entry_frame.pack()
    
    backup_entry = ttk.Entry(backup_entry_frame, textvariable=pass_values['backup folder'], font=small_font, width=50)
    backup_entry.pack(side=tk.LEFT, ipady=6)  # Adjust ipady to match the button height
    
    backup_browse = ttk.Button(backup_entry_frame, text="Browse", command=lambda: pass_values["backup folder"].set(filedialog.askdirectory()))
    backup_browse.pack(side=tk.LEFT)

    backup_open = tk.Button(backup_entry_frame, text="Open folder", font=small_font, command=lambda: os.startfile(pass_values["backup folder"].get()) if pass_values["backup folder"].get() else None)
    backup_open.pack(side=tk.LEFT)

    original_clear = tk.Button(backup_entry_frame, text="Clear", font=small_font, command=lambda: (pass_values["backup folder"].set("")))
    original_clear.pack(side=tk.LEFT)


    disable_on_start = []


    buttons_frame = tk.Frame(window)
    buttons_frame.pack(pady=20)

    def toggle_backup(label, values):
        values["only check"] = not values["only check"]

        if values["only check"]:
            label.config(text="Only check")
        else:
            label.config(text="Backup")


    backup_check_label =  ttk.Label(buttons_frame, text="Backup", font=mid_font, anchor="center")
    backup_check_label.pack(fill=tk.X, expand=True, side="top")

    only_check_button = tk.Button(buttons_frame, text="Toggle backup/check", font=big_font, command=lambda: toggle_backup(backup_check_label, pass_values))
    only_check_button.pack(fill=tk.X, expand=True, side=tk.LEFT)

    start_pause_button = tk.Button(buttons_frame, text="Start", font=big_font, command=lambda: start(pass_values))
    start_pause_button.pack(fill=tk.X, expand=True, side=tk.LEFT)

    cancel_button = tk.Button(buttons_frame, text="Cancel", font=big_font, command=lambda: cancel(pass_values))
    cancel_button.pack(fill=tk.X, expand=True, side=tk.LEFT)

    progress_frame = tk.Frame(window)
    progress_frame.pack(fill=tk.X, expand=True)

    #process label
    process_label = ttk.Label(progress_frame, text=progress_update(), font=small_font)
    process_label.pack(fill=tk.X, expand=True)

    # progress bar
    pb = ttk.Progressbar(progress_frame, length=200, mode="determinate")
    pb.pack(fill=tk.X, expand=True)

    progress_frame.pack_forget()


    window.update()
    window.mainloop()

def start():
    return

def cancel():
    return

def close(window, stop_event, processing_thread):
    if messagebox.askokcancel("Quit", "Do you want to quit?"):
        stop_event.set()

        if processing_thread is not None and processing_thread.is_alive():
            processing_thread.join(timeout=15)

        window.quit()  # Stop the main loop
        window.destroy() # Destroy the window

def progress_update():
    return

def human_readable_size(size):
    for unit in ['bytes', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0 or unit == 'TB':
            return f"{size:.2f} {unit}" if unit != 'bytes' else f"{int(size)} {unit}"
        size /= 1024.0

def make_backup():
    return


if __name__ == "__main__":
    run()