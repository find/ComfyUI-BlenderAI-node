from __future__ import annotations
import bpy
import random
import os
import re
import json
import math
import textwrap
import pickle
from copy import deepcopy
from hashlib import md5
from math import ceil
from typing import Any
from pathlib import Path
from random import random as rand
from functools import partial, lru_cache
from mathutils import Vector, Matrix
from bpy.types import Context, Event
from .utils import gen_mask, SELECTED_COLLECTIONS
from ..utils import logger, update_screen, Icon, _T
from ..datas import ENUM_ITEMS_CACHE
from ..preference import get_pref
from ..timer import Timer
from ..translation import ctxt
from .manager import get_url, Task, WITH_PROXY

NODES_POLL = {}
Icon.reg_none(Path(__file__).parent / "NONE.png")
PREVICONPATH = {}
PATH_CFG = Path(__file__).parent / "PATH_CFG.json"

SOCKET_TYPE = {}  # NodeType: {PropName: SocketType}
SOCKET_HASH_MAP = {  # {HASH: METATYPE}
    "INT": "INT",
    "FLOAT": "FLOAT",
    "STRING": "STRING",
    "BOOLEAN": "BOOLEAN"
}
INTERNAL_NAMES = {
    "bl_description",
    "bl_height_default",
    "bl_height_max",
    "bl_height_min",
    "bl_icon",
    "bl_idname",
    "bl_label",
    "bl_rna",
    "bl_static_type",
    "bl_width_default",
    "bl_width_max",
    "bl_width_min",
    "calc_slot_index",
    "class_type",
    "color",
    "dimensions",
    "draw_buttons",
    "draw_buttons_ext",
    "dump",
    "get_from_link",
    "get_meta",
    "height",
    "hide",
    "id",
    "inp_types",
    "input_template",
    "inputs",
    "internal_links",
    "is_base_type",
    "is_registered_node_type",
    "label",
    "load",
    "location",
    "mute",
    "name",
    "out_types",
    "output_template",
    "outputs",
    "parent",
    "poll",
    "poll_instance",
    "pool",
    "query_stat",
    "rna_type",
    "select",
    "serialize",
    "set_stat",
    "show_options",
    "show_preview",
    "show_texture",
    "socket_value_update",
    "switch_socket",
    "type",
    "unique_id",
    "update",
    "use_custom_color",
    "width",
    "width_hidden"
}

PROP_NAME_HEAD = "sdn_"
name2path = {
    "CheckpointLoader": {"ckpt_name": "checkpoints"},
    "CheckpointLoaderSimple": {"ckpt_name": "checkpoints"},
    "VAELoader": {"vae_name": "vae"},
    "LoraLoader": {"lora_name": "loras"},
    "CLIPLoader": {"clip_name": "clip"},
    "ControlNetLoader": {"control_net_name": "controlnet"},
    "DiffControlNetLoader": {"control_net_name": "controlnet"},
    "StyleModelLoader": {"style_model_name": "style_models"},
    "CLIPVisionLoader": {"clip_name": "clip_vision"},
    "unCLIPCheckpointLoader": {"ckpt_name": "checkpoints"},
    "UpscaleModelLoader": {"model_name": "upscale_models"},
    "GLIGENLoader": {"gligen_name": "gligen"}
}


def ui_scale():
    return bpy.context.preferences.system.ui_scale


def get_node_center(node: bpy.types.Node) -> Vector:
    node_size = node.dimensions / ui_scale()
    node_center = node.location + node_size / 2 * Vector((1, -1))
    return node_center


def get_nearest_nodes(nodes: list[bpy.types.Node], mouse_pos: Vector):
    find_nodes = []
    for nd in nodes:
        if nd.type in {"FRAME", "REROUTE"}:
            continue
        v = mouse_pos - get_node_center(nd)
        find_nodes.append((nd, v.length))
    find_nodes.sort(key=lambda a: a[1])
    return find_nodes


def loc_to_region2d(vec):
    vec = vec * ui_scale()
    return Vector(bpy.context.region.view2d.view_to_region(vec.x, vec.y, clip=False))


def try_get_path_cfg():
    if not PATH_CFG.exists():
        return
    try:
        d = json.loads(PATH_CFG.read_text())
        return d
    except Exception as e:
        logger.warn(_T("icon path load error") + str(e))

    try:
        d = json.loads(PATH_CFG.read_text(encoding="gbk"))
        return d
    except Exception as e:
        logger.warn(_T("icon path load error") + str(e))


def get_icon_path(nname):

    {'controlnet': [['D:\\BaiduNetdiskDownload\\AI\\ComfyUI\\models\\controlnet',
                     'D:\\BaiduNetdiskDownload\\AI\\ComfyUI\\models\\t2i_adapter'], ['.bin', '.safetensors', '.pth', '.pt', '.ckpt']],
     'upscale_models': [['D:\\BaiduNetdiskDownload\\AI\\ComfyUI\\models\\upscale_models'], ['.bin', '.safetensors', '.pth', '.pt', '.ckpt']]}

    if not PREVICONPATH:
        if not (d := try_get_path_cfg()):
            return {}
        # prev_dir = Path(get_pref().model_path) / "models"
        # {
        #     "ckpt_name": prev_dir / "checkpoints",
        # }
        for class_type in name2path:
            path_list = {}
            PREVICONPATH[class_type] = path_list
            for name in name2path[class_type]:
                reg_name = get_reg_name(name)
                path_list[reg_name] = d[name2path[class_type][name]][0]
    return PREVICONPATH.get(nname, {})


def get_reg_name(inp_name):
    if inp_name in INTERNAL_NAMES:
        return PROP_NAME_HEAD + inp_name
    return inp_name


def get_ori_name(inp_name):
    if inp_name.startswith(PROP_NAME_HEAD):
        return inp_name.replace(PROP_NAME_HEAD, "")
    return inp_name


def get_fixed_seed():
    return int(random.randrange(4294967294))


class NodeBase(bpy.types.Node):
    bl_width_min = 200.0
    bl_width_max = 2000.0
    sdn_order: bpy.props.IntProperty(default=-1)
    id: bpy.props.StringProperty(default="-1")
    builtin__stat__: bpy.props.StringProperty(subtype="BYTE_STRING")  # ori name: True/False
    pool = set()

    def query_stat(self, name):
        if not self.builtin__stat__:
            return None
        stat = pickle.loads(self.builtin__stat__)
        name = get_ori_name(name)
        return stat.get(name, None)

    def is_base_type(self, name):
        """
        判断是不是基本类型, 目前通过 拥有注册属性名判断
        """
        reg_name = get_reg_name(name)
        return hasattr(self, reg_name)

    def set_stat(self, name, value):
        if not self.builtin__stat__:
            self.builtin__stat__ = pickle.dumps({})
        stat = pickle.loads(self.builtin__stat__)
        stat[name] = value
        self.builtin__stat__ = pickle.dumps(stat)

    def switch_socket(self, name, value):
        self.set_stat(name, value)
        if value:
            socket_type = SOCKET_TYPE[self.class_type].get(name, "NONE")
            inp = self.inputs.new(socket_type, name)
            inp.slot_index = len(self.inputs) - 1
            return inp
        elif inp := self.inputs.get(name):
            self.inputs.remove(inp)

    @lru_cache
    def get_meta(self, inp_name) -> list:
        """
        判断`属性名`是否存在于`元数据`中(可能是socket也可能是widgets)
        """
        if not hasattr(self, "__metadata__"):
            logger.warn(f"node {self.name} has no metadata")
            return []
        inputs = self.__metadata__.get("input", {})
        if inp_name in (r := inputs.get("required", {})):
            return r[inp_name]
        if inp_name in (o := inputs.get("optional", {})):
            return o[inp_name]
        return []

    def calc_slot_index(self):
        for i, inp in enumerate(self.inputs):
            inp.slot_index = i
        for i, out in enumerate(self.outputs):
            out.slot_index = i

    def unique_id(self):
        for i in range(3, 99999):
            i = str(i)
            if i not in self.pool:
                self.pool.add(i)
                return i

    def free(self):
        self.pool.discard(self.id)

    def copy(self, node):
        self.apply_unique_id()
        if hasattr(self, "sync_rand"):
            self.sync_rand = False

    def apply_unique_id(self):
        self.id = self.unique_id()
        return self.id
        from .tree import CFNodeTree
        nodes = CFNodeTree.instance.get_nodes()
        for n in nodes:
            # 有相同, 重新生成
            if n.id == self.id and n != self:
                self.id = self.unique_id()
                break
        else:
            # 唯一，则判断是不是-1
            if self.id == "-1":
                self.pool.discard(self.id)
                self.id = self.unique_id()

        return self.id

    def draw_label(self):
        return self.name

    def update(self):
        if not self.is_registered_node_type():
            return
        self.remove_multi_link()
        self.remove_invalid_link()
        self.primitive_check()

    def remove_multi_link(self):
        for inp in self.inputs:
            if len(inp.links) <= 1:
                continue
            for l in inp.links[1:]:
                if not hasattr(bpy.context.space_data, "edit_tree"):
                    continue
                bpy.context.space_data.edit_tree.links.remove(l)

    def remove_invalid_link(self):
        for inp in self.inputs:
            if not inp.is_linked:
                continue
            lfrom = self.get_from_link(inp)
            for l in inp.links:
                fs = l.from_socket
                if lfrom:
                    fs = lfrom.from_socket
                ts = l.to_socket
                if fs.bl_idname == "*" and SOCKET_HASH_MAP.get(ts.bl_idname) in {"ENUM", "INT", "FLOAT", "STRING", "BOOLEAN"}:
                    continue
                if fs.bl_idname == ts.bl_idname:
                    continue
                if l.from_node.bl_idname == "NodeReroute":
                    continue
                if not hasattr(bpy.context.space_data, "edit_tree"):
                    continue

                bpy.context.space_data.edit_tree.links.remove(l)

    def primitive_check(self):
        if not bpy.context.space_data:
            return
        if bpy.context.space_data.type != "NODE_EDITOR":
            return
        tree = bpy.context.space_data.edit_tree
        if tree.bl_idname != "CFNodeTree":
            return
        # 对PrimitiveNode类型的输出进行限制
        if self.class_type != "PrimitiveNode":
            return
        if not self.outputs:
            return
        out = self.outputs[0]
        for l in out.links:
            if l.to_node.bl_idname != "NodeReroute":
                continue
            tree.links.remove(l)

        if not out.is_linked:
            return
        if not out.links:
            return
        self.prop = out.links[0].to_socket.name
        to_meta = [None]
        for link in out.links:
            if not link.to_node.is_registered_node_type():
                continue
            to_meta = link.to_node.get_meta(self.prop)
            break

        def meta_equal(meta1, meta2):
            if not meta1 or not meta2:
                return False
            if isinstance(meta1[0], list) or isinstance(meta2[0], list):
                return meta1[0] == meta2[0]
            if meta1[0] != meta2[0]:
                return False
            for k in meta1[1]:
                if k == "default":
                    continue
                if meta1[1][k] != meta2[1][k]:
                    return False
            return True
        for l in out.links:
            if l.to_node.is_registered_node_type() and meta_equal(to_meta, l.to_node.get_meta(l.to_socket.name)):
                continue
            tree.links.remove(l)

    def get_from_link(self, socket: bpy.types.NodeSocket):
        if not socket.is_linked:
            return
        if not socket.links:
            return
        link = socket.links[0]
        while True:
            node = link.from_node
            if node.bl_idname == "NodeReroute":
                if node.inputs[0].is_linked:
                    link = node.inputs[0].links[0]
                else:
                    return
            else:
                return link

    def serialize_pre(self):
        spec_serialize_pre(self)

    def serialize(self, execute=True):
        """
        gen prompt
        """
        inputs = {}
        for inp_name in self.inp_types:
            # inp = self.inp_types[inp_name]
            reg_name = get_reg_name(inp_name)
            if inp := self.inputs.get(reg_name):
                link = self.get_from_link(inp)
                if link:
                    from_node = link.from_node
                    if from_node.bl_idname == "PrimitiveNode":
                        # 添加 widget
                        inputs[inp_name] = getattr(self, reg_name)
                    else:
                        # 添加 socket
                        inputs[inp_name] = [link.from_node.id, link.from_node.outputs[:].index(link.from_socket)]
                elif self.get_meta(inp_name):
                    if hasattr(self, reg_name):
                        # 添加 widget
                        inputs[inp_name] = getattr(self, reg_name)
                    else:
                        # 添加 socket
                        inputs[inp_name] = [None]
            else:
                # 添加 widget
                inputs[inp_name] = getattr(self, reg_name)
        cfg = {
            "inputs": inputs,
            "class_type": self.class_type
        }
        spec_serialize(self, cfg, execute)
        return cfg

    def load(self, data, with_id=True):
        self.pool.discard(self.id)
        self.location[:] = [data["pos"][0], -data["pos"][1]]
        if isinstance(data["size"], list):
            self.width, self.height = [data["size"][0], -data["size"][1]]
        else:
            self.width, self.height = [data["size"]["0"], -data["size"]["1"]]
        title = data.get("title", "")
        if self.class_type in {"KSampler", "KSamplerAdvanced"}:
            logger.info(_T("Saved Title Name -> ") + title)  # do not replace name
        elif title:
            self.name = title
        if with_id:
            try:
                self.id = str(data["id"])
                self.pool.add(self.id)
            except BaseException:
                self.apply_unique_id()
        # 处理 inputs
        for inp in data.get("inputs", []):
            name = inp.get("name", "")
            if not self.is_base_type(name):
                continue
            new_socket = self.switch_socket(name, True)
            if si := inp.get("slot_index"):
                new_socket.slot_index = si
            md = self.get_meta(name)
            if isinstance(md, list):
                continue
            if not (default := inp.get("widget", {}).get("config", {}).get("default")):
                continue
            reg_name = get_reg_name(name)
            setattr(self, reg_name, default)
        # if self.class_type == "KSamplerAdvanced":
        #     data["widgets_values"].pop(2)
        if self.class_type == "KSampler":
            v = data["widgets_values"][1]
            if isinstance(v, bool):
                data["widgets_values"][1] = ["fixed", "increment", "decrement", "randomize"][int(v)]
        # if self.class_type == "DetailerForEach":
        #     data["widgets_values"].pop(3)
        if self.class_type == "MultiAreaConditioning":
            config = json.loads(self.config)
            for i in range(2):
                d = data["properties"]["values"][i]
                config[i]["x"] = d[0]
                config[i]["y"] = d[1]
                config[i]["sdn_width"] = d[2]
                config[i]["sdn_height"] = d[3]
                config[i]["strength"] = d[4]
            self["config"] = json.dumps(config)
            d = data["properties"]["values"][self.index]
            self["x"] = d[0]
            self["y"] = d[1]
            self["sdn_width"] = d[2]
            self["sdn_height"] = d[3]
            self["strength"] = d[4]
            self["resolutionX"] = data["properties"]["width"]
            self["resolutionY"] = data["properties"]["height"]

        for inp_name in self.inp_types:
            if not self.is_base_type(inp_name):
                continue
            reg_name = get_reg_name(inp_name)
            try:
                v = data["widgets_values"].pop(0)
                v = type(getattr(self, reg_name))(v)
                setattr(self, reg_name, v)
            except TypeError as e:
                if inp_name in {"seed", "noise_seed"}:
                    setattr(self, reg_name, str(v))
                elif (enum := re.findall(' enum "(.*?)" not found', str(e), re.S)):
                    logger.warn(f"{_T('|IGNORED|')} {self.class_type} -> {inp_name} -> {_T('Not Found Item')}: {enum[0]}")
                else:
                    logger.error(f"|{e}|")
            except IndexError:
                logger.info(f"{_T('|IGNORED|')} -> {_T('Load')}<{self.class_type}>{_T('Params not matching with current node')}")
            except Exception as e:
                logger.error(f"{_T('Params Loading Error')} {self.class_type} -> {self.class_type}.{inp_name}")
                logger.error(f" -> {e}")

    def dump(self, selected_only=False):
        tree = bpy.context.space_data.edit_tree
        all_links: bpy.types.NodeLinks = tree.links[:]

        inputs = []
        outputs = []
        widgets_values = []
        # 单独处理 widgets_values
        for inp_name in self.inp_types:
            if not self.is_base_type(inp_name):
                continue
            widgets_values.append(getattr(self, get_reg_name(inp_name)))
        for inp in self.inputs:
            inp_name = inp.name
            reg_name = get_reg_name(inp_name)
            md = self.get_meta(reg_name)
            inp_info = {"name": inp_name,
                        "type": inp.bl_idname,
                        "link": None}
            link = self.get_from_link(inp)
            is_base_type = self.is_base_type(inp_name)
            if link:
                if not selected_only:
                    inp_info["link"] = all_links.index(inp.links[0])
                elif inp.links[0].from_node.select:
                    inp_info["link"] = all_links.index(inp.links[0])
            if is_base_type:
                if not self.query_stat(inp.name) or not md:
                    continue
                inp_info["widget"] = {"name": reg_name,
                                      "config": md
                                      }
                inp_info["type"] = ",".join(md[0]) if isinstance(md[0], list) else md[0]
            inputs.append(inp_info)
        for i, out in enumerate(self.outputs):
            out_info = {"name": out.name,
                        "type": out.name,
                        }
            if not selected_only:
                out_info["links"] = [all_links.index(link) for link in out.links]
            elif out.links:
                out_info["links"] = [all_links.index(link) for link in out.links if link.to_node.select]
            out_info["slot_index"] = i
            outputs.append(out_info)
        properties = {}
        if self.class_type == "MultiAreaConditioning":
            config = json.loads(self["config"])
            properties = {'Node name for S&R': 'MultiAreaConditioning',
                          'width': self["resolutionX"],
                          'height': self["resolutionY"],
                          'values': [[64, 128, 384, 128, 10],
                                     [320, 64, 192, 128, 0.03]]}
            for i in range(2):
                properties["values"][i] = [
                    config[i]["x"],
                    config[i]["y"],
                    config[i]["sdn_width"],
                    config[i]["sdn_height"],
                    config[i]["strength"],
                ]
            widgets_values = [self["resolutionX"],
                              self["resolutionY"],
                              None,
                              self.index,
                              *properties["values"][self.index]
                              ]
        if self.class_type == "Reroute":
            inputs = [
                {"name": "",
                 "type": "*",
                 "link": None,
                 }
            ]
            if self.inputs[0].is_linked:
                if not selected_only:
                    inputs[0]["link"] = all_links.index(self.inputs[0].links[0])
                elif self.inputs[0].links[0].from_node.select:
                    inputs[0]["link"] = all_links.index(self.inputs[0].links[0])
            if not self.outputs[0].is_linked:
                outputs[0]["name"] = outputs[0]["type"] = "*"
            else:
                def find_out_node(node: bpy.types.Node):
                    output = node.outputs[0]
                    if not output.is_linked:
                        return None
                    to = output.links[0].to_node
                    to_socket = output.links[0].to_socket
                    if to.class_type == "Reroute":
                        return find_out_node(to)
                    return to_socket
                to_socket = find_out_node(self)
                if out and to_socket:
                    outputs[0]["name"] = to_socket.bl_idname
                    outputs[0]["type"] = to_socket.bl_idname
            properties = {
                "showOutputText": True,
                "horizontal": False
            }
        if self.class_type == "PrimitiveNode" and self.outputs[0].is_linked and self.outputs[0].links:
            node = self.outputs[0].links[0].to_node
            meta_data = deepcopy(node.get_meta(self.prop))
            output = outputs[0]
            output["name"] = "COMBO" if isinstance(meta_data[0], list) else meta_data[0]
            output["type"] = ",".join(meta_data[0]) if isinstance(meta_data[0], list) else meta_data[0]
            if output["name"] != "COMBO":
                meta_data[1]["default"] = getattr(node, self.prop)
            output["widget"] = {"name": self.prop,
                                "config": meta_data
                                }
            widgets_values = [getattr(node, self.prop), "fixed"]
        cfg = {
            "id": int(self.id),
            "type": self.class_type,
            "pos": [self.location.x, -self.location.y],
            "size": {"0": self.width, "1": self.height},
            "flags": {},
            "order": self.sdn_order,
            "mode": 0,
            "inputs": inputs,
            "outputs": outputs,
            "title": self.name,
            "properties": properties,
            "widgets_values": widgets_values
        }
        return cfg

    def save(self):
        save = {
            "id": 11,
            "type": "SaveImage",
            "pos": [
                1451,
                189
            ],
            "size": {
                "0": 210,
                "1": 58
            },
            "flags": {},
            "order": 2,
            "mode": 0,
            "inputs": [
                {
                    "name": "images",
                    "type": "IMAGE",
                    "link": 11  # id号 或 None
                }
            ],
            "properties": {},
            "widgets_values": [
                "ComfyUI"
            ]
        }
        return save

    def post_fn(self, task, result):
        ...

    def pre_fn(self):
        ...


class SocketBase(bpy.types.NodeSocket):
    allowLink = {"*", }

    def linkLimitCheck(self, context):
        self.allowLink.add(self.bl_label)
        # 连接限制
        for link in list(self.links):
            if link.from_node.bl_label in self.allowLink:
                continue
            logger.warn(f"{_T('Remove Link')}:{link.from_node.bl_label}",)
            context.space_data.edit_tree.links.remove(link)

    color: bpy.props.FloatVectorProperty(size=4, default=(1, 0, 0, 1))

    def draw_color(self, context, node):
        return self.color


class Ops_Swith_Socket(bpy.types.Operator):
    bl_idname = "sdn.switch_socket"
    bl_label = "切换Socket/属性"
    socket_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()
    action: bpy.props.StringProperty(default="")

    def execute(self, context):
        tree = bpy.context.space_data.edit_tree
        node: NodeBase = None
        socket_name = get_ori_name(self.socket_name)
        if not (node := tree.nodes.get(self.node_name)):
            return {"FINISHED"}
        match self.action:
            case "ToSocket":
                node.switch_socket(socket_name, True)
            case "ToProp":
                node.switch_socket(socket_name, False)
        self.action = ""
        return {"FINISHED"}

    def draw_prop(layout, node, prop, row=True, swlink=True) -> bpy.types.UILayout:
        l = layout
        if swlink:
            l = layout.row(align=True)
            op = l.operator(Ops_Swith_Socket.bl_idname, text="", icon="LINKED")
            op.node_name = node.name
            op.socket_name = prop
            op.action = "ToSocket"
        if row:
            l = l.row(align=True)
        else:
            l = l.column(align=True)
        return l


class Ops_Add_SaveImage(bpy.types.Operator):
    bl_idname = "sdn.add_saveimage"
    bl_label = "添加保存图片节点"
    node_name: bpy.props.StringProperty()

    def execute(self, context):
        tree = bpy.context.space_data.edit_tree
        node: NodeBase = None
        if not (node := tree.nodes.get(self.node_name)):
            return {"FINISHED"}
        inp = node.inputs[0]
        if not inp.is_linked:
            return {"FINISHED"}
        save_image_node = tree.nodes.new("存储")
        save_image_node.location = node.location
        save_image_node.location.y += 200
        tree.links.new(inp.links[0].from_socket, save_image_node.inputs[0])
        return {"FINISHED"}


class Ops_Active_Tex(bpy.types.Operator):
    bl_idname = "sdn.act_tex"
    bl_label = "选择纹理"
    img_name: bpy.props.StringProperty()
    node_name: bpy.props.StringProperty()

    def execute(self, context):
        if not (img := bpy.data.images.get(self.img_name)):
            return {"FINISHED"}
        tree = bpy.context.space_data.edit_tree
        if not (node := tree.nodes.get(self.node_name)):
            return {"FINISHED"}
        node.image = None if img == node.image else img
        return {"FINISHED"}


class Ops_Link_Mask(bpy.types.Operator):
    bl_idname = "sdn.link_mask"
    bl_label = "链接遮照"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.StringProperty(default="")
    cam_name: bpy.props.StringProperty(default="")
    node_name: bpy.props.StringProperty(default="")
    kmi: bpy.types.KeyMapItem = None
    kmis: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []
    properties = {}
    shotcut = {
        "idname": bl_idname,
        "type": "F",
        "value": "PRESS",
        "shift": False,
        "ctrl": False,
        "alt": False,
    }

    def get_nearest_node(self, context: bpy.types.Context, filter=lambda _: True) -> bpy.types.Node:
        mouse_pos = context.space_data.cursor_location
        for node in get_nearest_nodes(context.space_data.edit_tree.nodes, mouse_pos):
            if not filter(node[0]):
                continue
            return node[0]
        return None

    @classmethod
    def poll(cls, context):
        from .tree import TREE_TYPE
        return context.space_data.tree_type == TREE_TYPE

    def invoke(self, context: Context, event: Event):
        if self.action == "OnlyFocus":
            tree = context.space_data.edit_tree
            to_node = tree.nodes.get(self.node_name)
            if cam := bpy.data.objects.get(self.cam_name):
                self.validate_cam_cache(cam, to_node)
            else:
                cam = self.create_cam()
                to_node.cam = cam
                bpy.ops.sdn.mask(action="add", node_name=to_node.name, cam_name=cam.name)
            self.focus_cam(cam)
            self.active_cam_gp(cam)
            self.action = ""
            self.node_name = ""
            return {"FINISHED"}

        self.from_node: bpy.types.Node = None
        self.to_node: bpy.types.Node = None

        def prev_filter(n):
            return hasattr(n, "prev")
        self.from_node = self.get_nearest_node(context, filter=prev_filter)
        if not self.from_node:
            self.report({"ERROR"}, "No ImageNode Found!")
            return {"FINISHED"}
        bpy.context.window_manager.modal_handler_add(self)
        import gpu
        import gpu_extras
        shader_color = gpu.shader.from_builtin("2D_UNIFORM_COLOR")
        shader_line = gpu.shader.from_builtin("POLYLINE_SMOOTH_COLOR")
        shader_line.uniform_float("viewportSize", gpu.state.viewport_get()[2:4])
        shader_line.uniform_float("lineSmooth", True)

        def draw_fill_circle(pos, r=15, col=(1.0, 1.0, 1.0, .75), resolution=32):
            fac = 2.0 * math.pi / resolution  # pre calc
            vpos = ((pos[0], pos[1]), *((r * math.cos(i * fac) + pos[0], r * math.sin(i * fac) + pos[1]) for i in range(resolution + 1)))
            gpu.state.blend_set("ALPHA")
            shader_color.bind()
            shader_color.uniform_float("color", col)
            gpu_extras.batch.batch_for_shader(shader_color, "TRI_FAN", {"pos": vpos}).draw(shader_color)

        def draw_line(pos1, pos2, width=10, col1=(1.0, 1.0, 1.0, .75), col2=(1.0, 1.0, 1.0, .75)):
            gpu.state.blend_set("ALPHA")
            shader_line.bind()
            shader_line.uniform_float("lineWidth", width)
            content = {"pos": (pos1, pos2), "color": (col1, col2)}
            gpu_extras.batch.batch_for_shader(shader_line, "LINE_STRIP", content).draw(shader_line)

        def draw(self, context):
            if not self.from_node:
                return
            endpos = get_node_center(self.to_node) if self.to_node else context.space_data.cursor_location
            p1 = loc_to_region2d(get_node_center(self.from_node))
            p2 = loc_to_region2d(endpos)
            draw_line(p1, p2)
            draw_fill_circle(p1)
            if self.to_node:
                draw_fill_circle(p2)

        self.handle = bpy.types.SpaceNodeEditor.draw_handler_add(draw, (self, context), 'WINDOW', 'POST_PIXEL')
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: Event):
        context.area.tag_redraw()

        if not context.space_data.edit_tree:
            self.exit()
            return {"FINISHED"}
        if event.type == "MOUSEMOVE":
            def mask_filter(n: bpy.types.Node):
                return n.bl_idname == "Mask"
            n = self.get_nearest_node(context, filter=mask_filter)
            if n != self.from_node:
                self.to_node = n
        elif event.type == Ops_Link_Mask.kmi.type and event.value == "RELEASE":
            self.exec()
            self.exit()
            return {"FINISHED"}
        else:
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    def exec(self):
        if not self.from_node:
            self.report({"ERROR"}, "No ImageNode Found!")
            return
        if not self.to_node:
            self.report({"ERROR"}, "No MaskNode Found!")
            return
        print(f"{self.from_node.name} -> {self.to_node.name}")
        if not (img := self.from_node.prev):
            self.report({"ERROR"}, "No Image Found!")
            return
        self.to_node.mode = "Focus"
        if not (cam := self.to_node.cam):
            cam = self.create_cam()
            self.to_node.cam = cam
        camdata = cam.data
        if not camdata.background_images:
            bg = camdata.background_images.new()
        bg = camdata.background_images[0]
        bg.alpha = 1
        bg.image = img
        bg.show_background_image = True
        self.validate_cam_cache(cam, self.to_node)
        self.focus_cam(cam)
        self.active_cam_gp(cam)

    def exit(self):
        if not self.handle:
            return
        bpy.types.SpaceNodeEditor.draw_handler_remove(self.handle, "WINDOW")
        self.handle = None

    def create_cam(self) -> bpy.types.Object:
        camdata = bpy.data.cameras.new("SDN_Mask_Focus")
        cam = bpy.data.objects.new(name=camdata.name, object_data=camdata)
        cam.matrix_world = Matrix(((0.7071, -0.5, 0.5, 5.0),
                                   (0.7071, 0.5, -0.5, -5.0),
                                   (0, 0.7071, 0.7071, 5.0),
                                   (0.0, 0.0, 0.0, 1.0)))
        bpy.context.scene.collection.objects.link(cam)
        camdata.show_background_images = True
        return cam

    def validate_cam_cache(self, cam, node):
        gp = cam.get("SD_Mask", None)
        if isinstance(gp, list):
            gp = [o for o in gp if o is not None]
            cam["SD_Mask"] = gp
        if not gp:
            cam.pop("SD_Mask", None)
            bpy.ops.sdn.mask(action="add", node_name=node.name, cam_name=cam.name)

    def active_cam_gp(self, cam):
        gp = cam.get("SD_Mask")
        if isinstance(gp, list):
            gp = gp[0]
        if gp is None:
            return
        # toggle to draw mask
        bpy.context.view_layer.objects.active = gp
        bpy.ops.object.mode_set(mode="PAINT_GPENCIL")

    def focus_cam(self, cam):
        bpy.context.scene.camera = cam
        for node in bpy.context.space_data.edit_tree.nodes:
            if node.bl_idname != "Mask":
                continue
            if not node.cam:
                continue
            gp = node.cam.get("SD_Mask", [])

            if not isinstance(gp, list):
                gp.hide_set(cam != node.cam)
                continue
            for i in gp:
                if not i:
                    continue
                i.hide_set(cam != node.cam)
        for area in bpy.context.screen.areas:
            if area.type != "VIEW_3D":
                continue
            area.spaces[0].region_3d.view_perspective = "CAMERA"
            area.spaces[0].overlay.show_overlays = True

    @classmethod
    def reg(cls):
        # km = wm.keyconfigs.addon.keymaps.new(name = '3D View Generic', space_type = 'VIEW_3D')
        # kmi = km.keymap_items.new(isolate_select.bl_idname, 'Q', 'PRESS')
        # addon_keymaps.append((km, kmi))
        wm = bpy.context.window_manager
        km = wm.keyconfigs.addon.keymaps.new(name="Node Editor", space_type="NODE_EDITOR")
        cls.kmi = km.keymap_items.new(**cls.shotcut)
        for k, v in cls.properties.items():
            setattr(cls.kmi.properties, k, v)
        cls.kmis.append((km, cls.kmi))

    @classmethod
    def unreg(cls):
        for km, kmi in cls.kmis:
            km.keymap_items.remove(kmi)
        cls.kmis.clear()


class Set_Render_Res(bpy.types.Operator):
    bl_idname = "sdn.set_render_res"
    bl_label = ""
    bl_translation_context = ctxt
    node_name: bpy.props.StringProperty(default="")

    def execute(self, context):
        node = bpy.context.space_data.edit_tree.nodes.get(self.node_name)
        if not node or not node.prev:
            return {"FINISHED"}
        bpy.context.scene.render.resolution_x = node.prev.size[0]
        bpy.context.scene.render.resolution_y = node.prev.size[1]
        bpy.context.scene.render.resolution_percentage = 100
        return {'FINISHED'}


class GetSelCol(bpy.types.Operator):
    bl_idname = "sdn.get_sel_col"
    bl_label = ""
    bl_translation_context = ctxt

    def execute(self, context):
        SELECTED_COLLECTIONS.clear()
        for item in context.selected_ids:
            if item.bl_rna.identifier == "Collection":
                SELECTED_COLLECTIONS.append(item.name)
        return {'FINISHED'}


def parse_node():
    logger.warn(_T("Parsing Node Start"))
    path = Path(__file__).parent / "object_info.json"
    object_info = {}
    if path.exists():
        object_info = json.load(path.open("r"))
    try:
        # linux may not include request
        try:
            from requests import get as ______
        except BaseException:
            from ..utils import PkgInstaller
            PkgInstaller.try_install("requests")
        import requests
        if WITH_PROXY:
            req = requests.get(f"{get_url()}/object_info")
        else:
            req = requests.get(f"{get_url()}/object_info", proxies={"http": None, "https": None})
        if req.status_code == 200:
            cur_object_info = req.json()
            object_info.update(cur_object_info)
            path.write_text(json.dumps(object_info, ensure_ascii=False, indent=2))
            object_info = cur_object_info
    except requests.exceptions.ConnectionError:
        logger.warn(_T("Server Launch Failed"))

    nodetree_desc = {}
    nodes_desc = {}
    sockets = {"*", }  # Enum/Int/Float/String/Bool 不需要socket
    # node input type -> {'required', 'optional', 'hidden'}
    # node_inp_type = set()
    # for node in object_info.values():
    #     node_inp_type.update(set(node["input"].keys()))
    # logger.info(f"Node Input Type -> {node_inp_type}")
    SOCKET_TYPE.clear()
    object_info["PrimitiveNode"] = {
        "input": {"required": {}},
        "output": ["*"],
        "output_is_list": [False],
        "output_name": [
            "Output"
        ],
        "name": "PrimitiveNode",
        "display_name": "Primitive",
        "description": "",
        "category": "Utils",
        "output_node": False
    }

    for name, desc in object_info.items():
        if "|pysssss" in name:
            continue
        SOCKET_TYPE[name] = {}
        cat = desc["category"]
        for inp, inp_desc in desc["input"].get("required", {}).items():

            stype = inp_desc[0]
            if isinstance(stype, list):
                sockets.add("ENUM")
                # 太长 不能注册为 socket type(<64)
                hash_type = md5(",".join(stype).encode()).hexdigest()
                sockets.add(hash_type)
                SOCKET_HASH_MAP[hash_type] = "ENUM"
                SOCKET_TYPE[name][inp] = hash_type
            else:
                sockets.add(inp_desc[0])
                SOCKET_TYPE[name][inp] = inp_desc[0]
        for inp, inp_desc in desc["input"].get("optional", {}).items():
            stype = inp_desc[0]
            if isinstance(stype, list):
                sockets.add("ENUM")
                hash_type = md5(",".join(stype).encode()).hexdigest()
                sockets.add(hash_type)
                SOCKET_HASH_MAP[hash_type] = "ENUM"
                SOCKET_TYPE[name][inp] = hash_type
            else:
                sockets.add(inp_desc[0])
                SOCKET_TYPE[name][inp] = inp_desc[0]
        for index, out_type in enumerate(desc.get("output", [])):
            desc["output"][index] = [out_type, out_type]
        for index, out_name in enumerate(desc.get("output_name", [])):
            if not out_name:
                continue
            desc["output"][index][1] = out_name
        cpath = cat.split("/")
        nodes_desc[name] = desc
        ncur = nodetree_desc

        while cpath:
            ccur = cpath.pop(0)
            if ccur not in ncur:
                ncur[ccur] = {"items": [], "menus": {}}
            if not cpath:
                ncur[ccur]["items"].append(name)
            else:
                ncur = ncur[ccur]["menus"]

    # input / output / name / category
    # logger.warn(nodes_desc)
    # logger.warn(nodetree_desc)
    nodes_desc_ = {
        'KSampler': {
            'input': {
                'required': {
                    'model': ['MODEL'],
                    'seed': ['INT', {'default': 0, 'min': 0, 'max': 18446744073709551615}],
                    'cfg': ['FLOAT', {'default': 8.0, 'min': 0.0, 'max': 100.0}],
                    'sampler_name': [['euler', 'euler_ancestral']],
                    'scheduler': [['karras', 'normal', 'simple', 'ddim_uniform']],
                    'positive': ['CONDITIONING'],
                    'latent_image': ['LATENT']}},
            'output': ['LATENT'],
            'output_name': ['LATENT'],  # optional
            'name': 'KSampler',
            'display_name': "",  # optional
            'description': '',
            'category': 'sampling'}
    }
    node_clss = []

    for nname, ndesc in nodes_desc.items():
        opt_types = ndesc["input"].get("optional", {})
        inp_types = {}
        for key, value in ndesc["input"].get("required", {}).items():
            inp_types[key] = value
            if key == "seed" or (nname == "KSamplerAdvanced" and key == "noise_seed"):
                inp_types["control_after_generate"] = [["fixed", "increment", "decrement", "randomize"]]

        inp_types.update(opt_types)
        out_types = ndesc["output"]
        # 节点初始化

        def init(self: NodeBase, context):
            # logger.error("INIT")
            self.inputs.clear()
            self.outputs.clear()

            self.apply_unique_id()
            # self.use_custom_color = True
            # self.color = self.dcolor
            for index, inp_name in enumerate(self.inp_types):
                inp = self.inp_types[inp_name]
                if not inp:
                    logger.error(f"{_T('None Input')}: %s", inp_name)
                    continue
                socket = inp[0]
                if isinstance(inp[0], list):
                    # socket = "ENUM"
                    socket = md5(",".join(inp[0]).encode()).hexdigest()
                    continue
                if socket in {"ENUM", "INT", "FLOAT", "STRING", "BOOLEAN"}:
                    continue
                # logger.warn(inp)
                in1 = self.inputs.new(socket, inp_name)
                in1.display_shape = "DIAMOND_DOT"
                # in1.link_limit = 0
                in1.index = index
            for index, [out_type, out_name] in enumerate(self.out_types):
                if out_type in {"ENUM", }:
                    continue
                out = self.outputs.new(out_type, out_name)
                out.display_shape = "DIAMOND_DOT"
                # out.link_limit = 0
                out.index = index
            self.calc_slot_index()

        def draw_buttons(self: NodeBase, context, layout: bpy.types.UILayout):
            for prop in self.__annotations__:
                if self.query_stat(prop):
                    continue
                if prop == "control_after_generate":
                    continue
                l = layout
                # 返回True 则不绘制
                if spec_draw(self, context, l, prop):
                    continue
                if self.is_base_type(prop) and get_ori_name(prop) in self.inp_types:
                    l = Ops_Swith_Socket.draw_prop(l, self, prop)
                l.prop(self, prop, text=prop, text_ctxt=ctxt)

        def find_icon(nname, inp_name, item):
            prev_path_list = get_icon_path(nname).get(inp_name)
            if not prev_path_list:
                return 0
            file_list = []
            for prev_path in prev_path_list:
                if not Path(prev_path).exists():
                    continue
                for file in Path(prev_path).iterdir():
                    file_list.append(file)
            item_prefix = Path(item).stem
            # file_list = [file for prev_path in prev_path_list for file in Path(prev_path).iterdir()]
            for file in file_list:
                if (item not in file.stem) and (item_prefix not in file.stem):
                    continue
                if file.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                    continue
                # logger.info(f"🌟 Found Icon -> {file.name}")
                return Icon.reg_icon(file.absolute())
            # logger.info(f"🌚 No Icon <- {file.name}")
            return Icon["NONE"]

        def validate_inp(inp):
            if not isinstance(inp, list):
                return
            if len(inp) <= 1:
                return
            if not isinstance(inp[1], dict):
                return
            PARAMS = {"default", "min", "max", "step", "soft_min", "soft_max", "description", "subtype", "update", "options", "multiline"}
            # 排除掉不需要的属性
            for key in list(inp[1].keys()):
                if key in PARAMS:
                    continue
                inp[1].pop(key)

        properties = {}
        for inp_name in inp_types:
            reg_name = get_reg_name(inp_name)
            inp = inp_types[inp_name]
            if not inp:
                logger.error(f"{_T('None Input')}: %s", inp)
                continue
            proptype = inp[0]
            if isinstance(inp[0], list):
                proptype = "ENUM"
            validate_inp(inp)
            if proptype not in {"ENUM", "INT", "FLOAT", "STRING", "BOOLEAN"}:
                continue
            if proptype == "ENUM":

                def get_items(nname, inp_name, inp):
                    def wrap(self, context):
                        if nname not in ENUM_ITEMS_CACHE:
                            ENUM_ITEMS_CACHE[nname] = {}
                        if inp_name in ENUM_ITEMS_CACHE[nname]:
                            return ENUM_ITEMS_CACHE[nname][inp_name]
                        items = []
                        for item in inp[0]:
                            icon_id = find_icon(nname, inp_name, item)
                            if icon_id:
                                ENUM_ITEMS_CACHE[nname][inp_name] = items
                            items.append((item, item, "", icon_id, len(items)))
                        return items
                    return wrap

                prop = bpy.props.EnumProperty(items=get_items(nname, reg_name, inp))
            elif proptype == "INT":
                # {'default': 20, 'min': 1, 'max': 10000}
                inp[1]["max"] = min(int(inp[1].get("max", 9999999)), 2**31 - 1)
                inp[1]["min"] = max(int(inp[1].get("min", -999999)), -2**31)
                default = inp[1].get("default", 0)
                if not default:
                    default = 0
                inp[1]["default"] = int(default)
                inp[1]["step"] = ceil(inp[1].get("step", 1))
                prop = bpy.props.IntProperty(**inp[1])

            elif proptype == "FLOAT":
                {'default': 8.0, 'min': 0.0, 'max': 100.0}
                if "step" in inp[1]:
                    inp[1]["step"] *= 100
                prop = bpy.props.FloatProperty(**inp[1])
            elif proptype == "BOOLEAN":
                prop = bpy.props.BoolProperty(**inp[1])
            elif proptype == "STRING":
                {'default': 'ComfyUI', 'multiline': True}
                subtype = "NONE"

                def update_wrap(n=""):
                    i_name = n

                    def wrap(self, context):
                        if not self[i_name]:
                            return
                        if not self[i_name].startswith("//"):
                            return
                        self[i_name] = bpy.path.abspath(self[i_name])
                    return wrap
                update = update_wrap(inp_name)
                if inp_name == "image":
                    subtype = "FILE_PATH"
                elif inp_name == "output_dir":
                    subtype = "DIR_PATH"
                else:
                    def update(_, __): return
                prop = bpy.props.StringProperty(default=str(inp[1].get("default", "")),
                                                subtype=subtype,
                                                update=update)
            prop = spec_gen_properties(nname, inp_name, prop)
            properties[reg_name] = prop
        spec_extra_properties(properties, nname, ndesc)
        fields = {"init": init,
                  "inp_types": inp_types,
                  "out_types": out_types,
                  "class_type": nname,
                  "bl_label": nname,
                  "draw_buttons": draw_buttons,
                  "__annotations__": properties,
                  "__metadata__": ndesc
                  }
        spec_functions(fields, nname, ndesc)
        NodeDesc = type(nname, (NodeBase,), fields)
        NodeDesc.dcolor = (rand() / 2, rand() / 2, rand() / 2)
        node_clss.append(NodeDesc)
    socket_clss = []
    for stype in sockets:
        {'STYLE_MODEL', 'VAE', 'CLIP_VISION', 'MASK', 'UPSCALE_MODEL', 'FLOAT', 'CLIP_VISION_OUTPUT', 'STRING', 'INT', 'IMAGE', 'MODEL', 'CONDITIONING', 'ENUM', 'CONTROL_NET', 'LATENT', 'CLIP'}
        if stype in {"ENUM", }:
            continue

        def draw(self, context, layout, node: NodeBase, text):
            prop = get_reg_name(self.name)
            if self.is_output or not hasattr(node, prop):
                layout.label(text=self.name, text_ctxt=ctxt)
                return
            row = layout.row(align=True)
            row.label(text=prop, text_ctxt=ctxt)
            op = row.operator(Ops_Swith_Socket.bl_idname, text="", icon="UNLINKED")
            op.node_name = node.name
            op.socket_name = self.name
            op.action = "ToProp"
            row.prop(node, prop, text="", text_ctxt=ctxt)
        color = bpy.props.FloatVectorProperty(size=4, default=(rand()**0.5, rand()**0.5, rand()**0.5, 1))
        fields = {"draw": draw, "bl_label": stype, "__annotations__": {"color": color,
                                                                       "index": bpy.props.IntProperty(default=-1),
                                                                       "slot_index": bpy.props.IntProperty(default=-1)}}
        SocketDesc = type(stype, (SocketBase,), fields)
        socket_clss.append(SocketDesc)

    nodes_ = {
        'sampling': {
            'items': ['KSampler', 'KSamplerAdvanced']},
        'conditioning': {
            'items': ['CLIPTextEncode', 'CLIPSetLastLayer', 'ConditioningCombine', 'ConditioningSetArea', 'ControlNetApply'],
            'menus': {'style_model': {'items': ['StyleModelApply']}}},
        '无限圣杯': {
            'items': ['存储', 'ToBlender', 'Mask', '蜡笔']}
    }
    logger.warn(_T("Parsing Node Finished!"))
    return nodetree_desc, node_clss, socket_clss


def spec_gen_properties(nname, inp_name, prop):

    def set_sync_rand(self, seed):
        if not getattr(self, "sync_rand", False):
            return
        tree = bpy.context.space_data.edit_tree
        for node in tree.get_nodes():
            if node == self:
                continue
            if hasattr(node, "seed"):
                node["seed"] = seed
            elif node.class_type == "KSamplerAdvanced":
                node["noise_seed"] = seed

    if nname == "KSamplerAdvanced":
        if inp_name == "noise_seed":
            def setter(self, v):
                try:
                    _ = int(v)
                    self["noise_seed"] = v
                except Exception:
                    ...
                set_sync_rand(self, self["noise_seed"])

            def getter(self):
                if "noise_seed" not in self:
                    self["noise_seed"] = "0"
                return str(self["noise_seed"])
            prop = bpy.props.StringProperty(default="0", set=setter, get=getter)
    elif inp_name == "seed":
        def setter(self, v):
            try:
                _ = int(v)
                self["seed"] = v
            except Exception:
                ...
            set_sync_rand(self, self["seed"])

        def getter(self):
            if "seed" not in self:
                self["seed"] = "0"
            return str(self["seed"])
        prop = bpy.props.StringProperty(default="0", set=setter, get=getter)
    return prop


def spec_extra_properties(properties, nname, ndesc):
    if nname == "输入图像":
        prop = bpy.props.PointerProperty(type=bpy.types.Image)
        properties["prev"] = prop
        prop = bpy.props.StringProperty(default="", name="Render Layer")
        properties["render_layer"] = prop

        def search_layers(self, context):
            items = []
            if not bpy.context.scene.use_nodes:
                return items
            nodes = bpy.context.scene.node_tree.nodes
            render_layer = nodes.get(self.render_layer, None)
            if not render_layer:
                return items
            for output in render_layer.outputs:
                if not output.enabled:
                    continue
                items.append((output.name, output.name, "", len(items)))
            return items
        prop = bpy.props.EnumProperty(items=search_layers, name="Output Layer")
        properties["out_layers"] = prop
        prop = bpy.props.StringProperty(name="Frames Directory",
                                        subtype="DIR_PATH",
                                        default=Path.home().joinpath("Desktop").as_posix())
        properties["frames_dir"] = prop
        prop = bpy.props.BoolProperty(default=False)
        properties["disable_render"] = prop
    elif nname == "存储":
        items = [("Save", "Save", "", "", 0),
                 ("Import", "Import", "", "", 1),
                 ]
        prop = bpy.props.EnumProperty(items=items)
        properties["mode"] = prop
        prop = bpy.props.PointerProperty(type=bpy.types.Object)
        properties["obj"] = prop
        prop = bpy.props.PointerProperty(type=bpy.types.Image)
        properties["image"] = prop

    elif nname == "Mask":
        items = [("Grease Pencil", "Grease Pencil", "", "", 0),
                 ("Object", "Object", "", "", 1),
                 ("Collection", "Collection", "", "", 2),
                 ("Focus", "Focus", "", "", 3),
                 ]
        prop = bpy.props.EnumProperty(items=items)
        properties["mode"] = prop
        prop = bpy.props.PointerProperty(type=bpy.types.Object, poll=lambda s, o: o.type == "GPENCIL")
        properties["gp"] = prop
        prop = bpy.props.PointerProperty(type=bpy.types.Object, poll=lambda s, o: o.type == "CAMERA")
        properties["cam"] = prop
        prop = bpy.props.BoolProperty(default=False)
        properties["disable_render"] = prop
        # prop = bpy.props.PointerProperty(type=bpy.types.Object)
        # properties["obj"] = prop
        # prop = bpy.props.PointerProperty(type=bpy.types.Collection)
        # properties["col"] = prop
    elif nname == "预览":
        prop = bpy.props.CollectionProperty(type=Images)
        properties["prev"] = prop
        properties["lnum"] = bpy.props.IntProperty(default=3, min=1, max=10, name="Image num per line")
    elif nname == "KSamplerAdvanced" or "seed" in properties:
        prop = bpy.props.BoolProperty(default=False)
        properties["exe_rand"] = prop

        def update_sync_rand(self: bpy.types.Node, context):
            if not self.sync_rand:
                return
            tree = bpy.context.space_data.edit_tree
            for node in tree.get_nodes():
                if (not hasattr(node, "seed") and node.class_type != "KSamplerAdvanced") or node == self:
                    continue
                node.sync_rand = False
        prop = bpy.props.BoolProperty(default=False, name="Sync Rand", description="Sync Rand", update=update_sync_rand)
        properties["sync_rand"] = prop
    elif nname == "MultiAreaConditioning":
        config = [{"x": 0,
                   "y": 0,
                   "sdn_width": 0,
                   "sdn_height": 0,
                   "strength": 1.0,
                   "col": (1, 0, 1, 0.5)
                   },
                  {"x": 0,
                   "y": 0,
                   "sdn_width": 0,
                   "sdn_height": 0,
                   "strength": 1.0,
                   "col": (0, 1, 1, 0.5)
                   }]

        def config_set(self, value):
            self["config"] = value

        def config_get(self):
            if 'config' not in self:
                self['config'] = json.dumps(config)
            return self['config']

        prop = bpy.props.StringProperty(default=json.dumps(config), set=config_set, get=config_get)
        properties["config"] = prop

        def update(self):
            config = json.loads(self.config)
            c = config[self.index]
            for k in c:
                if k not in self:
                    continue
                c[k] = getattr(self, k)
            self.config = json.dumps(config)

        def resolutionX_set(self, value):
            self['resolutionX'] = (value // 64) * 64

        def resolutionX_get(self):
            if 'resolutionX' not in self:
                self['resolutionX'] = 0
            return self['resolutionX']
        prop = bpy.props.IntProperty(default=0, min=0, max=4096, set=resolutionX_set, get=resolutionX_get)
        properties["resolutionX"] = prop

        def resolutionY_set(self, value):
            self['resolutionY'] = (value // 64) * 64

        def resolutionY_get(self):
            if 'resolutionY' not in self:
                self['resolutionY'] = 0
            return self['resolutionY']
        prop = bpy.props.IntProperty(default=0, min=0, max=4096, set=resolutionY_set, get=resolutionY_get)
        properties["resolutionY"] = prop

        def update_index(self, context):
            config = json.loads(self.config)
            c = config[self.index]
            for k in c:
                if k not in self:
                    continue
                self[k] = c[k]

        prop = bpy.props.IntProperty(default=0, min=0, max=1, update=update_index)
        properties["index"] = prop

        def x_set(self, value):
            self['x'] = (value // 64) * 64
            update(self)

        def x_get(self):
            if 'x' not in self:
                self['x'] = 0
            return self['x']
        prop = bpy.props.IntProperty(default=0, min=0, max=4096, set=x_set, get=x_get)
        properties["x"] = prop

        def y_set(self, value):
            self['y'] = (value // 64) * 64
            update(self)

        def y_get(self):
            if 'y' not in self:
                self['y'] = 0
            return self['y']
        prop = bpy.props.IntProperty(default=0, min=0, max=4096, set=y_set, get=y_get)
        properties["y"] = prop

        def sdn_width_set(self, value):
            self['sdn_width'] = (value // 64) * 64
            update(self)

        def sdn_width_get(self):
            if 'sdn_width' not in self:
                self['sdn_width'] = 0
            return self['sdn_width']
        prop = bpy.props.IntProperty(default=0, min=0, max=4096, set=sdn_width_set, get=sdn_width_get)
        properties["sdn_width"] = prop

        def sdn_height_set(self, value):
            self['sdn_height'] = (value // 64) * 64
            update(self)

        def sdn_height_get(self):
            if 'sdn_height' not in self:
                self['sdn_height'] = 0
            return self['sdn_height']
        prop = bpy.props.IntProperty(default=0, min=0, max=4096, set=sdn_height_set, get=sdn_height_get)
        properties["sdn_height"] = prop

        def update_strength(self, context):
            update(self)
        prop = bpy.props.FloatProperty(default=1.0, min=0, max=10, update=update_strength)
        properties["strength"] = prop
    elif nname == "PrimitiveNode":
        prop = bpy.props.StringProperty()
        properties["prop"] = prop


def spec_serialize_pre(self):
    def get_sync_rand_node():
        tree = bpy.context.space_data.edit_tree
        for node in tree.get_nodes():
            # node不是KSampler、KSamplerAdvanced 跳过
            if not hasattr(node, "seed") and node.class_type != "KSamplerAdvanced":
                continue
            if node.sync_rand:
                return node

    if hasattr(self, "seed"):
        if (snode := get_sync_rand_node()) and snode != self:
            return
        if not self.exe_rand and not bpy.context.scene.sdn.rand_all_seed:
            return
        self.seed = str(get_fixed_seed())

    elif self.class_type == "KSamplerAdvanced":
        if (snode := get_sync_rand_node()) and snode != self:
            return
        if not self.exe_rand and not bpy.context.scene.sdn.rand_all_seed:
            return
        self.noise_seed = str(get_fixed_seed())
    elif self.class_type == "PrimitiveNode":
        if not self.outputs[0].is_linked:
            return
        prop = getattr(self.outputs[0].links[0].to_node, get_reg_name(self.prop))
        for link in self.outputs[0].links[1:]:
            setattr(link.to_node, get_reg_name(link.to_socket.name), prop)
    elif self.class_type == "预览":
        if self.inputs[0].is_linked:
            return
        self.prev.clear()


def spec_serialize(self, cfg, execute):
    def hide_gp():
        if (cam := bpy.context.scene.camera) and (gpos := cam.get("SD_Mask", [])):
            try:
                for gpo in gpos:
                    gpo.hide_render = True
            except BaseException:
                ...
    if not execute:
        return
    if self.class_type == "输入图像":
        ...
        # if self.mode == "渲染":
        #     logger.warn(f"{_T('Render')}->{self.image}")
        #     bpy.context.scene.render.filepath = self.image
        #     hide_gp()
        #     bpy.ops.render.render(write_still=True)
        # elif self.mode == "输入":
        #     ...
    elif self.class_type == "Mask":
        # print(self.channel)
        if self.disable_render or bpy.context.scene.sdn.disable_render_all:
            return
        gen_mask(self)
    elif hasattr(self, "seed"):
        cfg["inputs"]["seed"] = int(cfg["inputs"]["seed"])
    elif self.class_type == "KSamplerAdvanced":
        cfg["inputs"]["noise_seed"] = int(cfg["inputs"]["noise_seed"])
    elif self.class_type in {"OpenPoseFull", "OpenPoseHand", "OpenPoseMediaPipeFace", "OpenPoseDepth", "OpenPose", "OpenPoseFace", "OpenPoseLineart", "OpenPoseFullExtraLimb", "OpenPoseKeyPose", "OpenPoseCanny", }:
        rpath = Path(bpy.path.abspath(bpy.context.scene.render.filepath)) / "MultiControlnet"
        cfg["inputs"]["image"] = rpath.as_posix()
        cfg["inputs"]["frame"] = bpy.context.scene.frame_current


def spec_functions(fields, nname, ndesc):
    if nname == "输入图像":
        def render(self: NodeBase):
            if self.mode != "渲染":
                return
            if self.disable_render or bpy.context.scene.sdn.disable_render_all:
                return

            @Timer.wait_run
            def r():
                logger.warn(f"{_T('Render')}->{self.image}")
                bpy.context.scene.render.filepath = self.image
                if (cam := bpy.context.scene.camera) and (gpos := cam.get("SD_Mask", [])):
                    try:
                        for gpo in gpos:
                            gpo.hide_render = True
                    except BaseException:
                        ...
                if bpy.context.scene.use_nodes:
                    from .utils import set_composite
                    nt = bpy.context.scene.node_tree

                    with set_composite(nt) as cmp:
                        render_layer: bpy.types.CompositorNodeRLayers = nt.nodes.new("CompositorNodeRLayers")
                        if sel_render_layer := nt.nodes.get(self.render_layer, None):
                            render_layer.scene = sel_render_layer.scene
                            render_layer.layer = sel_render_layer.layer
                        if out := render_layer.outputs.get(self.out_layers):
                            nt.links.new(cmp.inputs["Image"], out)
                        bpy.ops.render.render(write_still=True)
                        nt.nodes.remove(render_layer)
                else:
                    bpy.ops.render.render(write_still=True)
            r()
        fields["pre_fn"] = render
    if nname == "存储":
        def post_fn(self: NodeBase, t: Task, result):
            logger.debug(f"{self.class_type}{_T('Post Function')}->{result}")
            img_paths = result.get("output", {}).get("images", [])
            for img in img_paths:
                if self.mode == "Save":
                    def f(self, img):
                        return bpy.data.images.load(img)
                elif self.mode == "Import":
                    def f(self, img):
                        self.image.filepath = img
                        self.image.filepath_raw = img
                        self.image.source = "FILE"
                        if self.image.packed_file:
                            self.image.unpack(method="REMOVE")
                        self.image.reload()
                Timer.put((f, self, img))

        fields["post_fn"] = post_fn
    if nname == "预览":
        def post_fn(self: NodeBase, t: Task, result):
            logger.debug(f"{self.class_type}{_T('Post Function')}->{result}")
            # return
            img_paths = result.get("output", {}).get("images", [])
            if not img_paths:
                return
            logger.warn(f"{_T('Load Preview Image')}: {img_paths}")

            def f(self, img_paths: list[str]):
                self.prev.clear()
                for img_path in img_paths:
                    p = self.prev.add()
                    p.image = bpy.data.images.load(img_path)
            Timer.put((f, self, img_paths))

        fields["post_fn"] = post_fn


def spec_draw(self: NodeBase, context: bpy.types.Context, layout: bpy.types.UILayout, prop: str, swlink=True):
    def draw_prop_with_link(layout, self, prop, row=True, pre=None, post=None, **kwargs):
        layout = Ops_Swith_Socket.draw_prop(layout, self, prop, row, swlink)
        if pre:
            pre(layout)
        layout.prop(self, prop, **kwargs)
        if post:
            post(layout)
        return layout
    if self.bl_idname == "PrimitiveNode":
        if self.outputs[0].is_linked and self.outputs[0].links:
            node = self.outputs[0].links[0].to_node
            # 可能会导致prop在node中找不到的情况(断开连接的时候)
            if not hasattr(node, self.prop):
                return True
            if spec_draw(node, context, layout, self.prop, swlink=False):
                return True
            layout.prop(node, get_reg_name(self.prop))
        return True
    if prop == "control_after_generate":
        return True
    # 多行文本处理
    md = self.get_meta(prop)
    if md and md[0] == "STRING" and len(md) > 1 and isinstance(md[1], dict) and md[1].get("multiline",):
        width = int(self.width) // 7
        lines = textwrap.wrap(text=str(getattr(self, prop)), width=width)
        for line in lines:
            layout.label(text=line, text_ctxt=ctxt)
        row = draw_prop_with_link(layout, self, prop)
        row.operator("sdn.enable_mlt", text="", icon="TEXT")
        return True

    def show_model_preview(self: NodeBase, context: bpy.types.Context, layout: bpy.types.UILayout, prop: str):
        if self.class_type not in name2path:
            return False
        if prop not in get_icon_path(self.class_type):
            return False
        col = draw_prop_with_link(layout, self, prop, text="", row=False)
        col.template_icon_view(self, prop, show_labels=True, scale_popup=popup_scale, scale=popup_scale)
        return True

    def setwidth(self: NodeBase, w, with_max=False):
        w = max(self.bl_width_min, w)
        if not with_max:
            w = min(self.bl_width_max, w)
        if self.width == w:
            return

        def delegate(self, w):
            self.width = w
            if with_max and self.bl_width_max < w:
                self.bl_width_max = w

        Timer.put((delegate, self, w))
    popup_scale = 5
    try:
        popup_scale = get_pref().popup_scale
    except BaseException:
        ...
    if show_model_preview(self, context, layout, prop):
        return True
    if hasattr(self, "seed"):
        if prop == "seed":
            row = draw_prop_with_link(layout, self, prop, text_ctxt=ctxt)
            row.prop(self, "exe_rand", text="", icon="FILE_REFRESH", text_ctxt=ctxt)
            row.prop(bpy.context.scene.sdn, "rand_all_seed", text="", icon="HAND", text_ctxt=ctxt)
            row.prop(self, "sync_rand", text="", icon="MOD_WAVE", text_ctxt=ctxt)
            return True
        if prop in {"exe_rand", "sync_rand"}:
            return True
    elif self.class_type == "KSamplerAdvanced":
        if prop in {"add_noise", "return_with_leftover_noise"}:
            def dpre(layout): layout.label(text=prop, text_ctxt=ctxt)
            draw_prop_with_link(layout, self, prop, expand=True, pre=dpre, text_ctxt=ctxt)
            return True
        if prop == "noise_seed":
            def dpost(layout):
                layout.prop(self, "exe_rand", text="", icon="FILE_REFRESH", text_ctxt=ctxt)
                layout.prop(bpy.context.scene.sdn, "rand_all_seed", text="", icon="HAND", text_ctxt=ctxt)
                layout.prop(self, "sync_rand", text="", icon="MOD_WAVE", text_ctxt=ctxt)
            draw_prop_with_link(layout, self, prop, post=dpost, text_ctxt=ctxt)
            return True
        if prop in {"exe_rand", "sync_rand"}:
            return True

    elif self.class_type == "输入图像":
        if prop == "mode":
            if self.mode == "序列图":
                # draw_prop_with_link(layout, self, "frames_dir", text="", text_ctxt=ctxt)
                layout.prop(self, "frames_dir", text="")
            else:
                # draw_prop_with_link(layout, self, "image", text="", text_ctxt=ctxt)
                layout.prop(self, "image", text="", text_ctxt=ctxt)
            # draw_prop_with_link(layout.row(), self, prop, expand=True, text_ctxt=ctxt)
            layout.row().prop(self, prop, expand=True, text_ctxt=ctxt)
            if self.mode == "序列图":
                layout.label(text="Frames Directory", text_ctxt=ctxt)
            if self.mode == "渲染":
                layout.label(text="Set Image Path of Render Result(.png)", icon="ERROR")
                if bpy.context.scene.use_nodes:
                    row = layout.row(align=True)
                    row.prop_search(self, "render_layer", bpy.context.scene.node_tree, "nodes")
                    icon = "RESTRICT_RENDER_ON" if self.disable_render else "RESTRICT_RENDER_OFF"
                    row.prop(self, "disable_render", text="", icon=icon)
                    icon = "HIDE_ON" if bpy.context.scene.sdn.disable_render_all else "HIDE_OFF"
                    row.prop(bpy.context.scene.sdn, "disable_render_all", text="", icon=icon)
                    layout.prop(self, "out_layers")
            return True
        elif prop == "image":
            if os.path.exists(self.image):
                def f(self):
                    Icon.load_icon(self.image)
                    if not (img := Icon.find_image(self.image)):
                        return
                    self.prev = img
                    w = max(self.prev.size[0], self.prev.size[1])
                    setwidth(self, w)
                    update_screen()
                if Icon.try_mark_image(self.image) or not self.prev:
                    Timer.put((f, self))
                elif Icon.find_image(self.image) != self.prev:  # 修复加载A 后加载B图, 再加载A时 不更新
                    Timer.put((f, self))
            elif self.prev:
                def f(self):
                    self.prev = None
                    update_screen()
                Timer.put((f, self))
            return True
        elif prop == "prev":
            if self.prev:
                Icon.reg_icon_by_pixel(self.prev, self.prev.filepath)
                icon_id = Icon[self.prev.filepath]
                row = layout.row(align=True)
                row.label(text=f"{self.prev.file_format} : [{self.prev.size[0]} x {self.prev.size[1]}]")
                row.operator(Set_Render_Res.bl_idname, text="", icon="LOOP_FORWARDS").node_name = self.name
                layout.template_icon(icon_id, scale=max(self.prev.size[0], self.prev.size[1]) // 20)
            return True
        elif prop in {"render_layer", "out_layers", "frames_dir", "disable_render"}:
            return True
    elif self.class_type == "存储":
        if prop == "mode":
            layout.prop(self, prop, expand=True, text_ctxt=ctxt)
            if self.mode == "Save":
                layout.prop(self, "filename_prefix", text_ctxt=ctxt)
                layout.prop(self, "output_dir", text_ctxt=ctxt)
                return True
            elif self.mode == "Import":
                layout.prop(self, "obj", text="")
                if obj := self.obj:
                    def find_tex(root, tex=None) -> list[bpy.types.Image]:
                        tex = tex if tex else []
                        for node in root.node_tree.nodes:
                            if node.type in {"TEX_IMAGE", "TEX_ENVIRONMENT"}:
                                if not node.image:
                                    continue
                                tex.append(node.image)
                            if node.type == "GROUP":
                                find_tex(node, tex)
                        return tex
                    if textures := find_tex(obj.active_material):
                        box = layout.box()
                        for t in textures:
                            row = box.row(align=True)
                            row.label(text=t.name)
                            col = row.column()
                            if t == self.image:
                                col.alert = True
                            op = col.operator(Ops_Active_Tex.bl_idname, text="", icon="REC")
                            op.node_name = self.name
                            op.img_name = t.name
                if self.image:
                    layout.label(text=f"当前纹理: {self.image.name}")
                return True
        return True
    elif self.class_type == "Mask":
        if prop == "mode":
            layout.prop(self, prop, expand=True, text_ctxt=ctxt)
            if self.mode == "Grease Pencil":
                row = layout.row(align=True)
                row.prop(self, "gp", text="", text_ctxt=ctxt)
                icon = "RESTRICT_RENDER_ON" if self.disable_render else "RESTRICT_RENDER_OFF"
                row.prop(self, "disable_render", text="", icon=icon)
                icon = "HIDE_ON" if bpy.context.scene.sdn.disable_render_all else "HIDE_OFF"
                row.prop(bpy.context.scene.sdn, "disable_render_all", text="", icon=icon)
                row.operator("sdn.mask", text="", icon="ADD").node_name = self.name
            if self.mode == "Object":
                row = layout.row(align=True)
                row.label(text="  Select mask Objects", text_ctxt=ctxt)
                icon = "RESTRICT_RENDER_ON" if self.disable_render else "RESTRICT_RENDER_OFF"
                row.prop(self, "disable_render", text="", icon=icon)
                icon = "HIDE_ON" if bpy.context.scene.sdn.disable_render_all else "HIDE_OFF"
                row.prop(bpy.context.scene.sdn, "disable_render_all", text="", icon=icon)
            if self.mode == "Collection":
                row = layout.row(align=True)
                row.label(text="  Select mask Collections", text_ctxt=ctxt)
                icon = "RESTRICT_RENDER_ON" if self.disable_render else "RESTRICT_RENDER_OFF"
                row.prop(self, "disable_render", text="", icon=icon)
                icon = "HIDE_ON" if bpy.context.scene.sdn.disable_render_all else "HIDE_OFF"
                row.prop(bpy.context.scene.sdn, "disable_render_all", text="", icon=icon)
            if self.mode == "Focus":
                row = layout.row(align=True)
                row.prop(self, "cam", text="")
                icon = "RESTRICT_RENDER_ON" if self.disable_render else "RESTRICT_RENDER_OFF"
                row.prop(self, "disable_render", text="", icon=icon)
                icon = "HIDE_ON" if bpy.context.scene.sdn.disable_render_all else "HIDE_OFF"
                row.prop(bpy.context.scene.sdn, "disable_render_all", text="", icon=icon)
                op = row.operator(Ops_Link_Mask.bl_idname, text="", icon="VIEW_CAMERA")
                op.action = "OnlyFocus"
                op.cam_name = self.cam.name if self.cam else ""
                op.node_name = self.name
            return True
        elif prop in {"gp", "obj", "col", "cam", "disable_render"}:
            return True
    elif self.class_type == "预览":
        if prop == "lnum":
            return True
        if self.inputs[0].is_linked and self.inputs[0].links:
            for link in self.inputs[0].links[0].from_socket.links:
                if link.to_node.bl_idname == "存储":
                    break
            else:
                layout.operator(Ops_Add_SaveImage.bl_idname, text="", icon="FILE_TICK").node_name = self.name
        if prop == "prev":
            layout.prop(self, "lnum")
            pnum = len(self.prev)
            if pnum == 0:
                return True
            p0 = self.prev[0].image
            w = max(p0.size[0], p0.size[1])
            setwidth(self, w * min(self.lnum, pnum), with_max=True)
            layout.label(text=f"{p0.file_format} : [{p0.size[0]} x {p0.size[1]}]")
            col = layout.column(align=True)
            for i, p in enumerate(self.prev):
                if i % self.lnum == 0:
                    row = col.row(align=True)
                    row.alignment = "LEFT"
                prev = p.image
                if prev.name not in Icon:
                    Icon.reg_icon_by_pixel(prev, prev.name)
                icon_id = Icon[prev.name]
                row.template_icon(icon_id, scale=max(prev.size[0], prev.size[1]) // 20)
            return True
    elif self.class_type in {"OpenPoseFull", "OpenPoseHand", "OpenPoseMediaPipeFace", "OpenPoseDepth", "OpenPose", "OpenPoseFace", "OpenPoseLineart", "OpenPoseFullExtraLimb", "OpenPoseKeyPose", "OpenPoseCanny", }:
        return True
    elif self.class_type == "MultiAreaConditioning":
        if prop == "config":
            return True
    return False


class Images(bpy.types.PropertyGroup):
    image: bpy.props.PointerProperty(type=bpy.types.Image)


clss = [Ops_Swith_Socket, Ops_Add_SaveImage, Set_Render_Res, GetSelCol, Ops_Active_Tex, Ops_Link_Mask, Images]

reg, unreg = bpy.utils.register_classes_factory(clss)


def nodes_reg():
    reg()
    Ops_Link_Mask.reg()


def nodes_unreg():
    ENUM_ITEMS_CACHE.clear()
    try:
        unreg()
    except BaseException:
        ...
    Ops_Link_Mask.unreg()
