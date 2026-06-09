"""HTML 模板加载"""

import os

_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template.html")
with open(_TEMPLATE_PATH, "r", encoding="utf-8") as _f:
    HTML_TMPL = _f.read()
