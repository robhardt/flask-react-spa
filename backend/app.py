""" Flask Application Factory Pattern
http://flask.pocoo.org/docs/0.12/patterns/appfactories/

Conventions to follow for magic to ensue:

VIEWS, MODELS, SERIALIZERS and COMMANDS ("bundles")
-----------------------------
All views/models should be contained in bundle folders.
Views should be in a file named `views.py` containing the flask.Blueprint instance.
Models should be in a file named `models.py` and should extend database.Model
Serializers should be in a file named `serializers.py` and should extend
 flask_marshmallow.sqla.ModelSchema or backend.api.ModelSerializer
Commands should be in a file named `commands.py` containing a click.Group instance.
Finally, each bundle folder must be registered in `config.py`

CLI COMMANDS
-----------------------------
Decorate custom CLI commands in `commands.py` using @cli.command()

FLASK SHELL CONTEXT
-----------------------------
Database models, serializers and app extensions will automatically be added to
the shell context, presuming the above conventions have been followed.
"""
import os
import sys

from flask import Flask as BaseFlask, session
from flask.helpers import get_debug_flag
from flask_wtf.csrf import generate_csrf

from .config import (
    BaseConfig,
    DevConfig,
    ProdConfig,
    PROJECT_ROOT,
    TEMPLATE_FOLDER,
    STATIC_FOLDER,
    STATIC_URL_PATH,
    EXTENSIONS,
    DEFERRED_EXTENSIONS,
)
from .logger import logger
from .magic import (
    get_bundles,
    get_commands,
    get_extensions,
)


class Flask(BaseFlask):
    bundles = {}
    models = {}
    serializers = {}

    def iterbundles(self):
        for bundle in self.bundles.values():
            yield bundle


def create_app():
    """Creates a pre-configured Flask application.

    Defaults to using :class:`backend.config.ProdConfig`, unless the
    :envvar:`FLASK_DEBUG` environment variable is explicitly set to "true",
    in which case it uses :class:`backend.config.DevConfig`. Also configures
    paths for the templates folder and static files.
    """
    return _create_app(
        DevConfig if get_debug_flag() else ProdConfig,
        template_folder=TEMPLATE_FOLDER,
        static_folder=STATIC_FOLDER,
        static_url_path=STATIC_URL_PATH
    )


def _create_app(config_object: BaseConfig, **kwargs):
    """Creates a Flask application.

    :param object config_object: The config class to use.
    :param dict kwargs: Extra kwargs to pass to the Flask constructor.
    """
    # WARNING: HERE BE DRAGONS!!!
    # DO NOT FUCK WITH THE ORDER OF THESE CALLS or nightmares will ensue
    app = Flask(__name__, **kwargs)
    app.bundles = dict(get_bundles())
    configure_app(app, config_object)

    extensions = dict(get_extensions(EXTENSIONS))
    register_extensions(app, extensions)

    register_blueprints(app)
    register_models(app)
    register_serializers(app)

    deferred_extensions = dict(get_extensions(DEFERRED_EXTENSIONS))
    extensions.update(deferred_extensions)
    register_extensions(app, deferred_extensions)

    register_cli_commands(app)
    register_shell_context(app, extensions)

    return app


def configure_app(app, config_object):
    """General application configuration:

    - register the app's config
    - register jinja extensions
    - register request/response cycle functions
    """
    # automatically configure a migrations folder for each bundle
    config_object.ALEMBIC['version_locations'] = [
        (bundle.name, os.path.join(PROJECT_ROOT,
                                   bundle.module_name.replace('.', os.sep),
                                   'migrations'))
        for bundle in app.iterbundles()
    ]
    app.config.from_object(config_object)

    app.jinja_env.add_extension('jinja2_time.TimeExtension')

    @app.before_request
    def enable_session_timeout():
        session.permanent = True  # set session to use PERMANENT_SESSION_LIFETIME
        session.modified = True   # reset the session timer on every request

    @app.after_request
    def set_csrf_cookie(response):
        if response:
            response.set_cookie('csrf_token', generate_csrf())
        return response


def register_extensions(app, extensions):
    """Register and initialize extensions."""
    for extension in extensions.values():
        extension.init_app(app)


def register_blueprints(app):
    """Register bundle views."""
    # disable strict_slashes on all routes by default
    if not app.config.get('STRICT_SLASHES', False):
        app.url_map.strict_slashes = False

    # register blueprints
    for bundle in app.iterbundles():
        for blueprint in bundle.blueprints:
            # rstrip '/' off url_prefix because views should be declaring their
            # routes beginning with '/', and if url_prefix ends with '/', routes
            # will end up looking like '/prefix//endpoint', which is no good
            url_prefix = (blueprint.url_prefix or '').rstrip('/')
            app.register_blueprint(blueprint, url_prefix=url_prefix)


def register_models(app):
    models = {}
    for bundle in app.iterbundles():
        for model_name, model_class in bundle.models:
            models[model_name] = model_class
    app.models = models


def register_serializers(app):
    """Register and initialize serializers."""
    serializers = {}
    for bundle in app.iterbundles():
        for _, serializer_class in bundle.serializers:
            serializers[serializer_class.Meta.model.__name__] = serializer_class
    app.serializers = serializers


def register_cli_commands(app):
    """Register all the Click commands declared in commands.py and
    each bundle's commands.py"""
    commands = list(get_commands())
    for bundle in app.iterbundles():
        if bundle.has_command_group:
            commands.append((bundle.command_group_name, bundle.command_group))
    for name, command in commands:
        if name in app.cli.commands:
            logger.error('Command name conflict: "%s" is taken.' % name)
            sys.exit(1)
        app.cli.add_command(command)


def register_shell_context(app, extensions):
    """Register variables to automatically import when running `flask shell`."""
    def shell_context():
        ctx = {}
        ctx.update(extensions)
        ctx.update(app.models)
        ctx.update(app.serializers)
        return ctx
    app.shell_context_processor(shell_context)
