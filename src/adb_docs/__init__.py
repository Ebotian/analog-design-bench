"""Documentation as a rendered artifact: facts, tagged blocks, index.

Documents under version control are outputs.  Their editable source lives in
``docs/src/``; measured numbers are placeholders resolved from committed
artifacts, so a document cannot drift away from the data it describes.
"""

from __future__ import annotations

__all__ = ["facts", "metrics", "render"]
