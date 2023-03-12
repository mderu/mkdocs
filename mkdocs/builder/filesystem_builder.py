from mkdocs import commands
from mkdocs.builder.scm_builder import ScmBuilder
from mkdocs.config.defaults import MkDocsConfig


class FilesystemBuilder(ScmBuilder):
    """Builds a Mkdocs site directly from the filesystem."""
    def get_default_version(self):
        return 'filesystem'

    def get_concrete_version(self, *_, **__):
        return 'filesystem'

    def unpack_version(self, config: MkDocsConfig, concrete_verison: str):
        config["docs_dir"] = self.config['docs_dir']
        return
