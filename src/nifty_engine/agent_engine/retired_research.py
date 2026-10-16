"""Compatibility projection only; never opens a feed or starts a worker."""
class RetiredResearch:
    def public(self): return dict(status='RETIRED_BY_OWNER',enabled=False,broker_writes=False)
    def next_view(self): return self.public()
    def start(self): pass
    def attach(self, market): pass
    def detach(self): pass
