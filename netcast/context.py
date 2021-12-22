from __future__ import annotations

import abc
import asyncio
import collections.abc
import functools
import io
import queue
from typing import Type, ForwardRef, Sequence

from netcast.toolkit.collections import AttributeDict, MemoryDict, MemoryList

CT, C = Type["Context"], ForwardRef("Context")


class ContextHook:
    prepared_contexts = MemoryList()

    @classmethod
    def on_modify(cls, context, func, *args, **kwargs):
        """Anytime a context is going to be modified, this method is called."""

    @classmethod
    def is_prepared(cls, context):
        return context in cls.prepared_contexts

    @classmethod
    def on_prepare(cls, context):
        cls.prepared_contexts.append(context)


class Context(metaclass=abc.ABCMeta):
    """
    All context classes must derive from this class.
    Should not be used directly - choose a proper subclass instead (or create your own).
    If subclassing, remember to call :class:`ModifyHandle` in all modification methods
    in order to make modification hooks work.
    """


def _hooked_method(method, hook, cls=None):
    if method is None:
        raise TypeError(
            f'method {method!r} '
            f'{"of " + repr(cls) + " " if cls is not None else ""}'
            f'does not exist'
        )

    @functools.wraps(method)
    def _method_wrapper(self, *args, **kwargs):
        bound_method = getattr(self, method.__name__)
        hook(self, bound_method, *args, **kwargs)
        return method(self, *args, **kwargs)

    return _method_wrapper


def _wrap_to_ctx(bases, hooked_methods=(), name=None, doc=None, init_subclass=None):
    if isinstance(bases, Sequence):
        if not bases:
            raise ValueError('at least 1 base class is required')
        cls = bases[0]
        if len(bases) == 1:
            bases += (Context,)
    else:
        cls = bases
        bases = (cls, Context)
    env = {**({'__doc__': doc} if doc else {})}
    for method in hooked_methods:
        method = (
            getattr(cls, method, None)
            if isinstance(method, str)
            else method
        )
        env[method.__name__] = _hooked_method(method, hook=ContextHook.on_modify, cls=cls)
    if name is None:
        cls_name = cls.__name__
        suffix = 'Context'
        if cls_name:
            f = cls_name[0].upper()
            if len(cls_name) > 1:
                name = f + cls_name[1:] + suffix
            else:
                name = f + suffix
        else:
            raise ValueError('class name was not provided')
    if init_subclass is None:
        init_subclass = {}
    return type(name, bases, env, **init_subclass)


_list_modifiers = (
    'append', 'extend', 'insert', 'pop', 'remove', 'reverse', '__setitem__', '__delitem__'
)
_deque_modifiers = _list_modifiers + ('appendleft', 'extendleft', 'popleft')
_dict_modifiers = ('__setitem__',)
_queue_modifiers = ('_put', '_get')
_io_modifiers = ('write', 'read', 'seek', 'close')

ListContext = _wrap_to_ctx(list, _list_modifiers)
DequeContext = _wrap_to_ctx(collections.deque, _deque_modifiers)
DictContext = _wrap_to_ctx(AttributeDict, _dict_modifiers)
ByteArrayContext = _wrap_to_ctx(bytearray, _list_modifiers)
MemoryDictContext = _wrap_to_ctx(MemoryDict, _dict_modifiers)
QueueContext = _wrap_to_ctx(queue.Queue, _queue_modifiers)
PriorityQueueContext = _wrap_to_ctx(queue.PriorityQueue, _queue_modifiers)
LifoQueueContext = _wrap_to_ctx(queue.LifoQueue, _queue_modifiers)
AsyncioQueueContext = _wrap_to_ctx(asyncio.Queue, _queue_modifiers, name='AsyncioQueue')
AsyncioPriorityQueueContext = _wrap_to_ctx(asyncio.PriorityQueue, _queue_modifiers, name='AsyncioPriorityQueueContext')  # noqa: E501
AsyncioLifoQueueContext = _wrap_to_ctx(asyncio.LifoQueue, _queue_modifiers, name='AsyncioLifoQueueContext')  # noqa: E501
FileIOContext = _wrap_to_ctx(io.FileIO, _io_modifiers)
BytesIOContext = _wrap_to_ctx(io.BytesIO, _io_modifiers)
StringIOContext = _wrap_to_ctx(io.StringIO, _io_modifiers)

# shortcuts
LContext = ListContext
DQContext = DequeContext
DContext = DictContext
BContext = BAContext = ByteContext = ByteArrayContext
MDContext = MemoryDictContext
QContext = QueueContext
PQContext = PriorityQueueContext
LQContext = LifoQueueContext
AQContext = AsyncioQueueContext
APQContext = AsyncioPriorityQueueContext
ALQContext = AsyncioLifoQueueContext
FIOContext = FileIOContext
BIOContext = BytesIOContext
SIOContext = StringIOContext
