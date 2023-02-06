import subprocess
from mkdocs.builder.scm_builder import ScmBuilder
from mkdocs.config.defaults import MkDocsConfig


class PerforceBuilder(ScmBuilder):
    """Builds a Mkdocs site from a CL + optional shelve."""

    def get_concrete_version(self, version_identifier: str):
        raise NotImplementedError('TODO: implement')

    def unpack_version(self, config: MkDocsConfig, commitish: str):
        subprocess.Popen(
            f'p4 print -o {config["docs_dir"]}/... //depot/main/...'
        )