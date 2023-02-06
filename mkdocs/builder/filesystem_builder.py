from mkdocs import commands
from mkdocs.config.defaults import MkDocsConfig


class FilesystemBuilder():
    """Builds a Mkdocs site directly from the filesystem."""
    def __init__(self, config: MkDocsConfig, live_server: bool = False, dirty: bool = False):
        self.config = config
        self.live_server = live_server
        self.dirty = dirty
        pass

    def build(self, build_version):
        commands.build(self.config, self.live_server, self.dirty)
