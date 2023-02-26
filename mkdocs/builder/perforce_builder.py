import logging
import posixpath
import subprocess
from mkdocs.builder.scm_builder import ScmBuilder
from mkdocs.config.defaults import MkDocsConfig

log = logging.getLogger(__name__)

class PerforceBuilder(ScmBuilder):
    """Builds a Mkdocs site from a CL + optional shelve.

    Note: this implementation does not allow you to serve the server across
    streams/branches/depots.
    """
    def __init__(self, *args, **kwargs):
        super(ScmBuilder, self).__init__(*args, **kwargs)

        # Get the depot path the documentation is defined at.
        command = f'p4 -ztag -F %depotFile% where {self.config.docs_dir}'
        popen = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE)
        stdout, _ = popen.communicate()
        self.docs_depot_path = stdout.decode('utf8').strip()


    def get_concrete_version(self, version_identifier: str):
        sync_label, shelve_number = version_identifier.split('+', 2)
        # TODO: non-numeric sync labels (i.e., labels that aren't CLs) can change their contents.
        #       Create a mechanism for auto-refreshing these.

        # Get the most recent commit to the mkdocs documentation.
        command = f'p4 -ztag -F %change% changes -m1 -s submitted {self.docs_depot_path}'
        popen = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE)
        stdout, _ = popen.communicate()
        highest_cl_number = int(stdout.decode('utf8').strip())

        if sync_label == '' or (sync_label.isdigit() and highest_cl_number < sync_label):
            sync_label = highest_cl_number

        if shelve_number:
            return f'{sync_label}+{shelve_number}'
        return sync_label

    def unpack_version(self, config: MkDocsConfig, concrete_verison: str):
        sync_label, shelve = concrete_verison.split('+')
        command = f'p4 print -o {config["docs_dir"]}/... {self.docs_depot_path}@{sync_label}'
        subprocess.Popen(command, shell=True).communicate()

        # TODO: Does `p4 print`ing a delete in a shelve work?
        command = f'p4 print -o {config["docs_dir"]}/... {self.docs_depot_path}@={shelve}'
        subprocess.Popen(command, shell=True).communicate()
