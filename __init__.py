bl_info = {
    'name': '无限圣杯-节点',
    'author': '幻之境开发小组-会飞的键盘侠、只剩一瓶辣椒酱',
    'version': (1, 2, 3),
    'blender': (3, 0, 0),
    'location': '3DView->Panel',
    'category': '辣椒出品',
    'doc_url': "https://shimo.im/docs/Ee32m0w80rfLp4A2"
}

import bpy
import sys
from .SDNode import rtnode_unreg, TaskManager
from .MultiLineText import EnableMLT

from .translation import translations_dict
from .utils import Icon
from .timer import timer_reg, timer_unreg
from .preference import AddonPreference
from .ops import Ops, Ops_Mask
from .ui import Panel
from .prop import Prop


clss = [Panel, Ops, Prop, Ops_Mask, EnableMLT]
reg, unreg = bpy.utils.register_classes_factory(clss)

def dump_info():
    import json
    from .preference import get_pref
    if "--get-blender-ai-node-info" in sys.argv:
        model_path = getattr(get_pref(), 'model_path')
        info = {"Version": ".".join([str(i) for i in bl_info["version"]]), "ComfyUIPath": model_path}
        sys.stderr.write(f"BlenderComfyUIInfo: {json.dumps(info)} BlenderComfyUIend")
        sys.stderr.flush()

def register():
    bpy.utils.register_class(AddonPreference)
    if "-b" in sys.argv or "--background" in sys.argv:
        dump_info()
        return
    bpy.app.translations.register(__name__, translations_dict)
    reg()
    Icon.set_hq_preview()
    TaskManager.run_server(fake=True)
    timer_reg()
    bpy.types.Scene.sdn = bpy.props.PointerProperty(type=Prop)

def unregister():
    bpy.utils.unregister_class(AddonPreference)
    if "-b" in sys.argv or "--background" in sys.argv:
        return
    bpy.app.translations.unregister(__name__)
    unreg()
    rtnode_unreg()
    timer_unreg()
    del bpy.types.Scene.sdn
    modules_update()

def modules_update():
    from .kclogger import close_logger
    close_logger()
    import sys
    modules = []
    for i in sys.modules.keys():
        if i.startswith(__package__) and i != __package__:
            modules.append(i)
    for i in modules:
        del sys.modules[i]
    del sys.modules[__package__]
