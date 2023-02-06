from __future__ import annotations
import copy

import gzip
import logging
import os
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, Optional, Sequence, Union
from urllib.parse import urlsplit
from zipfile import ZipFile

import jinja2
from jinja2.exceptions import TemplateNotFound

import mkdocs
from mkdocs import utils
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.exceptions import Abort, BuildError
from mkdocs.structure.files import File, Files, get_files
from mkdocs.structure.nav import Navigation, get_navigation
from mkdocs.structure.pages import Page


log = logging.getLogger(__name__)
log.addFilter(utils.DuplicateFilter())


class SubSite():
    def __init__(self, config: MkDocsConfig):
        self.config = config
        self.built = False

        # TODO: Hook these up to LiveReload server to repair live reload functionality.
        self.epoch_cond = threading.Condition()
        self.rebuild_cond = threading.Condition()


class GitBuilder():
    """Builds a Mkdocs site from specific commits in Git.

    Note that there are limitations:

    * The config is not reloaded. At the moment, this is intentional, to avoid older plugins that
      are no longer installed from crashing the server.

    """
    def __init__(self, config: MkDocsConfig, live_server: bool = False, dirty: bool = False):
        self.config = config
        self.live_server = live_server
        self.dirty = dirty

        self.subsite_table = {}
        pass

    def get_commit_hash(self, commitish):
        """TODO: Move this to a git utility."""
        # Translate the commitish, like a branch name or tag, to a commit hash. Note that
        # passing in a commit hash will still return the same commit hash.
        return subprocess.Popen(
            f'git rev-list {commitish} -n 1',
            shell=True,
            stdout=subprocess.PIPE
        ).communicate()[0].decode('utf8').strip()

    def get_subsite(self, commitish):
        """Returns the MkdocsConfig for the given commit hash.

        Note that these are separate to avoid accidental navigation across commits.
        """
        commit_hash = self.get_commit_hash(commitish)

        if commit_hash not in self.subsite_table:
            new_config = copy.deepcopy(self.config)
            new_config['docs_dir'] = tempfile.mkdtemp(prefix='mkdocs_src_')
            zip_file = f'{new_config["docs_dir"]}/git.zip'
            git_archive_cmd = f'git archive --format zip --output {zip_file} {commit_hash}'
            popen = subprocess.Popen(
                git_archive_cmd,
                cwd=self.config.docs_dir,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout,stderr = popen.communicate()
            with ZipFile(zip_file, 'r') as zipFile:
                zipFile.extractall(os.path.dirname(zip_file))
            os.remove(zip_file)

            if popen.returncode != 0:
                raise RuntimeError(
                    f"Unable to run `{git_archive_cmd}`.\n"
                    f"Stdout: {stdout}\nStderr{stderr}"
                )

            new_config['site_dir'] = tempfile.mkdtemp(prefix='mkdocs_srv_')
            self.subsite_table[commit_hash] = SubSite(new_config)

        return self.subsite_table[commit_hash]

    # TODO: Stop hardcoding master.
    def build(self, build_version='master'):
        """Builds the website from the given commit.

        Here we use a fairly naive approach: we'll rebuild the entire server for every requested
        commit.

        TODO: Future iterations should de-dupe file copies. Most notably:
        * MkdocsConfig.docs_dir
        * MkdcosConfig.site_dir
        """
        logger = logging.getLogger('mkdocs')

        # Add CountHandler for strict mode
        warning_counter = utils.CountHandler()
        warning_counter.setLevel(logging.WARNING)

        commit_hash = self.get_commit_hash(build_version)
        subsite = self.get_subsite(commit_hash)
        config = subsite.config

        if config.strict:
            logging.getLogger('mkdocs').addHandler(warning_counter)

        try:
            start = time.monotonic()

            # Run `config` plugin events.
            self.subsite_table[commit_hash].config = config.plugins.run_event('config', config)
            config = self.subsite_table[commit_hash].config

            # Run `pre_build` plugin events.
            config.plugins.run_event('pre_build', config=config)

            if not self.dirty:
                log.info('Cleaning site directory')
                utils.clean_directory(config.site_dir)
            else:  # pragma: no cover
                # Warn user about problems that may occur with --dirty option
                log.warning(
                    "A 'dirty' build is being performed, this will likely lead to inaccurate navigation and other"
                    " links within your site. This option is designed for site development purposes only."
                )

            if not self.live_server:  # pragma: no cover
                log.info(f'Building documentation to directory: {config.site_dir}')
                if self.dirty and site_directory_contains_stale_files(config.site_dir):
                    log.info('The directory contains stale files. Use --clean to remove them.')

            # First gather all data from all files/pages to ensure all data is consistent across all pages.

            files = get_files(config)
            env = config.theme.get_env()
            files.add_files_from_theme(env, config)

            # Run `files` plugin events.
            files = config.plugins.run_event('files', files, config=config)

            nav = get_navigation(files, config)

            # Run `nav` plugin events.
            nav = config.plugins.run_event('nav', nav, config=config, files=files)

            log.debug('Reading markdown pages.')
            for file in files.documentation_pages():
                log.debug(f'Reading: {file.src_uri}')
                assert file.page is not None
                _populate_page(file.page, config, files, self.dirty)

            # Run `env` plugin events.
            env = config.plugins.run_event('env', env, config=config, files=files)

            # Start writing files to site_dir now that all data is gathered. Note that order matters. Files
            # with lower precedence get written first so that files with higher precedence can overwrite them.

            log.debug('Copying static assets.')
            files.copy_static_files(dirty=self.dirty)

            for template in config.theme.static_templates:
                _build_theme_template(template, env, files, config, nav)

            for template in config.extra_templates:
                _build_extra_template(template, files, config, nav)

            log.debug('Building markdown pages.')
            doc_files = files.documentation_pages()
            for file in doc_files:
                assert file.page is not None
                _build_page(file.page, config, doc_files, nav, env, self.dirty)

            # Run `post_build` plugin events.
            config.plugins.run_event('post_build', config=config)

            counts = warning_counter.get_counts()
            if counts:
                msg = ', '.join(f'{v} {k.lower()}s' for k, v in counts)
                raise Abort(f'\nAborted with {msg} in strict mode!')

            log.info('Documentation built in %.2f seconds', time.monotonic() - start)
            subsite.built = True
        except Exception as e:
            # Run `build_error` plugin events.
            config.plugins.run_event('build_error', error=e)
            if isinstance(e, BuildError):
                log.error(str(e))
                raise Abort('\nAborted with a BuildError!')
            raise

        finally:
            logger.removeHandler(warning_counter)


def get_context(
    nav: Navigation,
    files: Union[Sequence[File], Files],
    config: MkDocsConfig,
    page: Optional[Page] = None,
    base_url: str = '',
) -> Dict[str, Any]:
    """
    Return the template context for a given page or template.
    """
    if page is not None:
        base_url = utils.get_relative_url('.', page.url)

    extra_javascript = utils.create_media_urls(config.extra_javascript, page, base_url)

    extra_css = utils.create_media_urls(config.extra_css, page, base_url)

    if isinstance(files, Files):
        files = files.documentation_pages()

    return {
        'nav': nav,
        'pages': files,
        'base_url': base_url,
        'extra_css': extra_css,
        'extra_javascript': extra_javascript,
        'mkdocs_version': mkdocs.__version__,
        'build_date_utc': utils.get_build_datetime(),
        'config': config,
        'page': page,
    }


def _build_template(
    name: str, template: jinja2.Template, files: Files, config: MkDocsConfig, nav: Navigation
) -> str:
    """
    Return rendered output for given template as a string.
    """
    # Run `pre_template` plugin events.
    template = config.plugins.run_event('pre_template', template, template_name=name, config=config)

    if utils.is_error_template(name):
        # Force absolute URLs in the nav of error pages and account for the
        # possibility that the docs root might be different than the server root.
        # See https://github.com/mkdocs/mkdocs/issues/77.
        # However, if site_url is not set, assume the docs root and server root
        # are the same. See https://github.com/mkdocs/mkdocs/issues/1598.
        base_url = urlsplit(config.site_url or '/').path
    else:
        base_url = utils.get_relative_url('.', name)

    context = get_context(nav, files, config, base_url=base_url)

    # Run `template_context` plugin events.
    context = config.plugins.run_event(
        'template_context', context, template_name=name, config=config
    )

    output = template.render(context)

    # Run `post_template` plugin events.
    output = config.plugins.run_event('post_template', output, template_name=name, config=config)

    return output


def _build_theme_template(
    template_name: str, env: jinja2.Environment, files: Files, config: MkDocsConfig, nav: Navigation
) -> None:
    """Build a template using the theme environment."""

    log.debug(f'Building theme template: {template_name}')

    try:
        template = env.get_template(template_name)
    except TemplateNotFound:
        log.warning(f"Template skipped: '{template_name}' not found in theme directories.")
        return

    output = _build_template(template_name, template, files, config, nav)

    if output.strip():
        output_path = os.path.join(config.site_dir, template_name)
        utils.write_file(output.encode('utf-8'), output_path)

        if template_name == 'sitemap.xml':
            log.debug(f'Gzipping template: {template_name}')
            gz_filename = f'{output_path}.gz'
            with open(gz_filename, 'wb') as f:
                timestamp = utils.get_build_timestamp()
                with gzip.GzipFile(
                    fileobj=f, filename=gz_filename, mode='wb', mtime=timestamp
                ) as gz_buf:
                    gz_buf.write(output.encode('utf-8'))
    else:
        log.info(f"Template skipped: '{template_name}' generated empty output.")


def _build_extra_template(template_name: str, files: Files, config: MkDocsConfig, nav: Navigation):
    """Build user templates which are not part of the theme."""

    log.debug(f'Building extra template: {template_name}')

    file = files.get_file_from_path(template_name)
    if file is None:
        log.warning(f"Template skipped: '{template_name}' not found in docs_dir.")
        return

    try:
        with open(file.abs_src_path, encoding='utf-8', errors='strict') as f:
            template = jinja2.Template(f.read())
    except Exception as e:
        log.warning(f"Error reading template '{template_name}': {e}")
        return

    output = _build_template(template_name, template, files, config, nav)

    if output.strip():
        utils.write_file(output.encode('utf-8'), file.abs_dest_path)
    else:
        log.info(f"Template skipped: '{template_name}' generated empty output.")


def _populate_page(page: Page, config: MkDocsConfig, files: Files, dirty: bool = False) -> None:
    """Read page content from docs_dir and render Markdown."""

    try:
        # When --dirty is used, only read the page if the file has been modified since the
        # previous build of the output.
        if dirty and not page.file.is_modified():
            return

        # Run the `pre_page` plugin event
        page = config.plugins.run_event('pre_page', page, config=config, files=files)

        page.read_source(config)

        # Run `page_markdown` plugin events.
        page.markdown = config.plugins.run_event(
            'page_markdown', page.markdown, page=page, config=config, files=files
        )

        page.render(config, files)

        # Run `page_content` plugin events.
        page.content = config.plugins.run_event(
            'page_content', page.content, page=page, config=config, files=files
        )
    except Exception as e:
        message = f"Error reading page '{page.file.src_uri}':"
        # Prevent duplicated the error message because it will be printed immediately afterwards.
        if not isinstance(e, BuildError):
            message += f' {e}'
        log.error(message)
        raise


def _build_page(
    page: Page,
    config: MkDocsConfig,
    doc_files: Sequence[File],
    nav: Navigation,
    env: jinja2.Environment,
    dirty: bool = False,
) -> None:
    """Pass a Page to theme template and write output to site_dir."""

    try:
        # When --dirty is used, only build the page if the file has been modified since the
        # previous build of the output.
        if dirty and not page.file.is_modified():
            return

        log.debug(f'Building page {page.file.src_uri}')

        # Activate page. Signals to theme that this is the current page.
        page.active = True

        context = get_context(nav, doc_files, config, page)

        # Allow 'template:' override in md source files.
        if 'template' in page.meta:
            template = env.get_template(page.meta['template'])
        else:
            template = env.get_template('main.html')

        # Run `page_context` plugin events.
        context = config.plugins.run_event(
            'page_context', context, page=page, config=config, nav=nav
        )

        # Render the template.
        output = template.render(context)

        # Run `post_page` plugin events.
        output = config.plugins.run_event('post_page', output, page=page, config=config)

        # Write the output file.
        if output.strip():
            utils.write_file(
                output.encode('utf-8', errors='xmlcharrefreplace'), page.file.abs_dest_path
            )
        else:
            log.info(f"Page skipped: '{page.file.src_uri}'. Generated empty output.")

        # Deactivate page
        page.active = False
    except Exception as e:
        message = f"Error building page '{page.file.src_uri}':"
        # Prevent duplicated the error message because it will be printed immediately afterwards.
        if not isinstance(e, BuildError):
            message += f" {e}"
        log.error(message)
        raise

def site_directory_contains_stale_files(site_directory: str) -> bool:
    """Check if the site directory contains stale files from a previous build."""

    return True if os.path.exists(site_directory) and os.listdir(site_directory) else False
