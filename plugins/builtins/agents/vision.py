"""视觉 Agent — 只看当前截图，返回相对坐标"""
import json
import re
import base64
from PIL import Image
from io import BytesIO
import core

SYSTEM_PROMPT = """你是桌面操作助手。给你一张截图和用户意图，返回严格 JSON。

输出固定结构：
{
  "done": false,
  "thinking": "分析过程",
  "result": "给用户的反馈",
  "actions": [],
  "error": "",
  "img_meta": {
    "norm": true,
    "infer_width": null,
    "infer_height": null
  }
}
img_meta说明：
- norm: true=所有坐标使用0~1归一化比例(推荐，无偏移)；false=使用像素坐标，必须填写infer_width/infer_height（模型推理时图片宽高）

【坐标标准 二选一，优先norm=true归一化】
1. 归一化模式(norm=true，推荐，彻底解决缩放偏移)
画面左上角(0,0)，右下角(1,1)
点击坐标(x,y)：x=横向占比0~1，y=纵向占比0~1
输入框/元素区域：[x1,y1,x2,y2] 左上/右下比例，0~1之间
2. 像素模式(norm=false，不推荐)
仅当无法输出比例时使用，必须同步提供推理图宽高infer_width、infer_height，坐标为推理图像素，后续会自动换算原图

操作类型：
- click: {"type":"click","x":0.5,"y":0.8,"label":"点击xxx"} 归一化坐标
- click像素版: {"type":"click","x":640,"y":900,"label":"点击xxx"} 仅norm=false时用
- type: {"type":"type","text":"输入内容"}
- hotkey: {"type":"hotkey","keys":["ctrl","v"]}
- scroll: {"type":"scroll","x":0.5,"y":0.8,"delta":-120}
- wait: {"type":"wait","seconds":2}
- screenshot: {"type":"screenshot","label":"截图确认"}

══════════════════════════
定位通用规则（全部基于比例，禁止目测像素）
══════════════════════════
1. 按钮/文字点击：取元素整体区域中心比例
   中心x = (区域左比例 + 区域右比例) / 2
   中心y = (区域上比例 + 区域下比例) / 2
2. 输入框：返回完整框线的比例区域 [x1,y1,x2,y2]，包含完整圆角边框
3. Excel单元格：
   列中心比例 = (前一列分隔线x比例 + 当前列右分隔线x比例) / 2
   行中心比例 = (上一行分隔线y比例 + 当前行下分隔线y比例) / 2
   输入必须连续双击两次click，间隔0.2s等待
4. 列表/侧边栏项：取整行垂直中心比例

══════════════════════════
执行流程强制要求
══════════════════════════
1. 需要输入文字：先result输出文本内容，再actions包含点击输入框+type输入+截图确认
2. 单次定位偏移修正逻辑：
   若上一轮坐标点偏，对比目标中心比例差值，修正一次比例；两次定位失败直接返回error
3. 操作完成后必须追加screenshot动作确认画面
4. allow_actions=false时actions必须为空数组
5. 分析查询类需求，在result文字中带上目标元素归一化矩形区域 [x1,y1,x2,y2]

通用约束：
1. done=true代表当前意图完全执行完毕
2. 找不到目标元素、两次修正仍定位失败 → error填原因，actions置空
3. type操作默认末尾自动回车换行
4. img_meta必须完整携带，norm优先填true
只返回纯净JSON，不要任何markdown、注释、额外文字。"""

# 新增：base64图片解析，返回原图宽高
def get_image_wh(base64_str: str) -> tuple[int, int]:
    """解析data:image/png;base64,xxx，返回原图width, height"""
    if base64_str.startswith("data:image"):
        base64_str = base64_str.split(",")[-1]
    img_bytes = base64.b64decode(base64_str)
    img = Image.open(BytesIO(img_bytes))
    return img.width, img.height

# 新增：坐标转换核心函数
def convert_action_coords(actions: list, norm: bool, infer_w: int, infer_h: int, origin_w: int, origin_h: int) -> list:
    """
    把LLM返回动作坐标统一转换成原始截图像素坐标
    :param actions: LLM原始actions数组
    :param norm: 是否归一化模式true/false
    :param infer_w: 模型推理图宽度（norm=false必填）
    :param infer_h: 模型推理图高度（norm=false必填）
    :param origin_w: 本地原始截图宽
    :param origin_h: 本地原始截图高
    :return: 转换后、可直接操作桌面的actions
    """
    new_actions = []
    for act in actions:
        act_copy = act.copy()
        t = act_copy.get("type")
        if t in ("click", "scroll"):
            x = act_copy.get("x")
            y = act_copy.get("y")
            if norm:
                # 归一化比例转原图像素（无误差）
                act_copy["x"] = int(round(x * origin_w))
                act_copy["y"] = int(round(y * origin_h))
            else:
                # 推理图像素 等比例映射回原图
                scale_x = origin_w / infer_w
                scale_y = origin_h / infer_h
                act_copy["x"] = int(round(x * scale_x))
                act_copy["y"] = int(round(y * scale_y))
        new_actions.append(act_copy)
    return new_actions

def convert_region(region: list, norm: bool, infer_w: int, infer_h: int, origin_w: int, origin_h: int) -> list:
    """
    转换矩形区域 [x1,y1,x2,y2] 到原图原生像素
    """
    if len(region) != 4:
        return []
    x1, y1, x2, y2 = region
    if norm:
        return [
            int(round(x1 * origin_w)),
            int(round(y1 * origin_h)),
            int(round(x2 * origin_w)),
            int(round(y2 * origin_h))
        ]
    else:
        scale_x = origin_w / infer_w
        scale_y = origin_h / infer_h
        return [
            int(round(x1 * scale_x)),
            int(round(y1 * scale_y)),
            int(round(x2 * scale_x)),
            int(round(y2 * scale_y))
        ]

# 从result文本提取归一化矩形坐标
def extract_region_from_text(text: str) -> list:
    # 匹配格式: [x1=0.393, y1=0.812, x2=0.965, y2=0.967]
    match = re.search(r'\[x1\s*=\s*([\d.]+),\s*y1\s*=\s*([\d.]+),\s*x2\s*=\s*([\d.]+),\s*y2\s*=\s*([\d.]+)\]', text, re.IGNORECASE)
    if match:
        return list(map(float, match.groups()))
    
    # 兼容纯数字格式: [0.393, 0.812, 0.965, 0.967]
    match = re.search(r'\[(\d+\.?\d*),\s*(\d+\.?\d*),\s*(\d+\.?\d*),\s*(\d+\.?\d*)\]', text)
    if match:
        return list(map(float, match.groups()))
    
    return []


async def execute(envelop, agent):
    """每次独立调用，只看当前截图"""
    image_base64 = envelop.payload.get("image", "")
    intent = envelop.payload.get("intent", "")
    allow_actions = envelop.payload.get("allow_actions", False)
    region = envelop.payload.get("region", {})

    if not image_base64 or not intent:
        envelop.payload = {"error": "缺少 image 或 intent"}
        return envelop

    if not agent.llm:
        envelop.payload = {"error": "LLM 不可用"}
        return envelop

    # 读取原始截图宽高（用于坐标换算）
    try:
        origin_w, origin_h = get_image_wh(image_base64)
    except Exception as e:
        envelop.payload = {"error": f"图片解析失败，无法获取尺寸: {e}"}
        return envelop

    action_hint = "可以返回操作指令" if allow_actions else "只分析，不返回操作"
    user_prompt = f"意图：{intent}\n{action_hint}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
            ]
        }
    ]

    try:
        reply = await agent.llm.chat(messages)
    except Exception as e:
        envelop.payload = {"error": f"LLM 失败: {e}"}
        return envelop

    # 解析 JSON
    reply_text = reply.strip()
    result = None
    attempts = [
        reply_text,
        reply_text[7:] if reply_text.startswith("```json") else "",
        reply_text[3:] if reply_text.startswith("```") else "",
        reply_text[:-3] if reply_text.endswith("```") else "",
    ]
    for text in attempts:
        if not text: continue
        try:
            result = json.loads(text.strip())
            break
        except json.JSONDecodeError:
            continue

    if result is None:
        match = re.search(r'\{[\s\S]*\}', reply_text)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    if result is None:
        envelop.payload = {"error": "JSON 解析失败", "result": reply_text[:500]}
        return envelop

    actions = result.get("actions", [])
    done = result.get("done", False)
    error = result.get("error", "")
    img_meta = result.get("img_meta", {"norm": True, "infer_width": None, "infer_height": None})
    raw_result_text = result.get("result", "")

    # LLM 主动报错，直接返回
    if error:
        envelop.payload = {
            "done": False,
            "thinking": result.get("thinking", ""),
            "result": raw_result_text,
            "error": error,
            "actions": [],
            "need_screenshot": False,
            "origin_image_size": f"{origin_w}*{origin_h}",
            "result_origin_coords": []
        }
        return envelop

    # ========== 1. 转换操作点击坐标 ==========
    converted_actions = []
    if allow_actions and actions:
        norm_flag = img_meta.get("norm", True)
        infer_w = img_meta.get("infer_width")
        infer_h = img_meta.get("infer_height")
        # 像素模式校验推理图尺寸参数
        if not norm_flag and (infer_w is None or infer_h is None):
            envelop.payload = {
                "done": False,
                "thinking": result.get("thinking", ""),
                "result": raw_result_text,
                "error": "LLM返回像素坐标但未提供推理图宽高，定位失效",
                "actions": [],
                "need_screenshot": False,
                "origin_image_size": f"{origin_w}*{origin_h}",
                "result_origin_coords": []
            }
            return envelop
        # 执行坐标映射转换
        converted_actions = convert_action_coords(
            actions=actions,
            norm=norm_flag,
            infer_w=infer_w,
            infer_h=infer_h,
            origin_w=origin_w,
            origin_h=origin_h
        )

    # ========== 2. 新增：提取并转换分析返回的元素区域坐标 ==========
    norm_flag = img_meta.get("norm", True)
    infer_w = img_meta.get("infer_width")
    infer_h = img_meta.get("infer_height")
    raw_region = extract_region_from_text(raw_result_text)
    result_origin_coords = []
    if raw_region:
        result_origin_coords = convert_region(
            region=raw_region,
            norm=norm_flag,
            infer_w=infer_w,
            infer_h=infer_h,
            origin_w=origin_w,
            origin_h=origin_h
        )

    # 执行转换后的精准操作
    exec_result = None
    need_screenshot = False
    print(f"action========{region}")
    if allow_actions and converted_actions:
        try:
            exec_env = await agent.system.call(core.Envelop(
                sender="builtins/agents/vision",
                receiver="builtins/agents/actions",
                payload={"actions": converted_actions, "region": region}
            ))
            if exec_env:
                exec_result = exec_env.payload
                need_screenshot = exec_result.get("need_screenshot", False)
        except Exception as e:
            exec_result = {"error": str(e)}

    envelop.payload = {
        "done": done,
        "thinking": result.get("thinking", ""),
        "result": raw_result_text,
        "error": "",
        "actions": converted_actions if allow_actions else [],
        "exec_result": exec_result,
        "need_screenshot": need_screenshot,
        "origin_image_size": f"{origin_w}*{origin_h}",
        "result_origin_coords": result_origin_coords  # 换算完成的原图像素区域，分析场景直接读取
    }
    return envelop