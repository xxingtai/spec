# -*- coding: utf-8 -*-
"""dev_langs.bash_ts — Shell 适配器（tree-sitter 实现）。

- function_definition（name 字段为 word）→ kind=function
- scan_deps：source ./x.sh 的依赖字面量
"""
from .base import LanguageAdapter, node_text, parse_tree


class BashTreeSitterAdapter(LanguageAdapter):
    lang = "bash"
    exts = (".sh",)
    grammar = "bash"

    def _scan(self, data, rel_path, module_id, counters):
        tree, root = parse_tree(self.grammar, data)
        symbols, deps = [], []
        for node in _walk(root):
            if node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                name = node_text(data, name_node) if name_node else None
                if not name:
                    continue
                symbols.append(self._symbol(
                    self._fun_id(counters), "function", module_id, name, rel_path,
                    node.start_point[0] + 1, "%s()" % name))
            elif node.type == "command" and node.child_by_field_name("name") is not None:
                cmd = node_text(data, node.child_by_field_name("name"))
                if cmd == "source" or cmd == ".":
                    args = [node_text(data, c) for c in node.children[1:]]
                    if args:
                        deps.append(args[0].strip("\"'")[:120])
        return symbols, [], [], []

    def scan_deps(self, data):
        _, _, deps, _ = self._scan(data, "", "", {"fun": 0, "api": 0, "msg": 0, "srv": 0})
        return deps


def _walk(node):
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        for ch in reversed(cur.children):
            stack.append(ch)
