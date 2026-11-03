"""Поддержка прокси (PROXY в .env), например: socks5://user:pass@1.2.3.4:1080 или http://1.2.3.4:3128."""
from urllib.parse import unquote, urlparse


def telethon_proxy(url: str | None) -> dict | None:
    """Преобразует URL прокси в формат Telethon (нужен пакет python-socks)."""
    if not url:
        return None
    p = urlparse(url)
    scheme = (p.scheme or "socks5").lower()
    if scheme.startswith("socks5"):
        proxy_type = "socks5"
    elif scheme.startswith("socks4"):
        proxy_type = "socks4"
    elif scheme in ("http", "https"):
        proxy_type = "http"
    else:
        raise ValueError(f"Неподдерживаемый тип прокси: {scheme}")
    proxy = {"proxy_type": proxy_type, "addr": p.hostname, "port": p.port, "rdns": True}
    if p.username:
        proxy["username"] = unquote(p.username)
        proxy["password"] = unquote(p.password or "")
    return proxy


def aiohttp_connector(url: str | None):
    """Коннектор aiohttp через прокси (нужен пакет aiohttp-socks) или None."""
    if not url:
        return None
    from aiohttp_socks import ProxyConnector

    return ProxyConnector.from_url(url)
