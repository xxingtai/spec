# -*- coding: utf-8 -*-
"""dev_langs.js_ts — JavaScript/TypeScript 适配器（tree-sitter 实现）。

- function_declaration / method_definition / class_declaration
- 箭头函数赋值（const f = (…) => …）→ kind=function（对齐旧 _TS_CONSTFN 语义）
- scan_deps：import 语句源
- 语法：.js/.mjs/.cjs → javascript；.ts → typescript；.tsx → tsx
"""
from .base import LanguageAdapter, node_text, parse_tree


def _grammar_for(ext):
    if ext in (".ts",):
        return "typescript"
    if ext in (".tsx",):
        return "tsx"
    return "javascript"


class JsTsTreeSitterAdapter(LanguageAdapter):
    lang = "javascript"
    exts = (".js", ".mjs", ".cjs", ".ts", ".tsx")
    grammar = "javascript"   # .ts/.tsx 动态切换 typescript/tsx 语法（_grammar_for）

    def _scan(self, data, rel_path, module_id, counters):
        grammar = _grammar_for("." + rel_path.lower().rsplit(".", 1)[-1])
        tree, root = parse_tree(grammar, data)
        symbols, deps = [], []
        cls_stack = []

        def on_class(node):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return None
            name = node_text(data, name_node)
            symbols.append(self._symbol(
                self._fun_id(counters), "class", module_id, name, rel_path,
                node.start_point[0] + 1,
                node_text(data, node).split("{", 1)[0].strip()[:200]))
            return name

        def add_fn(name, node, cls=None, kind="function"):
            if not name:
                return
            symbols.append(self._symbol(
                self._fun_id(counters), "method" if cls else kind, module_id,
                "%s.%s" % (cls, name) if cls else name, rel_path,
                node.start_point[0] + 1,
                node_text(data, node).split("{", 1)[0].strip()[:200],
                cls=cls))

        def visit(node):
            if node.type in ("class_declaration", "class"):
                name = on_class(node)
                if name:
                    cls_stack.append(name)
                for ch in node.children:
                    visit(ch)
                if name:
                    cls_stack.pop()
                return
            if node.type == "function_declaration":
                name_node = node.child_by_field_name("name")
                add_fn(node_text(data, name_node) if name_node else None, node)
            elif node.type == "method_definition":
                name_node = node.child_by_field_name("name")
                add_fn(node_text(data, name_node) if name_node else None, node,
                       cls=cls_stack[-1] if cls_stack else None)
            elif node.type in ("lexical_declaration", "variable_declaration"):
                for d in node.children:
                    if d.type == "variable_declarator":
                        name_node = d.child_by_field_name("name")
                        value = d.child_by_field_name("value")
                        if value is not None and value.type in ("arrow_function", "function"):
                            add_fn(node_text(data, name_node) if name_node else None, d)
            elif node.type == "import_statement":
                src = node.child_by_field_name("source")
                if src is not None:
                    deps.append(node_text(data, src).strip("\"'")[:120])
            for ch in node.children:
                visit(ch)

        visit(root)
        return symbols, [], [], []

    def scan_deps(self, data):
        # 复用主扫描（deps 已在 _scan 中收集）；此处独立解析避免状态耦合
        tree, root = parse_tree("javascript", data)
        out = []
        for node in _walk(root):
            if node.type == "import_statement":
                src = node.child_by_field_name("source")
                if src is not None:
                    out.append(node_text(data, src).strip("\"'")[:120])
        return out


def _walk(node):
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        for ch in reversed(cur.children):
            stack.append(ch)
