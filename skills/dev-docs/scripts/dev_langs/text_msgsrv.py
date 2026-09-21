# -*- coding: utf-8 -*-
"""dev_langs.text_msgsrv — ROS .msg/.srv 适配器（文本解析，唯一非 tree-sitter 路径）。

tree-sitter 官方无 ROS msg/srv grammar，故用行解析；接口统一、capability=reliable、
extractor="text"。产出进 inventory.interfaces（MSG-xxx / SRV-xxx），由 dev_docs 渲染成
带锚点的"ROS 接口定义"卡片并纳入对账。

msg 行法：`类型 字段名` / `类型[N] 字段名` / `常量类型 常量名=值`；`#` 注释；空行忽略。
srv：首个 `---` 分请求/响应两段。
"""
from .base import LanguageAdapter


def _parse_field(line):
    """一行 -> {type, name, array, constant}；非字段行返回 None。"""
    s = line.split("#", 1)[0].strip()
    if not s:
        return None
    parts = s.split(None, 1)
    if len(parts) != 2:
        return None
    ftype, rest = parts[0], parts[1].strip()
    array = ""
    if "[" in ftype:
        idx = ftype.index("[")
        array, ftype = ftype[idx:], ftype[:idx]
    constant = None
    if "=" in rest:
        name, constant = rest.split("=", 1)
        name = name.strip()
        constant = constant.strip()
    else:
        name = rest
    if not name or not ftype:
        return None
    return {"type": ftype, "name": name, "array": array, "constant": constant}


def _parse_block(lines):
    out = []
    for i, line in enumerate(lines):
        f = _parse_field(line)
        if f:
            f["line"] = i + 1
            out.append(f)
    return out


class MsgSrvTextAdapter(LanguageAdapter):
    lang = "msgsrv"
    exts = (".msg", ".srv")
    capability = "reliable"

    def _scan(self, data, rel_path, module_id, counters):
        text = data.decode("utf-8", "replace")
        name = rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        # 定义起始行 = 首个非空且非注释行（msg/srv 文件常以注释块开头）
        line1 = next((k + 1 for k, ln in enumerate(text.splitlines())
                      if ln.strip() and not ln.strip().startswith("#")), 1)
        if rel_path.lower().endswith(".srv"):
            seg = text.split("---", 1)
            request = _parse_block(seg[0].splitlines())
            response = _parse_block(seg[1].splitlines()) if len(seg) > 1 else []
            item = {
                "id": self._iface_id(counters, "srv"), "kind": "srv", "module": module_id,
                "name": name, "file": rel_path, "line": line1,
                "request": request, "response": response,
                "extractor": "text", "confidence": "reliable",
            }
        else:
            item = {
                "id": self._iface_id(counters, "msg"), "kind": "msg", "module": module_id,
                "name": name, "file": rel_path, "line": line1,
                "fields": _parse_block(text.splitlines()),
                "extractor": "text", "confidence": "reliable",
            }
        return [], [], [item], []
