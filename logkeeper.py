import threading

class LogKeeper:
    def __init__(self):
        self.individual_times = {}
        self.errors = {}
        self.time_lock = threading.Lock()
        self.error_lock = threading.Lock()

    def clear(self, thing_to_clear="all"):
        if thing_to_clear == "all":
            self.individual_times = {}
            self.errors = {}
        if thing_to_clear == "times":
            self.individual_times = {}
        if thing_to_clear == "errors":
            self.errors = {}

    def add_time(self, time, event):
        with self.time_lock:
            if event not in self.individual_times:
                self.individual_times[event] = []

            self.individual_times[event].append(time)

    def make_time_readable(self, time):
        total_ms = int(round(time * 1000))
        hours, rem = divmod(total_ms, 3_600_000)
        minutes, rem = divmod(rem, 60_000)
        seconds, milliseconds = divmod(rem, 1_000)
        # Format the string
        return f"{hours:02}h:{minutes:02}m:{seconds:02}s:{milliseconds:03}ms"

    def add_error(self, file, error):
        with self.error_lock:
            if error not in self.errors:
                self.errors[error] = []

            self.errors[error].append(file)

    def get_time(self, event=None, avg=False, sort="9"):
        total_dict = {}
        events = [event] if event is not None else self.individual_times

        with self.time_lock:
            for event in events:
                total = 0
                for time in self.individual_times[event]:
                    total += time
            
                if avg:    
                    total_dict[event] = total/len(self.individual_times[event])
                else:
                    total_dict[event] = total
        
        key_lambda = lambda item: item[0] 
        if sort in ["a","z"]:
            key_lambda = lambda item: item[0]
        elif sort in ["0", "9"]:
            key_lambda = lambda item: item[1]
         
        reverse = sort in ["z", "9"]
        
        
        total_dict = {k: self.make_time_readable(v) for k, v in sorted(total_dict.items(), key=key_lambda, reverse=reverse)}
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