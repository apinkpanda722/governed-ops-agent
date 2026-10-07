"""Worker-shared artifacts protected for their entire build/read transaction."""

import copy
import hashlib
from collections.abc import Callable

from filelock import FileLock


def cached_artifact[T](
    tmp_path_factory, name: str, build: Callable[[], T], *, dump, load
) -> T:
    root = tmp_path_factory.getbasetemp().parent / "corpus-cache"
    root.mkdir(exist_ok=True)
    path = root / hashlib.sha256(name.encode()).hexdigest()
    with FileLock(str(path) + ".lock"):
        if not path.exists():
            value = build()
            temporary = path.with_suffix(".tmp")
            dump(value, temporary)
            temporary.replace(path)
        return copy.deepcopy(load(path))
