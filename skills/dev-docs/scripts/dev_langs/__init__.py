# -*- coding: utf-8 -*-
"""dev_langs — 语言适配器层（tree-sitter 全语言统一抽取）。

对外出口：
- EXT_LANG: {ext: lang}（原 dev_inventory.EXT_LANG 语义，单一事实源迁至此）
- CODE_EXTS: 参与模块划分/文件归属的扩展名全集
- get_adapter(ext): 按扩展名路由适配器（未注册返回 None）
- available_extractors(): 语言 -> {extractor, version}

适配器模式：每语言一个 LanguageAdapter 子类（base.py），tree-sitter 为运行时
硬依赖；ROS .msg/.srv 无官方 grammar，用文本解析适配器（同接口，extractor=text）。
"""
from .base import (  # noqa: F401
    LanguageAdapter,
    docstrings_by_def_line,
    get_parser,
    identifier_counts,
    is_decl_line,
    is_test_file,
    line_kind_ts,
    macro_defs,
)
from .registry import CODE_EXTS, EXT_LANG, adapters, available_extractors, get_adapter  # noqa: F401
