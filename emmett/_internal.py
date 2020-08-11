# -*- coding: utf-8 -*-
"""
    emmett._internal
    ----------------

    Provides internally used helpers and objects.

    :copyright: 2014 Giovanni Barillari

    Several parts of this code comes from Flask and Werkzeug.
    :copyright: (c) 2014 by Armin Ronacher.

    :license: BSD-3-Clause
"""

import asyncio
import datetime
import os
import pkgutil
import sys
import warnings

from functools import partial
from shutil import copyfileobj
from typing import Any, Generic

import pendulum

from .typing import T


class ProxyMixin:
    @property
    def __dict__(self):
        try:
            return self._get_robj().__dict__
        except RuntimeError:
            raise AttributeError('__dict__')

    def __repr__(self):
        try:
            obj = self._get_robj()
        except RuntimeError:
            return '<%s unbound>' % self.__class__.__name__
        return repr(obj)

    def __bool__(self):
        try:
            return bool(self._get_robj())
        except RuntimeError:
            return False

    def __dir__(self):
        try:
            return dir(self._get_robj())
        except RuntimeError:
            return []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_robj(), name)

    def __setitem__(self, key, value):
        self._get_robj()[key] = value

    def __delitem__(self, key):
        del self._get_robj()[key]

    __setattr__ = lambda x, n, v: setattr(x._get_robj(), n, v)
    __delattr__ = lambda x, n: delattr(x._get_robj(), n)
    __str__ = lambda x: str(x._get_robj())
    __getitem__ = lambda x, i: x._get_robj()[i]
    __eq__ = lambda x, o: x._get_robj() == o
    __ne__ = lambda x, o: x._get_robj() != o
    __call__ = lambda x, *a, **kw: x._get_robj()(*a, **kw)
    __iter__ = lambda x: iter(x._get_robj())
    __contains__ = lambda x, i: i in x._get_robj()


class ObjectProxy(ProxyMixin, Generic[T]):
    __slots__ = ('__obj', '__name__')

    def __init__(self, obj: Any, name: str):
        object.__setattr__(self, '_ObjectProxy__obj', obj)
        object.__setattr__(self, '__name__', name)

    def _get_robj(self) -> T:
        return getattr(self.__obj, self.__name__)


class ContextVarProxy(ProxyMixin, Generic[T]):
    __slots__ = ('__obj', '__name__')

    def __init__(self, obj: Any, name: str):
        object.__setattr__(self, '_ContextVarProxy__obj', obj)
        object.__setattr__(self, '__name__', name)

    def _get_robj(self) -> T:
        return getattr(self.__obj.get(), self.__name__)


class ImmutableListMixin(object):
    _hash_cache = None

    def __hash__(self):
        if self._hash_cache is not None:
            return self._hash_cache
        rv = self._hash_cache = hash(tuple(self))
        return rv

    def __reduce_ex__(self, protocol):
        return type(self), (list(self),)

    def __delitem__(self, key):
        _is_immutable(self)

    def __iadd__(self, other):
        _is_immutable(self)
    __imul__ = __iadd__

    def __setitem__(self, key, value):
        _is_immutable(self)

    def append(self, item):
        _is_immutable(self)
    remove = append

    def extend(self, iterable):
        _is_immutable(self)

    def insert(self, pos, value):
        _is_immutable(self)

    def pop(self, index=-1):
        _is_immutable(self)

    def reverse(self):
        _is_immutable(self)

    def sort(self, cmp=None, key=None, reverse=None):
        _is_immutable(self)


class ImmutableList(ImmutableListMixin, list):
    def __repr__(self):
        return '%s(%s)' % (
            self.__class__.__name__, list.__repr__(self)
        )


class LoopFileCtxWrapper:
    __slots__ = ('_coro', '_obj')

    def __init__(self, coro):
        self._coro = coro
        self._obj = None

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self):
        self._obj = await self._coro
        return self._obj

    async def __aexit__(self, exc_type, exc, tb):
        await self._obj.close()
        self._obj = None


class LoopFileWrapper:
    __slots__ = ('_file', '_loop')

    def __init__(self, f, loop=None):
        self._file = f
        self._loop = loop or asyncio.get_running_loop()

    async def read(self, *args, **kwargs):
        return await self._loop.run_in_executor(
            None, partial(self._file.read, *args, **kwargs))

    async def write(self, *args, **kwargs):
        return await self._loop.run_in_executor(
            None, partial(self._file.write, *args, **kwargs))

    async def close(self, *args, **kwargs):
        return await self._loop.run_in_executor(
            None, partial(self._file.close, *args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._file, name)


def _is_immutable(self):
    raise TypeError('%r objects are immutable' % self.__class__.__name__)


async def _loop_open_file(loop, *args, **kwargs):
    f = await loop.run_in_executor(None, partial(open, *args, **kwargs))
    return LoopFileWrapper(f, loop)


def loop_open_file(*args, **kwargs):
    return LoopFileCtxWrapper(
        _loop_open_file(asyncio.get_running_loop(), *args, **kwargs)
    )


async def loop_copyfileobj(fsrc, fdst, length=None):
    return await asyncio.get_running_loop().run_in_executor(
        None, partial(copyfileobj, fsrc, fdst, length)
    )


#: deprecation helpers
class RemovedInNextVersionWarning(DeprecationWarning):
    pass


class deprecated(object):
    def __init__(self, old_method_name, new_method_name, class_name=None, s=0):
        self.class_name = class_name
        self.old_method_name = old_method_name
        self.new_method_name = new_method_name
        self.additional_stack = s

    def __call__(self, f):
        def wrapped(*args, **kwargs):
            warn_of_deprecation(
                self.old_method_name, self.new_method_name, self.class_name,
                3 + self.additional_stack)
            return f(*args, **kwargs)
        return wrapped


warnings.simplefilter('always', RemovedInNextVersionWarning)


def warn_of_deprecation(old_name, new_name, prefix=None, stack=2):
    msg = "%(old)s is deprecated, use %(new)s instead."
    if prefix:
        msg = "%(prefix)s." + msg
    warnings.warn(
        msg % {'old': old_name, 'new': new_name, 'prefix': prefix},
        RemovedInNextVersionWarning, stack)


#: app init helpers
def get_root_path(import_name):
    """Returns the path of the package or cwd if that cannot be found."""
    # Module already imported and has a file attribute.  Use that first.
    mod = sys.modules.get(import_name)
    if mod is not None and hasattr(mod, '__file__'):
        return os.path.dirname(os.path.abspath(mod.__file__))

    # Next attempt: check the loader.
    loader = pkgutil.get_loader(import_name)

    # Loader does not exist or we're referring to an unloaded main module
    # or a main module without path (interactive sessions), go with the
    # current working directory.
    if loader is None or import_name == '__main__':
        return os.getcwd()

    # For .egg, zipimporter does not have get_filename until Python 2.7.
    # Some other loaders might exhibit the same behavior.
    if hasattr(loader, 'get_filename'):
        filepath = loader.get_filename(import_name)
    else:
        # Fall back to imports.
        __import__(import_name)
        mod = sys.modules[import_name]
        filepath = getattr(mod, '__file__', None)

        # If we don't have a filepath it might be because we are a
        # namespace package.  In this case we pick the root path from the
        # first module that is contained in our package.
        if filepath is None:
            raise RuntimeError('No root path can be found for the provided '
                               'module "%s".  This can happen because the '
                               'module came from an import hook that does '
                               'not provide file name information or because '
                               'it\'s a namespace package.  In this case '
                               'the root path needs to be explicitly '
                               'provided.' % import_name)

    # filepath is import_name.py for a module, or __init__.py for a package.
    return os.path.dirname(os.path.abspath(filepath))


def create_missing_app_folders(app):
    try:
        for subfolder in ['languages', 'logs', 'static']:
            path = os.path.join(app.root_path, subfolder)
            if not os.path.exists(path):
                os.mkdir(path)
    except Exception:
        pass


#: monkey patches
def _pendulum_to_datetime(obj):
    return datetime.datetime(
        obj.year, obj.month, obj.day,
        obj.hour, obj.minute, obj.second, obj.microsecond,
        tzinfo=obj.tzinfo
    )


def _pendulum_to_naive_datetime(obj):
    obj = obj.in_timezone('UTC')
    return datetime.datetime(
        obj.year, obj.month, obj.day,
        obj.hour, obj.minute, obj.second, obj.microsecond
    )


def _pendulum_json(obj):
    return obj.for_json()


pendulum.DateTime.as_datetime = _pendulum_to_datetime
pendulum.DateTime.as_naive_datetime = _pendulum_to_naive_datetime
pendulum.DateTime.__json__ = _pendulum_json
