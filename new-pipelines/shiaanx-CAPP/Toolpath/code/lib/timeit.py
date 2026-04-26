import time
from contextlib import contextmanager

class Timer():
    def __init__(self):
        self.data = {}

    def add_timing(self, key, time):
        if not key in self.data:
            self.data[key] = {"key" : key, "ncalls" :0, "tottime" : 0.0}

        entry = self.data[key]
        entry["ncalls"] += 1
        entry["tottime"] += time

    def reset(self):
        self.data = {}
    
    @contextmanager
    def time_block(self, key):
        t_start = time.perf_counter()
        try:
            yield
        finally:
            t_stop = time.perf_counter()
            t = t_stop - t_start
            self.add_timing(key, t)

    def time_calls(self, f):
        def wrapper(*args, **kwargs):
            with self.time_block(f.__name__):
                return f(*args, **kwargs)
        return wrapper


    def __repr__(self):
        rows = [
            ["name", "ncalls", "tottime"],
        ]
        colwidths = [len(x) for x in rows[0]]
        # TODO sort by time?
        items = sorted(self.data.values(), key=lambda x: x["tottime"], reverse=True)
        for item in items:
            tottime = "{t:.4g}s".format(t = item["tottime"])
            name = str(item["key"])
            ncalls = str(item["ncalls"])
            row = [name, ncalls, tottime]
            rows.append(row)
            for i in range(len(row)):
                colwidths[i] = max(colwidths[i], len(row[i]))

        res = "\n".join(
            [
            " | ".join([entry.ljust(colwidths[i]) for (i, entry) in enumerate(row)])
            for row in rows
            ]
        )
        return res

DEFAULT_TIMER = Timer()