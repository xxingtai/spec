# -*- coding: utf-8 -*-
"""dev_langs.cpp_ts — C/C++ 适配器（tree-sitter 实现）。

收集范围：
- function_definition：.cpp/.cc/.cxx 定义 + 纯头文件内联定义；qualified 名（DroneController::foo）
  拆出 cls，kind=method
- class_specifier / struct_specifier（带名）：kind=class
- field_declaration：仅头文件（.h/.hpp/.hh）中的方法声明 → kind=method（类接口面）
- 匿名命名空间整棵跳过；宏调用只进 scan_calls，不产生符号（防宏爆炸）
- public 一律 True（C++ 无下划线约定）；visibility 暂不判定（二期）
- ROS 领域扩展：advertise/subscribe/advertiseService/serviceClient/ros::init →
  话题与服务的"绑定条目"（TOP-/SVC-）与节点入口（NDE-），进 inventory.interfaces
"""
import re

from .base import LanguageAdapter, node_text, parse_tree, walk

_HEADER_EXTS = (".h", ".hpp", ".hh")

_ROS_TOPIC_PUB = re.compile(r"advertise\s*<\s*([^>(]+?)\s*>\s*\(\s*\"([^\"]+)\"")
_ROS_TOPIC_SUB = re.compile(r"subscribe\s*(?:<\s*([^>(]+?)\s*>\s*)?\(\s*\"([^\"]+)\"")
_ROS_SVC_SERVER = re.compile(r"advertiseService\s*\(\s*\"([^\"]+)\"")
_ROS_SVC_CLIENT = re.compile(r"serviceClient\s*<\s*([^>(]+?)\s*>\s*\(\s*\"([^\"]+)\"")
_ROS_NODE = re.compile(r"ros::init\s*\(([^;]*)\)")
_ROS_GUARD = re.compile(r"ros/ros\.h|ros::|serviceClient|advertiseService")


def _decl_name(data, fn_node):
    """沿 declarator 链下钻取名字。返回 (name, cls)；qualified 名拆 `::` 前缀。"""
    cur = fn_node.child_by_field_name("declarator")
    seen = None
    while cur is not None:
        if cur.type in ("identifier", "field_identifier", "qualified_identifier",
                        "destructor_name", "operator_name"):
            seen = cur
        nxt = cur.child_by_field_name("declarator")
        if nxt is None:
            break
        cur = nxt
    if seen is None:
        return None, None
    t = node_text(data, seen)
    if "::" in t:
        cls, name = t.rsplit("::", 1)
        return name, cls or None
    return t, None


def _sig_text(data, node):
    t = node_text(data, node).split("{", 1)[0]
    t = " ".join(t.split())
    return t.strip()[:200]


class CppTreeSitterAdapter(LanguageAdapter):
    lang = "cpp"
    exts = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h")
    grammar = "cpp"

    def _scan_ros(self, data, text, rel_path, module_id, counters):
        """ROS 领域形态 → 话题/服务绑定条目（TOP-/SVC-）与节点入口（NDE-）。
        仅当文件含 ROS 痕迹时启用；话题名为变量（如来自 yaml 配置）的场景不抓取（如实缺失）。"""
        if not _ROS_GUARD.search(text):
            return []

        def line_of(m):
            return text[:m.start()].count("\n") + 1

        def in_comment(m):
            """匹配点位于注释内（行内 // 或块注释行首）→ 跳过（防抓取被注释掉的代码）。"""
            ls = text.rfind("\n", 0, m.start()) + 1
            line = text[ls:m.start()]
            return "//" in line or line.lstrip().startswith("*")

        def add(kind, name, m, msg=None, srv=None, role=None):
            item = {
                "id": self._iface_id(counters, kind), "kind": kind, "module": module_id,
                "name": name, "file": rel_path, "line": line_of(m),
                "extractor": "tree-sitter", "confidence": "reliable",
            }
            if msg:
                item["msg"] = msg.strip()
            if srv:
                item["srv"] = srv.strip()
            if role:
                item["role"] = role
            out.append(item)

        out = []
        for m in _ROS_TOPIC_PUB.finditer(text):
            if not in_comment(m):
                add("topic", m.group(2), m, msg=m.group(1), role="pub")
        for m in _ROS_TOPIC_SUB.finditer(text):
            if not in_comment(m):
                add("topic", m.group(2), m, msg=m.group(1), role="sub")
        for m in _ROS_SVC_SERVER.finditer(text):
            if not in_comment(m):
                add("service", m.group(1), m, role="server")
        for m in _ROS_SVC_CLIENT.finditer(text):
            if not in_comment(m):
                add("service", m.group(2), m, srv=m.group(1), role="client")
        for m in _ROS_NODE.finditer(text):
            quotes = re.findall(r'"([^"]+)"', m.group(1))
            if quotes and not in_comment(m):
                add("node", quotes[-1], m)     # ros::init 第三个字符串参数 = 节点名
        out.sort(key=lambda i: (i["file"], i["line"]))
        return out

    def _scan(self, data, rel_path, module_id, counters):
        tree, root = parse_tree(self.grammar, data)
        is_header = rel_path.lower().endswith(_HEADER_EXTS)
        symbols, notes = [], []
        cls_stack = []

        def on_class(node):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return None
            name = node_text(data, name_node)
            symbols.append(self._symbol(
                self._fun_id(counters), "class", module_id, name, rel_path,
                node.start_point[0] + 1, _sig_text(data, node)))
            return name

        def on_function(node):
            name, cls = _decl_name(data, node)
            if not name:
                return
            kind = "method" if (cls or cls_stack) else "function"
            symbols.append(self._symbol(
                self._fun_id(counters), kind, module_id,
                "%s::%s" % (cls, name) if cls else name, rel_path,
                node.start_point[0] + 1, _sig_text(data, node),
                cls=cls or (cls_stack[-1] if cls_stack else None)))

        def on_field_decl(node):
            # 类内声明（仅头文件计数，避免与 .cpp 定义重复入表）
            if not is_header:
                return
            decl = node.child_by_field_name("declarator")
            if decl is None:
                return
            fn = decl if decl.type == "function_declarator" else None
            if fn is None:
                for ch in decl.children:
                    if ch.type == "function_declarator":
                        fn = ch
                        break
            if fn is None:
                return
            name, cls = _decl_name(data, fn)
            if not name:
                return
            cls = cls or (cls_stack[-1] if cls_stack else None)
            symbols.append(self._symbol(
                self._fun_id(counters), "method", module_id,
                "%s::%s" % (cls, name) if cls else name, rel_path,
                node.start_point[0] + 1, _sig_text(data, node),
                cls=cls, visibility="public"))

        def visit(node):
            if node.type == "namespace_definition" and node.child_by_field_name("name") is None:
                return                     # 匿名命名空间：内部链接，整棵跳过
            if node.type == "class_specifier" or node.type == "struct_specifier":
                name = on_class(node)
                body = node.child_by_field_name("body")
                if body is not None:
                    if name:
                        cls_stack.append(name)
                    for ch in node.children:
                        visit(ch)
                    if name:
                        cls_stack.pop()
                    return
            elif node.type == "function_definition":
                on_function(node)
            elif node.type == "field_declaration":
                on_field_decl(node)
            elif node.type == "call_expression":
                pass                       # 调用关系走 scan_calls（独立遍历）
            for ch in node.children:
                visit(ch)

        visit(root)
        interfaces = self._scan_ros(data, node_text(data, root), rel_path, module_id, counters)
        return symbols, [], interfaces, notes

    def scan_calls(self, data):
        tree, root = parse_tree(self.grammar, data)
        out = []
        for n in walk(root):
            if n.type == "call_expression":
                fn = n.child_by_field_name("function")
                if fn is not None:
                    out.append({"line": n.start_point[0] + 1,
                                "callee": node_text(data, fn)[:80]})
        return out

    def scan_deps(self, data):
        tree, root = parse_tree(self.grammar, data)
        out = []
        for n in walk(root):
            if n.type == "preproc_include":
                t = node_text(data, n).replace("#include", "").strip()
                out.append(t.strip("\"'")[:120])
        return out
