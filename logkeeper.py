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

    def make_time_readable(self, time):
        hours = int(time // 3600)
        minutes = int((time % 3600) // 60)
        seconds = int(time % 60)
        milliseconds = int((time - int(time)) * 1000)

        # Format the string
        return f"{hours:02}h:{minutes:02}m:{seconds:02}s:{milliseconds:03}ms"

    def add_error(self, file, error):
        with self.error_lock:
            if error not in self.errors:
                self.errors[error] = []

            self.errors[error].append(file)

    def get_time(self, event=None, avg=False):
        total_dict = {}
        events = [event] if event is not None else self.individual_times

        with self.time_lock:
            for event in events:
                total = 0
                for time in self.individual_times[event]:
                    total += time
            
                if avg:    
                    total_dict[event] = self.make_time_readable(total/len(self.individual_times[event]))
                else:
                    total_dict[event] = self.make_time_readable(total)
        
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