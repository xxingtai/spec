# -*- coding: utf-8 -*-
"""dev_langs.java_ts — Java 适配器（tree-sitter 实现）。

语义与原正则启发版对齐并升级为 reliable：
- method_declaration / constructor_declaration → kind=method（qname=方法名）
- class_declaration / interface_declaration → kind=class
- 注解端点：@GetMapping/@PostMapping/.../@RequestMapping（沿用旧 _JAVA_ANN 口径）
- scan_deps：import_declaration
"""
import re

from .base import LanguageAdapter, node_text, parse_tree

_JAVA_ANN = re.compile(
    r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\s*"
    r"\(\s*(?:value\s*=\s*)?\"([^\"]+)\"\s*(?:,\s*method\s*=\s*(?:RequestMethod\.)?(\w+))?\)?")

_VERB = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
         "DeleteMapping": "DELETE", "PatchMapping": "PATCH"}


class JavaTreeSitterAdapter(LanguageAdapter):
    lang = "java"
    exts = (".java",)
    grammar = "java"

    def _scan(self, data, rel_path, module_id, counters):
        tree, root = parse_tree(self.grammar, data)
        text = data.decode("utf-8", "replace")
        symbols, endpoints = [], []
        cls_stack = []

        def on_class(node):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return None
            name = node_text(data, name_node)
            kind = "class"
            symbols.append(self._symbol(
                self._fun_id(counters), kind, module_id, name, rel_path,
                node.start_point[0] + 1,
                node_text(data, node).split("{", 1)[0].strip()[:200]))
            return name

        def on_method(node):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return
            name = node_text(data, name_node)
            cls = cls_stack[-1] if cls_stack else None
            symbols.append(self._symbol(
                self._fun_id(counters), "method", module_id,
                "%s.%s" % (cls, name) if cls else name, rel_path,
                node.start_point[0] + 1,
                node_text(data, node).split("{", 1)[0].strip()[:200],
                cls=cls))

        def visit(node):
            if node.type in ("class_declaration", "interface_declaration"):
                name = on_class(node)
                if name:
                    cls_stack.append(name)
                for ch in node.children:
                    visit(ch)
                if name:
                    cls_stack.pop()
                return
            if node.type in ("method_declaration", "constructor_declaration"):
                on_method(node)
            for ch in node.children:
                visit(ch)

        visit(root)

        # 注解端点（文本级口径与旧版一致；注解参数树形解析收益低）
        for m in _JAVA_ANN.finditer(text):
            verb = (m.group(3) or _VERB.get(m.group(1), "*")).upper()
            endpoints.append({
                "id": self._api_id(counters), "kind": "endpoint", "module": module_id,
                "method": verb, "path": m.group(2) or "", "handler": "",
                "file": rel_path, "line": text[:m.start()].count("\n") + 1,
                "confidence": "reliable", "extractor": "tree-sitter",
            })
        return symbols, endpoints, [], []

    def scan_deps(self, data):
        tree, root = parse_tree(self.grammar, data)
        out = []
        for node in walk_all(root):
            if node.type == "import_declaration":
                out.append(node_text(data, node).replace("import", "").strip().rstrip(";"))
        return out


def walk_all(node):
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        for ch in reversed(cur.children):
            stack.append(ch)
