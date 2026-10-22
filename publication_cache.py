"""A bounded publication cache (Gamma G6). Keys are scoped by database, goal,
policy hash, evidence and dataset generations and a validity generation; never
full config text. The cache is not a source of evidence truth, and a TTL does
not make a deadline or availability current.
"""
from collections import OrderedDict
import copy
import threading
import time


class PublicationCache:
    def __init__(self, limit=8, clock=time.monotonic):
        if type(limit) is not int or limit < 1:
            raise ValueError("Positive cache bound required")
        self.limit, self.clock = limit, clock
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key, ttl):
        if type(ttl) not in (int, float) or not 0 < ttl <= 600:
            raise ValueError("Bounded positive TTL required")
        now = self.clock()
        with self.lock:
            entry = self.entries.get(key)
            if entry is None:
                return None
            stamp, value = entry
            if now - stamp >= ttl:
                del self.entries[key]
                return None
            self.entries.move_to_end(key)
            return copy.deepcopy(value)

    def put(self, key, value):
        stored = copy.deepcopy(value)
        with self.lock:
            self.entries[key] = (self.clock(), stored)
            self.entries.move_to_end(key)
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)

    def clear(self):
        with self.lock:
            self.entries.clear()


def publication_key(database_id, goal, policy_hash, evidence_generation, dataset_hash, validity_generation):
    return (database_id, goal, policy_hash, evidence_generation, dataset_hash, validity_generation)
