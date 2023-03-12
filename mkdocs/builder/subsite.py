import threading
from mkdocs.config.defaults import MkDocsConfig


class SubSite():
    def __init__(self, config: MkDocsConfig):
        self.config = config
        # TODO: Add a lock around reading/setting built to prevent race conditions
        # caused by mutliple page loads.
        self.built = False

        self._shutdown = False

        # TODO: Hook these up to LiveReload server to repair live reload functionality.
        self.epoch_cond = threading.Condition()
        self.rebuild_cond = threading.Condition()
