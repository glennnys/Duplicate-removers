import threading

class LogKeeper:
    def __init__(self):
        self.individual_times = {}
        self.errors = {}
        self.time_lock = threading.Lock()
        self.error_lock = threading.Lock()

    def add_time(self, time, event):
        with self.time_lock:
            if event not in self.individual_times:
                self.individual_times[event] = []

            self.individual_times[event].append(time)

    def add_error(self, file, error):
        with self.error_lock:
            if error not in self.errors:
                self.errors[error] = []

            self.errors[error].append(file)

    def get_time(self, event=None, avg=False):
        if event is not None:
            total = 0
            with self.time_lock:
                for time in self.individual_times[event]:
                    total += time

                if avg:
                    return f"{total/len(self.individual_times[event]):.2f}"    
                else:
                    return f"{total:.2f}"
        
        else:
            total_dict = {}

            with self.time_lock:
                for event in self.individual_times:
                    total = 0
                    for time in self.individual_times[event]:
                        total += time
                
                    if avg:    
                        total_dict[event] = f"{total/len(self.individual_times[event]):.2f}"
                    else:
                        total_dict[event] = f"{total:.2f}"
            
            return total_dict
        
    def get_errors(self, error=None, count=False):
        if error is not None:
            if count:
                total = 0
                with self.error_lock:
                    for file in self.errors[error]:
                        total += 1

                return total
            
            else:
                return self.errors[error]
        
        else:
            if count:
                total_dict = {}

                with self.error_lock:
                    for error in self.errors:
                        total = 0
                        for file in self.individual_times[error]:
                            total += 1

                        total_dict[error] = total
                
                return total_dict
            
            return self.errors