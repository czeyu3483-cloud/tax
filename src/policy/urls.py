"""Offline public-link checks, not network fetch authorization.

Future fetchers must also validate resolved addresses and each redirect.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit


def public_http_url(value: str) -> bool:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return False
    if re.search(r'[\x00-\x20\x7f\\]', value):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').rstrip('.').lower()
        if (parsed.scheme not in {'http', 'https'} or not host
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in {None, 80, 443} or '%' in host):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            ascii_host = host.encode('idna').decode('ascii')
            if ('.' not in ascii_host or ascii_host.endswith(
                    ('.localhost', '.local', '.internal', '.lan', '.home', '.test', '.invalid'))
                    or host == 'localhost'):
                return False
            if re.fullmatch(r'(?:0x[0-9a-f]+|[0-9.]+)', ascii_host):
                return False
            labels = ascii_host.split('.')
            return all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x)
                       for x in labels) and not labels[-1].isdigit()
        return address.is_global and not address.is_multicast
    except (ValueError, UnicodeError):
        return False


def official_public_url(value: str, domains: tuple[str, ...]) -> bool:
    if not public_http_url(value):
        return False
    host = (urlsplit(value).hostname or '').rstrip('.').lower()
    return any(host == domain or host.endswith('.' + domain) for domain in domains)
