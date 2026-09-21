# -*- coding: utf-8 -*-
"""dev_langs.python_ts — Python 适配器（tree-sitter 实现）。

语义与原 ast 版 scan_python 逐项等价（迁移验收的硬前提）：
- 只收模块顶层函数 + class 体直接子级函数（嵌套函数/lambda 不收）
- `_` 前缀不收（public=False 不产出，与旧版一致：私有符号根本不入表）
- qname：顶层 = 函数名；方法 = ``类名.方法名``（点号风格保持）
- 签名 = def 起始行至括号闭合（与旧版 _sig_from_source 同算法）
- 装饰器路由端点（FastAPI/Flask 等）从 decorator 节点还原
- ROS 领域扩展（rospy）：init_node/Publisher/Subscriber/Service/ServiceProxy →
  节点入口与话题/服务绑定条目（NDE-/TOP-/SVC-），与 cpp 侧同一 schema
"""
import re

from .base import LanguageAdapter, node_text, parse_tree, sig_from_lines

_HTTP_VERB = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}

_ROSPY_NODE = re.compile(r"rospy\.init_node\s*\(\s*['\"]([^'\"]+)['\"]")
_ROSPY_PUB = re.compile(r"rospy\.Publisher\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([\w.]+)")
_ROSPY_SUB = re.compile(r"rospy\.Subscriber\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([\w.]+)")
_ROSPY_SVC = re.compile(r"rospy\.Service\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([\w.]+)")
_ROSPY_SVC_PROXY = re.compile(r"rospy\.ServiceProxy\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([\w.]+)")
_ROSPY_GUARD = re.compile(r"\brospy\b")


def _dotted_name(data, node):
    """还原标识符/属性链的点号名（app.post -> 'app.post'）。"""
    if node is None:
        return ""
    if node.type == "identifier":
        return node_text(data, node)
    if node.type == "attribute":
        base = _dotted_name(data, node.child_by_field_name("object"))
        attr = node_text(data, node.child_by_field_name("attribute"))
        return (base + "." + attr) if base else attr
    return ""


def _deco_info(data, deco):
    """decorator 节点 -> (点号名, 首个位置字符串参数, 关键字字符串参数 dict)。"""
    expr = deco.named_children[0] if deco.named_children else None
    if expr is None:
        return "", None, {}
    fn_node = expr
    path, kw = None, {}
    if expr.type in ("call_expression", "call"):   # ts-python 的调用节点叫 call
        fn_node = expr.child_by_field_name("function")
        args = expr.child_by_field_name("arguments")
        if args is not None:
            for child in args.named_children:
                if child.type == "string" and path is None:
                    path = node_text(data, child).strip("\"'")
                elif child.type == "keyword_argument":
                    kn = child.child_by_field_name("name")
                    kv = child.child_by_field_name("value")
                    if kn is not None and kv is not None and kv.type == "string":
                        kw[node_text(data, kn)] = node_text(data, kv).strip("\"'")
    return _dotted_name(data, fn_node), path, kw


def _collect_endpoints(deco_infos, handler):
    """装饰器 -> HTTP 端点（与旧版 _collect_endpoints 同口径）。"""
    eps = []
    for fn, path, kw in deco_infos:
        tail = fn.rsplit(".", 1)[-1] if fn else ""
        methods = None
        if tail in _HTTP_VERB:
            methods = [tail.upper()]
        elif tail == "route" and "methods" in kw:
            methods = kw["methods"].upper().split(",")
        if (methods or tail == "route") and path and path.startswith("/"):
            eps.append({"id": "API-XXX", "method": methods or ["*"], "path": path, "handler": handler})
        elif fn and (("api" in fn.lower()) or tail in _HTTP_VERB or tail == "route") and path:
            eps.append({"id": "API-XXX", "method": methods or ["*"], "path": path, "handler": handler})
    return eps


def _in_comment(text, pos):
    """匹配点是否位于注释内（行首至匹配点间出现 #）——避免抓取被注释掉的代码。"""
    ls = text.rfind("\n", 0, pos) + 1
    return "#" in text[ls:pos]


def _scan_ros(data, text, rel_path, module_id, counters, iface_id):
    """rospy ROS 形态 → 节点/话题/服务绑定条目（与 cpp 侧同一 schema）。"""
    if not _ROSPY_GUARD.search(text):
        return []

    def line_of(m):
        return text[:m.start()].count("\n") + 1

    out = []

    def add(kind, name, m, msg=None, srv=None, role=None):
        item = {"id": iface_id(counters, kind), "kind": kind, "module": module_id,
                "name": name, "file": rel_path, "line": line_of(m),
                "extractor": "tree-sitter", "confidence": "reliable"}
        if msg:
            item["msg"] = msg
        if srv:
            item["srv"] = srv
        if role:
            item["role"] = role
        out.append(item)

    for m in _ROSPY_NODE.finditer(text):
        if not _in_comment(text, m.start()):
            add("node", m.group(1), m)
    for m in _ROSPY_PUB.finditer(text):
        if not _in_comment(text, m.start()):
            add("topic", m.group(1), m, msg=m.group(2), role="pub")
    for m in _ROSPY_SUB.finditer(text):
        if not _in_comment(text, m.start()):
            add("topic", m.group(1), m, msg=m.group(2), role="sub")
    for m in _ROSPY_SVC.finditer(text):
        if not _in_comment(text, m.start()):
            add("service", m.group(1), m, srv=m.group(2), role="server")
    for m in _ROSPY_SVC_PROXY.finditer(text):
        if not _in_comment(text, m.start()):
            add("service", m.group(1), m, srv=m.group(2), role="client")
    out.sort(key=lambda i: (i["file"], i["line"]))
    return out


class PythonTreeSitterAdapter(LanguageAdapter):
    lang = "python"
    exts = (".py",)
    grammar = "python"

    def _scan(self, data, rel_path, module_id, counters):
        text = data.decode("utf-8", "replace")
        lines = text.splitlines()
        tree, root = parse_tree(self.grammar, data)
        symbols, endpoints, notes = [], [], []

        def func_sym(fn, decorators, cls_name=None):
            name_node = fn.child_by_field_name("name")
            if name_node is None:
                return None, []
            name = node_text(data, name_node)
            if name.startswith("_"):
                return None, []          # 私有不入表（与旧版一致）
            qname = "%s.%s" % (cls_name, name) if cls_name else name
            sig = sig_from_lines(lines, fn.start_point[0])
            deco_infos = [_deco_info(data, d) for d in decorators]
            s = self._symbol(self._fun_id(counters),
                             "method" if cls_name else "function",
                             module_id, qname, rel_path, fn.start_point[0] + 1, sig,
                             cls=cls_name)
            s["decorators"] = [i[0] for i in deco_infos]
            eps = _collect_endpoints(deco_infos, qname)
            for ep in eps:
                ep["id"] = self._api_id(counters)
                ep["module"] = module_id
                ep["file"] = rel_path
                ep["line"] = fn.start_point[0] + 1
                ep["kind"] = "endpoint"
                ep["handler"] = qname
                ep["confidence"] = "reliable"
                endpoints.append(ep)
            if eps:
                s["endpoints"] = [e["id"] for e in eps]
            return s, eps

        def handle_fn(node, decorators, cls_name=None):
            s, _ = func_sym(node, decorators, cls_name)
            if s:
                symbols.append(s)

        def handle_class(node, decorators):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return
            cls_name = node_text(data, name_node)
            if cls_name.startswith("_"):
                return
            body = node.child_by_field_name("body")
            if body is None:
                return
            for item in body.named_children:
                if item.type == "function_definition":
                    handle_fn(item, [], cls_name)
                elif item.type == "decorated_definition":
                    inner = item.child_by_field_name("definition")
                    decs = [c for c in item.named_children if c.type == "decorator"]
                    if inner is not None and inner.type == "function_definition":
                        handle_fn(inner, decs, cls_name)

        for node in root.named_children:
            if node.type == "function_definition":
                handle_fn(node, [])
            elif node.type == "class_definition":
                handle_class(node, [])
            elif node.type == "decorated_definition":
                inner = node.child_by_field_name("definition")
                decs = [c for c in node.named_children if c.type == "decorator"]
                if inner is None:
                    continue
                if inner.type == "function_definition":
                    handle_fn(inner, decs)
                elif inner.type == "class_definition":
                    handle_class(inner, decs)
        interfaces = _scan_ros(data, text, rel_path, module_id, counters, self._iface_id)
        return symbols, endpoints, interfaces, notes
