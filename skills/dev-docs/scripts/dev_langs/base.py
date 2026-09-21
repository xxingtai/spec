# -*- coding: utf-8 -*-
"""dev_langs.base — 语言适配器基类与公共设施。

适配器模式：每种语言一个 LanguageAdapter 子类，只实现语言相关的扫描钩子；
base.scan() 是 dev_inventory 唯一调用的入口，返回统一 schema 的四元组
(symbols, endpoints, interfaces, notes)。

约定：
- 输入 data 为文件原始字节（tree-sitter 直接解析字节；文本按需 utf-8 replace 解码）。
- 输出确定性：符号顺序 = 节点 start_byte 顺序；ID 由全局 counters 分配。
- tree-sitter 是运行时硬依赖：get_parser 失败直接抛错（由上层给出安装指引）。
- 单文件异常在 base.scan 兜底为 note，不拖垮整个盘点。
"""
import os
import re

# 统一符号 schema 的 TODO 占位（与 dev_docs.SYM_TODO 同文案；此处独立定义防循环导入）
SYM_TODO = "<!-- TODO AI 依源码填写（用途 / 参数 / 返回 / 错误；缺失写 unknown） -->"

# 单符号签名提取的最大行跨度（防超长函数签名拖慢）
_SIG_MAX_SPAN = 60

_PARSER_CACHE = {}

# 多语法包特例：lang -> (模块名, 语言函数名)
_TS_SPECIAL = {
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
}


def grammar_module(lang):
    """语言 -> (语法包模块名, 语言函数名)。"""
    if lang in _TS_SPECIAL:
        return _TS_SPECIAL[lang]
    return "tree_sitter_" + lang, "language"


def grammar_version(lang):
    """语法包版本号（探测/报告用），缺失返回 None。"""
    try:
        import importlib.metadata as md
        return md.version(grammar_module(lang)[0].replace("_", "-"))
    except Exception:
        return None


def get_parser(lang):
    """按语言取 tree-sitter Parser（进程内单例）。语法包缺失时抛出带安装指引的异常。"""
    if lang in _PARSER_CACHE:
        return _PARSER_CACHE[lang]
    try:
        from tree_sitter import Language, Parser
        import importlib
        module_name, func_name = grammar_module(lang)
        mod = importlib.import_module(module_name)
        parser = Parser(Language(getattr(mod, func_name)()))
    except ImportError as e:
        raise SystemExit(
            "缺少运行时依赖 tree-sitter（%s）：请执行 pip install -r skills/dev-docs/requirements.txt" % e
        )
    _PARSER_CACHE[lang] = parser
    return parser


def parse_tree(lang, data):
    """解析并返回 (tree, root)。

    ⚠️ 调用方**必须保留 tree**（用局部变量即可）：py-tree-sitter 的 Node **不持有** Tree，
    若写成 `get_parser(x).parse(data).root_node`，底层 Tree 会被立即回收，节点变成悬空指针——
    实测在多文件连续解析时直接段错误（v1.5.2 修复的既存缺陷）。"""
    tree = get_parser(lang).parse(data)
    return tree, tree.root_node


def node_text(data, node):
    """节点原文（utf-8 replace 解码）。"""
    if node is None:
        return ""
    return data[node.start_byte:node.end_byte].decode("utf-8", "replace")


# ---------------- 语法层行性质判定（v1.5.2）----------------
# 目的：确认某 file:line 引用确实指向**可执行代码**，而不是注释或字符串
# （含被 '''…'''/"""…"""/ /* … */ 包住的"注释掉的代码"——它看着就像代码）。
# 文本启发式（只看行首是不是 #）无法覆盖这类情况，故此处用 tree-sitter 判定。
_NONCODE_NODE_KINDS = {
    # 注释：C/C++/Java/JS 的 // 与 /* */、Python/Shell 的 #
    "comment": "comment", "line_comment": "comment", "block_comment": "comment",
    # 字符串：多行字符串/模板串的中间行走这里
    "string": "string", "string_content": "string", "string_start": "string",
    "string_end": "string", "string_literal": "string", "raw_string_literal": "string",
    "interpreted_string_literal": "string", "string_fragment": "string",
    "template_string": "string", "heredoc_body": "string",
}

# 多语法包（同一适配器对应多个语法）：按扩展名细分
_EXT_GRAMMAR = {".ts": "typescript", ".tsx": "tsx"}

_LINE_KIND_CACHE = {}   # abs_path -> {行号: 'comment'|'string'}（纯数据，不驻留语法树对象）


def grammar_for_path(path):
    """路径 -> 语法名（None = 该类型无 tree-sitter 语法，如 .msg/.srv）。"""
    from .registry import get_adapter
    ext = os.path.splitext(path or "")[1].lower()
    if ext in _EXT_GRAMMAR:
        return _EXT_GRAMMAR[ext]
    adapter = get_adapter(ext)
    return getattr(adapter, "grammar", None) if adapter else None


def _docstring_ranges(root):
    """Python 文档字符串（模块/类/函数体首个语句为字符串）的字节区间列表。
    文档字符串是**正当引用锚点**（"见 XXX 模块说明"），其整段（含 string_content 等子节点）
    都不作为"字符串内的行"报警。"""
    out = []
    for node in walk(root):
        if node.type not in ("module", "block", "class_body"):
            continue
        for first in node.named_children[:1]:
            if first.type != "expression_statement":
                continue
            inner = first.named_children
            if inner and inner[0].type == "string":
                out.append((inner[0].start_byte, inner[0].end_byte))
    return out


def _line_kinds_for_file(abs_path, grammar):
    """整文件"非代码行"表 `{行号: 'comment'|'string'}`（进程内缓存）。

    实现要点（都实测踩过坑）：
    - 解析一次后**遍历节点收集非代码节点的行范围**，随即释放语法树——不驻留任何
      tree-sitter 对象（Node 不持有 Tree，驻留会 use-after-free）；
    - 不用 `descendant_for_point_range` 点查询：实测与"多文件连续解析"组合会破坏堆
      （表现为后续完全无关的解析段错误）；
    - 多行节点（三引号块 / `/* … */`）整段算非代码行（起始行若节点前已有代码则不算）；
    - 单行节点必须"整行就是该节点"才算（如整行的 `#` 注释、单行字符串字面量），从而把
      `x = "abc"`、`int x; /* note */`、字典字面量行这类**含注释/字符串的代码行**排除；
    - 豁免**文档字符串**与 **doxygen 文档注释**（形如 `/** … */`、`///`）——它们是正当锚点。"""
    if abs_path in _LINE_KIND_CACHE:
        return _LINE_KIND_CACHE[abs_path]
    kinds = {}
    try:
        with open(abs_path, "rb") as fh:
            data = fh.read()
        tree, root = parse_tree(grammar, data)
        src_lines = data.split(b"\n")
        doc_ranges = _docstring_ranges(root)

        def in_docstring(n):
            return any(s <= n.start_byte and n.end_byte <= e for s, e in doc_ranges)

        for node in walk(root):
            kind = _NONCODE_NODE_KINDS.get(node.type)
            if not kind or in_docstring(node):
                continue
            raw = data[node.start_byte:node.end_byte]
            if raw.startswith((b"/**", b"///")):
                continue                      # doxygen 文档注释：正当锚点
            start_row, start_col = node.start_point[0], node.start_point[1]
            end_row = node.end_point[0]
            if end_row > start_row:            # 多行：整段算非代码
                for row in range(start_row, end_row + 1):
                    if row == start_row and row < len(src_lines) \
                            and src_lines[row][:start_col].strip():
                        continue               # 起始行节点之前已有代码 → 该行按代码行
                    kinds.setdefault(row + 1, kind)
            elif start_row < len(src_lines) and src_lines[start_row].strip() == raw.strip():
                kinds.setdefault(start_row + 1, kind)   # 单行：整行即该节点
    except (OSError, SystemExit):
        kinds = {}
    _LINE_KIND_CACHE[abs_path] = kinds
    return kinds


def line_kind_ts(abs_path, lineno, line_text=""):
    """语法层行性质：'comment' / 'string' / None（无法判定或该类型无语法）。

    `line_text` 仅为兼容既有调用签名保留，判定不依赖它。"""
    grammar = grammar_for_path(abs_path)
    if not grammar:
        return None
    return _line_kinds_for_file(abs_path, grammar).get(lineno)


# ---------------- 声明行判定（v1.5.2，供填卡证据提示用）----------------
# 区分"声明/定义行"与"调用点行"：只有前者行尾注释才是该符号的语义依据。
# 典型陷阱：调用点 `publishCoreCmd(A_MSG_CMD_RECG_STOP);//停止识别` 的行尾注释
# 是**调用处**说明，若误配给定义处会写出错误功能描述（实测踩过）。
_DECL_NODE_TYPES = {
    "function_declarator", "function_definition", "declaration", "field_declaration",
    "method_declaration", "constructor_declaration", "decorated_definition",
    "class_declaration", "class_definition", "class_specifier", "struct_specifier",
    "function_item", "method_definition", "property_declaration",
}

_DECL_LINE_CACHE = {}   # abs_path -> {行号: frozenset(节点类型)}（仅声明类节点）


def decl_lines(abs_path):
    """该文件里"落在声明类节点起始行"的行号 -> 节点类型集合（进程内缓存）。

    只记录行号与类型名（纯数据），不驻留语法树对象。无语法时返回空表。"""
    if abs_path in _DECL_LINE_CACHE:
        return _DECL_LINE_CACHE[abs_path]
    out = {}
    grammar = grammar_for_path(abs_path)
    if grammar:
        try:
            with open(abs_path, "rb") as fh:
                data = fh.read()
            tree, root = parse_tree(grammar, data)
            for node in walk(root):
                if node.type in _DECL_NODE_TYPES:
                    out.setdefault(node.start_point[0] + 1, set()).add(node.type)
        except (OSError, SystemExit):
            out = {}
    out = {k: frozenset(v) for k, v in out.items()}
    _DECL_LINE_CACHE[abs_path] = out
    return out


def is_decl_line(abs_path, lineno):
    """该行是否为声明/定义行（而非调用点行）。无语法信息时保守返回 True
    （宁可提示，也不因缺语法让提示功能失效）。"""
    table = decl_lines(abs_path)
    if not table:
        return True
    return lineno in table


_DOCSTRING_CACHE = {}   # abs_path -> {定义行: 文档字符串首 2 行}


def docstrings_by_def_line(abs_path):
    """`{函数/类定义行: 文档字符串（首 2 行）}`（纯数据缓存，不驻留语法树）。"""
    if abs_path in _DOCSTRING_CACHE:
        return _DOCSTRING_CACHE[abs_path]
    out = {}
    grammar = grammar_for_path(abs_path)
    if grammar:
        try:
            with open(abs_path, "rb") as fh:
                data = fh.read()
            tree, root = parse_tree(grammar, data)
            for node in walk(root):
                body = node.child_by_field_name("body")
                if body is None:
                    continue
                firsts = body.named_children[:1]
                if not firsts or firsts[0].type != "expression_statement":
                    continue
                inner = firsts[0].named_children
                if not inner or inner[0].type != "string":
                    continue
                raw = data[inner[0].start_byte:inner[0].end_byte].decode("utf-8", "replace")
                text = raw.strip().strip("\"'").strip()
                if text:
                    out[node.start_point[0] + 1] = " ".join(text.split()[:24])[:160]
        except (OSError, SystemExit):
            out = {}
    _DOCSTRING_CACHE[abs_path] = out
    return out


# ---------------- 全库引用计数（v1.5.3，治"由命名推断用途"）----------------
# 目的：宏/函数/类被引用了几次是**机器可知的事实**（曾把全库零引用的宏写成"换算约定"，
# 见方案-提取质量机制化.md P0-4）。计数口径：tree-sitter **语义标识符节点**——注释与
# 字符串天然不含此类节点，不会被误计；声明处自身的出现由调用方按声明位点数扣减，
# 得到"声明之外的引用数"（refs）。各语法包的标识符节点名集中在一张表，未列出的
# 语法退化为 ("identifier",)（保守：宁少计不多计——refs 偏小只会多提示，不会漏提示）。
_IDENT_NODE_KINDS = {
    "c": ("identifier", "field_identifier", "type_identifier", "namespace_identifier",
          "operator_name"),
    "cpp": ("identifier", "field_identifier", "type_identifier", "namespace_identifier",
            "operator_name"),
    "java": ("identifier", "type_identifier"),
    "javascript": ("identifier", "property_identifier", "shorthand_property_identifier"),
    "typescript": ("identifier", "property_identifier", "shorthand_property_identifier",
                   "type_identifier"),
    "tsx": ("identifier", "property_identifier", "shorthand_property_identifier",
            "type_identifier"),
    "bash": ("command_name", "variable_name"),
}

_IDENT_COUNT_CACHE = {}   # abs_path -> {name: count}（纯数据，不驻留语法树对象）
_MACRO_CACHE = {}         # abs_path -> [(name, line)]（仅 C/C++ #define）


def identifier_counts(abs_path):
    """该文件内标识符 -> 出现次数（进程内缓存；无 tree-sitter 语法的类型返回空表）。"""
    if abs_path in _IDENT_COUNT_CACHE:
        return _IDENT_COUNT_CACHE[abs_path]
    out = {}
    grammar = grammar_for_path(abs_path)
    if grammar:
        kinds = _IDENT_NODE_KINDS.get(grammar, ("identifier",))
        try:
            with open(abs_path, "rb") as fh:
                data = fh.read()
            tree, root = parse_tree(grammar, data)
            for node in walk(root):
                if node.type in kinds:
                    name = data[node.start_byte:node.end_byte].decode("utf-8", "replace")
                    out[name] = out.get(name, 0) + 1
        except (OSError, SystemExit):
            out = {}
    _IDENT_COUNT_CACHE[abs_path] = out
    return out


def macro_defs(abs_path):
    """C/C++ `#define` 名列表 `[(name, line)]`（进程内缓存；其他语言返回 []）。

    宏不是适配器扫描的符号（防宏爆炸），但零引用判定必须覆盖它——
    P0-4 的三个"换算约定"宏全是 #define。"""
    if abs_path in _MACRO_CACHE:
        return _MACRO_CACHE[abs_path]
    out = []
    grammar = grammar_for_path(abs_path)
    if grammar in ("c", "cpp"):
        try:
            with open(abs_path, "rb") as fh:
                data = fh.read()
            tree, root = parse_tree(grammar, data)
            for node in walk(root):
                if node.type in ("preproc_def", "preproc_function_def"):
                    nm = node.child_by_field_name("name")
                    if nm is not None:
                        out.append((node_text(data, nm), node.start_point[0] + 1))
        except (OSError, SystemExit):
            out = []
    _MACRO_CACHE[abs_path] = out
    return out


def walk(node):
    """深度优先遍历（含自身），顺序稳定。"""
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        for child in reversed(cur.children):
            stack.append(child)


# 测试文件判定（跨语言统一口径：只入测试索引，不生成文档符号/卡片）
_TEST_DIR_RE = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)", re.I)
_TEST_STEM = re.compile(r"^(tests?|test[_.\-].*|.*[_.\-]test)$", re.I)


def is_test_file(rel_path):
    """是否为测试文件：位于 tests//test//__tests__//spec/ 目录，或文件名形如
    `test.cpp` / `test_foo.py` / `foo_test.cpp` / `tests.py`（跨语言统一）。"""
    rel = (rel_path or "").replace("\\", "/")
    base = rel.rsplit("/", 1)[-1]
    if _TEST_DIR_RE.search(rel):
        return True
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return bool(_TEST_STEM.match(stem))


def sig_from_lines(lines, row0):
    """从源码行构造签名文本（def 起始行至括号闭合；与原 ast 版算法逐行等价）。"""
    start = row0
    if start < 0 or start >= len(lines):
        return ""
    depth = 0
    buf = []
    i = start
    while i < len(lines):
        line = lines[i]
        buf.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0 and (")" in line or i > start):
            break
        if i - start > _SIG_MAX_SPAN:
            break
        i += 1
    sig = " ".join(x.strip() for x in buf)
    if "->" in sig:
        sig = sig.split("->")[0].rstrip()
    if sig.endswith(":"):
        sig = sig[:-1]
    sig = re.sub(r"\s+", " ", sig)
    return sig.strip()


class LanguageAdapter:
    """语言适配器基类。子类实现 _scan，返回 (symbols, endpoints, interfaces, notes)。"""

    lang = ""
    exts = ()
    capability = "reliable"

    def scan(self, data, rel_path, module_id, counters):
        """统一入口（含单文件异常兜底）。"""
        try:
            return self._scan(data, rel_path, module_id, counters)
        except Exception as e:  # noqa: BLE001 单文件异常不拖垮盘点
            return [], [], [], [{"note": "adapter_error %s: %s" % (self.lang, e)}]

    def _scan(self, data, rel_path, module_id, counters):
        raise NotImplementedError

    # ---- ID 分配（全局 counters 共享，保证跨语言唯一） ----

    @staticmethod
    def _fun_id(counters):
        counters["fun"] += 1
        return "FUN-%03d" % counters["fun"]

    @staticmethod
    def _api_id(counters):
        counters["api"] += 1
        return "API-%03d" % counters["api"]

    @staticmethod
    def _iface_id(counters, kind):
        # kind -> (计数器键, ID 前缀)：msg/srv=接口定义；topic/service=ROS 话题/服务绑定；
        # node=ROS 节点入口
        key, prefix = {
            "msg": ("msg", "MSG"), "srv": ("srv", "SRV"),
            "topic": ("top", "TOP"), "service": ("svc", "SVC"),
            "node": ("nde", "NDE"),
        }.get(kind, ("msg", "MSG"))
        counters[key] = counters.get(key, 0) + 1      # 容错：调用方 counters 未预置该键
        return "%s-%%03d" % prefix % counters[key]

    # ---- 公共 schema 构造 ----

    @staticmethod
    def _symbol(sid, kind, module_id, qname, rel_path, line, signature,
                public=True, cls=None, visibility=None, extractor="tree-sitter"):
        s = {
            "id": sid, "kind": kind, "module": module_id, "qname": qname,
            "file": rel_path, "line": line, "signature": signature,
            "public": public, "extractor": extractor, "confidence": "reliable",
            "tested_by": [],
        }
        if cls:
            s["cls"] = cls
        if visibility:
            s["visibility"] = visibility
        return s

    @staticmethod
    def _first_line(data, node, limit=160):
        """节点首行文本（签名展示用）。"""
        t = node_text(data, node).splitlines()
        if not t:
            return ""
        return t[0].strip()[:limit]
