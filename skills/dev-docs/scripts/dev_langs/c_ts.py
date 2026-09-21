# -*- coding: utf-8 -*-
"""dev_langs.c_ts — C 适配器（复用 C++ 规则，切换 c 语法）。"""
from .cpp_ts import CppTreeSitterAdapter


class CTreeSitterAdapter(CppTreeSitterAdapter):
    lang = "c"
    exts = (".c",)
    grammar = "c"
