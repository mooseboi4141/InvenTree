"""Helpers for plugin app."""

import inspect
import logging
import os
import pathlib
import pkgutil
import sysconfig
import traceback
from importlib.metadata import entry_points

from django import template
from django.conf import settings
from django.core.exceptions import AppRegistryNotReady
from django.db.utils import IntegrityError

logger = logging.getLogger('inventree')


# region logging / errors
class IntegrationPluginError(Exception):
    """Error that encapsulates another error and adds the path / reference of the raising plugin."""

    def __init__(self, path, message):
        """Init a plugin error.

        Args:
            path: Path on which the error occurred - used to find out which plugin it was
            message: The original error message
        """
        self.path = path
        self.message = message

    def __str__(self):
        """Returns the error message."""
        return self.message  # pragma: no cover


class MixinImplementationError(ValueError):
    """Error if mixin was implemented wrong in plugin.

    Mostly raised if constant is missing
    """

    pass


class MixinNotImplementedError(NotImplementedError):
    """Error if necessary mixin function was not overwritten."""

    pass


def log_error(error, reference: str = 'general'):
    """Log an plugin error."""
    from plugin import registry

    # make sure the registry is set up
    if reference not in registry.errors:
        registry.errors[reference] = []

    # add error to stack
    registry.errors[reference].append(error)


def handle_error(error, do_raise: bool = True, do_log: bool = True, log_name: str = ''):
    """Handles an error and casts it as an IntegrationPluginError."""
    package_path = traceback.extract_tb(error.__traceback__)[-1].filename
    install_path = sysconfig.get_paths()['purelib']

    try:
        package_name = pathlib.Path(package_path).relative_to(install_path).parts[0]
    except ValueError:
        # is file - loaded -> form a name for that
        try:
            path_obj = pathlib.Path(package_path).relative_to(settings.BASE_DIR)
            path_parts = [*path_obj.parts]
            path_parts[-1] = path_parts[-1].replace(
                path_obj.suffix, ''
            )  # remove suffix

            # remove path prefixes
            if path_parts[0] == 'plugin':
                path_parts.remove('plugin')
                path_parts.pop(0)
            else:
                path_parts.remove('plugins')  # pragma: no cover

            package_name = '.'.join(path_parts)
        except Exception:
            package_name = package_path

    if do_log:
        log_kwargs = {}
        if log_name:
            log_kwargs['reference'] = log_name
        log_error({package_name: str(error)}, **log_kwargs)

    if do_raise:
        # do a straight raise if we are playing with environment variables at execution time, ignore the broken sample
        if (
            settings.TESTING_ENV
            and package_name != 'integration.broken_sample'
            and isinstance(error, IntegrityError)
        ):
            raise error  # pragma: no cover

        raise IntegrationPluginError(package_name, str(error))


def get_entrypoints():
    """Returns list for entrypoints for InvenTree plugins."""
    return entry_points().get('inventree_plugins', [])


# endregion


# region git-helpers
def get_git_log(path):
    """Get dict with info of the last commit to file named in path."""
    import datetime

    from dulwich.repo import NotGitRepository, Repo

    from InvenTree.ready import isInTestMode

    output = None
    path = os.path.abspath(path)

    if os.path.exists(path) and os.path.isfile(path):
        path = os.path.dirname(path)

    # only do this if we are not in test mode
    if not isInTestMode():  # pragma: no cover
        try:
            repo = Repo(path)
            head = repo.head()
            commit = repo[head]

            output = [
                head.decode(),
                commit.author.decode().split('<')[0][:-1],
                commit.author.decode().split('<')[1][:-1],
                datetime.datetime.fromtimestamp(commit.author_time).isoformat(),
                commit.message.decode().split('\n')[0],
            ]
        except NotGitRepository:
            pass

    if not output:
        output = 5 * ['']  # pragma: no cover

    return {
        'hash': output[0],
        'author': output[1],
        'mail': output[2],
        'date': output[3],
        'message': output[4],
    }


# endregion


# region plugin finders
def get_modules(pkg, path=None):
    """Get all modules in a package."""
    context = {}

    if path is None:
        path = pkg.__path__
    elif type(path) is not list:
        path = [path]

    for loader, name, _ in pkgutil.walk_packages(path):
        try:
            module = loader.find_module(name).load_module(name)
            pkg_names = getattr(module, '__all__', None)
            for k, v in vars(module).items():
                if not k.startswith('_') and (pkg_names is None or k in pkg_names):
                    context[k] = v
            context[name] = module
        except AppRegistryNotReady:  # pragma: no cover
            pass
        except Exception as error:
            # this 'protects' against malformed plugin modules by more or less silently failing

            # log to stack
            log_error({name: str(error)}, 'discovery')

    return [v for k, v in context.items()]


def get_classes(module):
    """Get all classes in a given module."""
    return inspect.getmembers(module, inspect.isclass)


def get_plugins(pkg, baseclass, path=None):
    """Return a list of all modules under a given package.

    - Modules must be a subclass of the provided 'baseclass'
    - Modules must have a non-empty NAME parameter
    """
    plugins = []

    modules = get_modules(pkg, path=path)

    # Iterate through each module in the package
    for mod in modules:
        # Iterate through each class in the module
        for item in get_classes(mod):
            plugin = item[1]
            if issubclass(plugin, baseclass) and plugin.NAME:
                plugins.append(plugin)

    return plugins


# endregion


# region templates
def render_template(plugin, template_file, context=None):
    """Locate and render a template file, available in the global template context."""
    try:
        tmp = template.loader.get_template(template_file)
    except template.TemplateDoesNotExist:
        logger.exception(
            "Plugin %s could not locate template '%s'", plugin.slug, template_file
        )

        return f"""
        <div class='alert alert-block alert-danger'>
        Template file <em>{template_file}</em> does not exist.
        </div>
        """

    # Render with the provided context
    html = tmp.render(context)

    return html


def render_text(text, context=None):
    """Locate a raw string with provided context."""
    ctx = template.Context(context)

    return template.Template(text).render(ctx)


# endregion
