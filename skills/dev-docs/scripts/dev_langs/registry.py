# -*- coding: utf-8 -*-
"""dev_langs.registry — 适配器注册表与路由。"""
from .bash_ts import BashTreeSitterAdapter
from .c_ts import CTreeSitterAdapter
from .cpp_ts import CppTreeSitterAdapter
from .java_ts import JavaTreeSitterAdapter
from .js_ts import JsTsTreeSitterAdapter
from .python_ts import PythonTreeSitterAdapter
from .text_msgsrv import MsgSrvTextAdapter

_ADAPTERS = (
    PythonTreeSitterAdapter(),
    CppTreeSitterAdapter(),
    CTreeSitterAdapter(),
    JavaTreeSitterAdapter(),
    JsTsTreeSitterAdapter(),
    BashTreeSitterAdapter(),
    MsgSrvTextAdapter(),
)

# ext -> lang（保持原 dev_inventory.EXT_LANG 的对外语义；value 为语言名）
EXT_LANG = {ext: a.lang for a in _ADAPTERS for ext in a.exts}

# 参与模块划分/文件归属的扩展名（在 LANG_TABLE 之上追加配置/接口定义类）
CODE_EXTS = frozenset(EXT_LANG) | {".launch", ".cmake", ".gradle", ".proto"}

_BY_EXT = {}
for _a in _ADAPTERS:
    for _e in _a.exts:
        _BY_EXT[_e] = _a


def get_adapter(ext):
    """按扩展名路由适配器；未注册语言返回 None（文件仍进 L0 全集）。"""
    return _BY_EXT.get(ext.lower())


def adapters():
    return list(_ADAPTERS)


def available_extractors():
    """语言 -> {extractor, version}（doctor/报告展示用）。"""
    import importlib
    from .base import grammar_module, grammar_version
    out = {}
    for a in _ADAPTERS:
        if getattr(a, "grammar", None):
            module_name, _ = grammar_module(a.grammar)
            try:
                importlib.import_module(module_name)
                out[a.lang] = {"extractor": "tree-sitter",
                               "version": grammar_version(a.grammar) or "?"}
            except ImportError:
                out[a.lang] = {"extractor": "missing", "version": ""}
        else:
            out[a.lang] = {"extractor": "text", "version": ""}
    return out
