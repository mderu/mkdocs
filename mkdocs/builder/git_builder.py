import os
import subprocess
from zipfile import ZipFile
from mkdocs.builder.scm_builder import ScmBuilder
from mkdocs.config.defaults import MkDocsConfig


class GitBuilder(ScmBuilder):
    """Builds a Mkdocs site from specific commits in Git."""

    def get_concrete_version(self, commitish: str):
        # Translate the commitish, like a branch name or tag, to a commit hash. Note that
        # passing in a commit hash will still return the same commit hash.
        command = f'git log -1 --pretty=format:"%H" {commitish} -- {self.config.docs_dir}'
        popen = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE)
        stdout, stderr = popen.communicate()
        ret_value = stdout.decode('utf8').strip()

        if popen.returncode != 0:
            raise RuntimeError(f'Failed to execute `{command}`: STDERR: {stderr}')

        # If an empty line is returned, the path does not exist on the given commit-ish.
        if ret_value == '':
            raise RuntimeError(f'Commit `{commitish}` does not have files at path '
                               f'{self.config.docs_dir}')
        return ret_value

    def unpack_version(self, config: MkDocsConfig, commitish: str):
        zip_file = f'{config["docs_dir"]}/git.zip'
        git_archive_cmd = f'git archive --format zip --output {zip_file} {commitish}'
        popen = subprocess.Popen(
            git_archive_cmd,
            # Note that this docs_dir config is different from the above.
            # This is the original docs dir, i.e., the docs directory of the Git repo.
            # Running from this directory creates an archive of only this and subdirectories.
            cwd=self.config.docs_dir,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout,stderr = popen.communicate()
        with ZipFile(zip_file, 'r') as zipFile:
            zipFile.extractall(os.path.dirname(zip_file))
        #os.remove(zip_file)
        if popen.returncode != 0:
            raise RuntimeError(
                f"Unable to run `{git_archive_cmd}`.\n"
                f"Stdout: {stdout}\nStderr{stderr}"
            )
