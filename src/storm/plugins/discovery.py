from __future__ import annotations

from importlib import import_module
import inspect
import pkgutil
from types import ModuleType
from typing import TypeVar


ComponentT = TypeVar("ComponentT", bound=object)


class PluginDiscoveryError(ImportError):
    """Raised when a package selected for plugin discovery cannot be imported."""


def discover_implementations(
    package: str | ModuleType,
    interface: type[ComponentT],
) -> tuple[type[ComponentT], ...]:
    """Import a package recursively and return its concrete interface subclasses."""
    root = _import_package(package)
    package_name = root.__name__
    package_path = getattr(root, "__path__", None)
    if package_path is not None:
        module_names = sorted(
            module.name
            for module in pkgutil.walk_packages(
                package_path,
                prefix=f"{package_name}.",
            )
        )
        for module_name in module_names:
            try:
                import_module(module_name)
            except Exception as error:
                raise PluginDiscoveryError(
                    f"Could not import plugin module '{module_name}'."
                ) from error

    implementations = (
        implementation
        for implementation in _subclasses(interface)
        if _belongs_to_package(implementation, package_name)
        and not inspect.isabstract(implementation)
    )
    return tuple(
        sorted(
            implementations,
            key=lambda implementation: (
                implementation.__module__,
                implementation.__qualname__,
            ),
        )
    )


def _import_package(package: str | ModuleType) -> ModuleType:
    if isinstance(package, ModuleType):
        return package
    try:
        return import_module(package)
    except Exception as error:
        raise PluginDiscoveryError(
            f"Could not import plugin package '{package}'."
        ) from error


def _subclasses(interface: type[ComponentT]) -> set[type[ComponentT]]:
    found: set[type[ComponentT]] = set()
    pending = list(interface.__subclasses__())
    while pending:
        candidate = pending.pop()
        if candidate in found:
            continue
        found.add(candidate)
        pending.extend(candidate.__subclasses__())
    return found


def _belongs_to_package(component: type[object], package_name: str) -> bool:
    return component.__module__ == package_name or component.__module__.startswith(
        f"{package_name}."
    )
