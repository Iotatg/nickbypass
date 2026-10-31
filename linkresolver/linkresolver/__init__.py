"""linkresolver - short link / ad gate resolver.

Usage:
    from linkresolver import resolve
    r = resolve("https://tinyurl.com/xxxx")
    print(r.final_url)
"""

from .engine import Resolver, normalize
from .models import Result, Step

__all__ = ["Resolver", "Result", "Step", "resolve", "normalize"]
__version__ = "1.0.0"


def resolve(url: str, browser: bool = True, timeout: float = 25.0) -> Result:
    return Resolver(timeout=timeout, allow_browser=browser).resolve(url)
