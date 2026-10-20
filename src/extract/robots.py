"""robots.txt checking per RFC 9309 (D9): 2xx parsed, 4xx allow all, 5xx/network = unreachable.

R20: 429 (after the client's retries) is deliberately treated as unreachable, i.e. disallow,
not as a 4xx allow-all. Rate limiting means the host is asking us to back off and we cannot
read its rules; the conservative choice matches Google's handling of 429 as a server error.
Bodies are decoded as utf-8-sig so a leading BOM does not corrupt the first directive."""
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from src.cache import ApiError, AuthError, SourceClient


class RobotsChecker:
    """One parser per scheme+host, kept in memory for the life of the checker."""

    def __init__(self, client: SourceClient, user_agent: str):
        self.client = client
        self.user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}  # None = unreachable

    def _load(self, scheme: str, netloc: str) -> RobotFileParser | None:
        rp = RobotFileParser()
        try:
            resp = self.client.request("GET", f"{scheme}://{netloc}/robots.txt")
        except AuthError:  # 401/403 is a 4xx: no robots restrictions
            rp.allow_all = True
            return rp
        except ApiError as e:
            if e.status is not None and 400 <= e.status < 500 and e.status != 429:
                rp.allow_all = True
                return rp
            return None
        if 200 <= resp.status < 300:
            rp.parse(resp.content.decode("utf-8-sig", errors="replace").splitlines())
        elif 400 <= resp.status < 500:
            rp.allow_all = True
        else:
            return None
        return rp

    def allowed(self, url: str) -> tuple[bool, str | None]:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        if key not in self._parsers:
            self._parsers[key] = self._load(parts.scheme, parts.netloc)
        rp = self._parsers[key]
        if rp is None:
            return False, "robots_unreachable"
        return rp.can_fetch(self.user_agent, url), None
