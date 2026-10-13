"""Крошечное приложение: корзина заказа и расчёт суммы."""

from importlib import metadata

try:
    __version__ = metadata.version("workflow-sandbox")
except metadata.PackageNotFoundError:  # запуск из исходников без установки пакета
    __version__ = "unknown"
