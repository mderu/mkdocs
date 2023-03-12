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
        super().__init__(*args, **kwargs)

        self.docs_depot_path = self.config.perforce_scm_builder['docs_depot_path']
        if not self.docs_depot_path.endswith('/...'):
            self.docs_depot_path = self.docs_depot_path.strip('/') + '/...'

    def get_concrete_version(self, version_identifier: str):
        sync_label, *shelve_number = version_identifier.split('+', 2)
        if shelve_number:
            shelve_number = shelve_number[0]

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
    
    def get_default_version(self):
        return self.config.perforce_scm_builder['default_label']

    def unpack_version(self, config: MkDocsConfig, concrete_verison: str):
        sync_label, *shelve = concrete_verison.split('+', 2)
        if shelve:
            shelve = shelve[0]

        log.info(f'Printing "{self.docs_depot_path}@{sync_label}" to {config["docs_dir"]}.')
        command = f'p4 print -o "{config["docs_dir"]}/..." "{self.docs_depot_path}@{sync_label}"'
        subprocess.Popen(command, shell=True).communicate()

        if shelve:
            # TODO: Does `p4 print`ing a delete in a shelve work?
            command = f'p4 print -o "{config["docs_dir"]}/..." "{self.docs_depot_path}@={shelve}"'
            subprocess.Popen(command, shell=True).communicate()
