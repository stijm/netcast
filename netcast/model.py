from __future__ import annotations  # Python 3.8

import collections.abc
import functools
import inspect
import threading
from typing import Any, ClassVar, Type, TypeVar, Union

from netcast.constants import LEAST, GREATEST, MISSING
from netcast.driver import Driver, DriverMeta
from netcast.serializer import Serializer
from netcast.tools import strings
from netcast.tools.inspection import combined_getattr


__all__ = (
    "ComponentStack",
    "FilteredComponentStack",
    "VersionAwareComponentStack",
    "ComponentDescriptor",
    "ComponentArgumentT",
    "ComponentT",
    "Model",
    "is_component",
    "model"
)


class ComponentStack:
    def __init__(self):
        self._components = []
        self._lock = threading.RLock()

    @classmethod
    def transform_submodel(cls, submodel) -> Model:
        return submodel

    @classmethod
    def transform_serializer(
        cls, component: Serializer, settings: dict | None = None
    ) -> Serializer:
        if settings is None:
            settings = {}
        if getattr(component, "__taken__", False):
            return component
        component = component(**settings)
        component.__taken__ = True
        return component

    def transform_component(
        self, component: ComponentArgumentT, *, settings=None, default_name=None
    ) -> ComponentT | None:
        if settings is None:
            settings = {}
        settings.setdefault("name", default_name)
        if isinstance(component, type) and not issubclass(component, Model):
            component = component(**settings)
        elif isinstance(component, type) and issubclass(component, Model):
            component = self.transform_submodel(component)
        else:
            assert isinstance(component, Serializer)
            component = self.transform_serializer(component, settings)
        return component

    def add(
        self,
        component: ComponentArgumentT,
        *,
        settings: dict | None = None,
        default_name: str | None = None,
    ):
        if isinstance(settings, dict):
            settings = settings.copy()
        transformed = self.transform_component(
            component=component, default_name=default_name, settings=settings
        )
        self.push(transformed)
        return transformed

    def insert(self, idx: int, component: ComponentT):
        self._lock.acquire()
        self._components.insert(idx, component)
        self._lock.release()

    def discard(self, component: ComponentT):
        self._lock.acquire()
        try:
            idx = self._components.index(component)
        except IndexError:
            idx = None
        self._lock.release()
        if idx is not None:
            self.pop(idx)

    def push(self, component: ComponentT):
        self._lock.acquire()
        self._components.append(component)
        self._lock.release()

    def pop(self, index: int = -1) -> ComponentT | None:
        self._lock.acquire()
        obj = self._components.pop(index)
        self._lock.release()
        return obj

    def get(self, index: int = -1, settings: dict | None = None) -> ComponentT | None:
        self._lock.acquire()
        try:
            obj = self._components[index]
        except IndexError:
            obj = None
        self._lock.release()
        return obj

    def clear(self):
        self._lock.acquire()
        self._components.clear()
        self._lock.release()

    @property
    def size(self) -> int:
        return len(self._components)

    def get_matching_components(self, settings=None) -> dict[str, ComponentT]:
        if settings is None:
            settings = {}
        matched = {}
        for idx in range(self.size):
            component = self.get(idx, settings)
            if component is not None:
                matched[component.name] = component
        return matched

    def __del__(self):
        for component in self._components:
            component.__taken__ = False
        self._components.clear()

    def __repr__(self) -> str:
        name = type(self).__name__
        return f"<{name} {self._components}>"


class FilteredComponentStack(ComponentStack):
    def predicate(self, component, settings):
        return True

    def get(self, index=-1, settings=None):
        if settings is None:
            settings = {}
        component = super().get(index, settings)
        if component is None:
            return component
        if not self.predicate(component, settings):
            component = None
        return component


class VersionAwareComponentStack(FilteredComponentStack):
    """
    A very simple and basic versioning layer.  `foo = Int64(version_added=1, version_removed=5)`
    will inform the model to include `foo` component only if `1 <= <version> <= 5`.
    """

    def __init__(
        self,
        *,
        settings_version_field="version",
        since_field="settings[version_added]",
        until_field="settings[version_removed]",
        default_version=GREATEST,
        default_since_version=LEAST,
        default_until_version=GREATEST,
    ):
        super().__init__()
        self.settings_version_field = settings_version_field
        self.since_version_field = since_field
        self.until_version_field = until_field
        self.default_version = default_version
        self.default_since_version = default_since_version
        self.default_until_version = default_until_version

    def predicate_version(self, component, settings):
        version = settings.get(self.settings_version_field, self.default_version)
        since_version = combined_getattr(
            component, self.since_version_field, self.default_since_version
        )
        until_version = combined_getattr(
            component, self.until_version_field, self.default_until_version
        )
        introduced = since_version <= version
        up_to_date = until_version >= version
        return introduced and up_to_date

    def predicate(self, component, settings):
        return self.predicate_version(component, settings)


class _BaseDescriptor:
    component: ComponentT

    def __getattr__(self, item):
        return getattr(self.component, item)


class ComponentDescriptor(_BaseDescriptor):
    def __init__(self, component: ComponentT):
        self.component = component
        self._value = MISSING
        self.aliases = []

    @property
    def value(self):
        return self._value

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return self.value

    def __set__(self, instance, value):
        self._value = value

    def __call__(self, value):
        self.__set__(None, value)
        return value


class AliasDescriptor(_BaseDescriptor):
    def __init__(self, link_to):
        self.linked_to = link_to

    @property
    def component(self):
        return self.linked_to.component

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return self.value

    def __set__(self, instance, value):
        self.component.__set__(instance, value)

    def __call__(self, value):
        self.__set__(None, value)
        return value


class Model:
    stack: ClassVar[ComponentStack]
    settings: ClassVar[dict[str, Any]]
    descriptor_class = ComponentDescriptor
    descriptor_alias_class = AliasDescriptor

    def __init__(
        self,
        name: str | None = None,
        defaults: dict[str, Any] | None = None,
        **settings,
    ):
        self._name = name
        if defaults is None:
            defaults = {}
        self._defaults = defaults

        for key in set(settings):
            if key in self._descriptors:
                self[key] = settings.pop(key)

        self.settings = settings

    @property
    def name(self):
        if self._name is None:
            self._name = type(self).__name__
        return self._name

    @property
    def default(self):
        defaults = self._defaults.copy()
        for name, desc in self._descriptors.items():
            default = desc.component.default
            if default is not MISSING:
                defaults[name] = default
        return defaults

    def resolve_serializer(self, driver_or_serializer, settings=None):
        if settings is None:
            settings = {}
        settings = {**settings, **self.settings}

        if isinstance(driver_or_serializer, DriverMeta):
            driver = driver_or_serializer
            components = self.get_matching_components(**settings).values()
            serializer_arg = None
            serializer = driver.get_model_serializer(
                serializer_arg,
                components=components,
                settings=settings
            )
        else:
            serializer = driver_or_serializer
            serializer = serializer.get_dependency(
                serializer,
                name=self.name,
                default=self.default,
                **self.settings
            )
        return serializer

    @property
    def state(self):
        return self.get_state()

    def get_state(self, fill_value=MISSING, **settings):
        descriptors = self.get_matching_descriptors(**settings)
        state = {}

        for name, desc in descriptors.items():
            value = desc.value

            if value is MISSING:
                value = desc.component.default
            if value is MISSING:
                if fill_value is not MISSING:
                    value = fill_value
                else:
                    raise ValueError(
                        f"missing required {type(desc.component).__name__} "
                        f"value for serializer named {desc.component.name}"
                    )

            state[name] = value

        return state

    def get_matching_components(self, **settings):
        settings = {**settings, **self.settings}
        return self.stack.get_matching_components(settings)

    def get_matching_descriptors(self, **settings):
        namespace = set(self.get_matching_components(**settings))
        descriptors = {
            name: desc for name, desc in self._descriptors.items() if name in namespace
        }
        return descriptors

    def dump(
            self,
            driver_or_serializer: Type[Driver] | Serializer,
            **settings
    ):
        serializer = self.resolve_serializer(driver_or_serializer, settings)
        return serializer.dump(self.get_state(), settings=settings)

    def load(
            self,
            driver_or_serializer: Type[Driver] | Serializer,
            dump,
            **settings
    ):
        serializer = self.resolve_serializer(driver_or_serializer, settings)
        load = serializer.load(dump, settings=settings)
        state = self.load_state(load)
        self.set_state(state)
        return self

    @functools.singledispatchmethod
    def load_state(self, load) -> dict | tuple[tuple[str, Any], ...]:
        raise TypeError(
            f"unsupported state type: {strings.truncate(type(load).__name__)}"
        )

    @load_state.register
    def load_state_from_sequence(self, load: collections.abc.Sequence) -> dict:
        return dict(zip(self._descriptors, load))

    @load_state.register
    def load_state_from_mapping(self, load: collections.abc.Mapping) -> dict:
        return dict(load)

    def set_state(self, state):
        if callable(getattr(state, "items", None)):
            state = state.items()
        for item, value in state:
            try:
                self[item] = value
            except KeyError:
                pass

    def __iter__(self):
        for name in self._descriptors:
            yield name, self[name]

    def __setitem__(self, key, value):
        self._descriptors[key].__set__(self, value)

    def __getitem__(self, key):
        return self._descriptors[key].__get__(self, None)

    def __setattr__(self, key, value):
        if key in getattr(self, "_descriptors", {}):
            self._descriptors[key].__set__(self, value)
            return
        object.__setattr__(self, key, value)

    def __eq__(self, other):
        return self.get_state() == other.get_state()

    def __init_subclass__(
        cls,
        stack=None,
        from_members=None,
        stack_class=VersionAwareComponentStack,
        **settings,
    ):
        if from_members is None:
            from_members = stack is None

        if stack is None:
            stack = stack_class()

        cls.stack = stack
        cls.settings = settings
        descriptors = {}
        if from_members:
            seen = {}
            for attr_name, component in inspect.getmembers(cls, is_component):
                name = attr_name
                seen_descriptor = seen.get(id(component))
                if seen_descriptor is None:
                    transformed = cls.stack.add(
                        component,
                        default_name=attr_name,
                        settings=cls.settings
                    )
                    descriptor = cls.descriptor_class(transformed)

                    setattr(cls, transformed.name, descriptor)
                else:
                    descriptor = seen_descriptor
                    alias_descriptor = cls.descriptor_alias_class(descriptor)
                    setattr(cls, attr_name, alias_descriptor)
                descriptors[name] = descriptor
                seen[id(component)] = descriptor
            seen.clear()
        else:
            for name, component in cls.stack.get_matching_components(**settings):
                setattr(cls, name, cls.descriptor_class(component))
        cls._descriptors = descriptors


ComponentT = TypeVar("ComponentT", Serializer, Model)
ComponentArgumentT = Union[ComponentT, Type[ComponentT]]


def is_component(maybe_component, accept_type=True):
    ok_instance = isinstance(maybe_component, (Serializer, Model))
    ok_type = accept_type and (
        isinstance(maybe_component, type)
        and issubclass(maybe_component, (Serializer, Model))
    )
    return ok_instance or ok_type


def model(
    *components,
    stack=None,
    name=None,
    model_class=Model,
    stack_class=VersionAwareComponentStack,
    serializer=None,
    **settings,
):
    if stack is None:
        stack = stack_class()
    for component in components:
        stack.add(component, settings=settings)
    if name is None:
        name = "model_" + str(id(stack))
    return type(name, (model_class,), {}, stack=stack, serializer=serializer)
