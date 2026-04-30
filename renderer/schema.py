"""
MG 动画中间表示层 (IR Schema)

定义结构化的动画描述格式，作为 pipeline 各模块之间的契约。
LLM 输出 IR JSON → JS 播放器根据 IR 确定性渲染动画。
"""

# ── 可选值枚举 ──

ELEMENT_TYPES = ["background", "image", "text", "shape", "icon"]

ELEMENT_ANIMATIONS = [
    "none",
    "fade-in", "fade-out",
    "slide-left", "slide-right", "slide-up", "slide-down",
    "zoom-in", "zoom-out",
    "pop", "bounce",
    "rotate-in",
    "typewriter", "char-cascade",
    # MG 特色预设
    "count-up",     # 数字递增动画（0→目标值）
    "grow",         # 从 0 增长到满（适合条形图/进度条）
    "float",        # 悬浮微动（小幅上下浮动）
    # 自定义关键帧
    "custom",       # 使用 keyframes 数组定义任意动画
]

ANIMATION_DIRECTIONS = [
    "top", "bottom", "left", "right",
    "top-left", "top-right", "bottom-left", "bottom-right",
    "center",
]

SCENE_TRANSITIONS = [
    "cut",
    "crossfade",
    "slide-left", "slide-right",
    "wipe-left", "wipe-right", "wipe-up", "wipe-down",
    "zoom-through",
]

CAMERA_MOVEMENTS = [
    "none",
    "ken-burns",
    "pan-left", "pan-right", "pan-up", "pan-down",
    "zoom-in-slow", "zoom-out-slow",
]

TEXT_STYLES = ["title", "subtitle", "body", "label", "number-highlight"]

# ── 关键帧 schema ──

KEYFRAME_SCHEMA = {
    "type": "object",
    "description": "单个关键帧，定义元素在某个时间点的状态",
    "properties": {
        "time": {"type": "number", "description": "时间点（秒），相对于元素动画开始"},
        "x": {"type": "string", "description": "水平位置，如 '50%', '100px'"},
        "y": {"type": "string", "description": "垂直位置，如 '50%', '100px'"},
        "scale": {"type": "number", "description": "缩放比例，1.0 为原始大小"},
        "opacity": {"type": "number", "description": "透明度，0-1"},
        "rotate": {"type": "number", "description": "旋转角度（度）"},
    },
    "required": ["time"],
}

# ── IR JSON Schema ──

IR_ELEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "元素唯一标识，如 bg_1, img_1, title_1"},
        "type": {"type": "string", "enum": ELEMENT_TYPES},
        "src": {"type": "string", "description": "图片URL/路径（image/background类型必填）"},
        "content": {"type": "string", "description": "文字内容（text类型必填）；count-up 动画时填目标数字如 '1000'"},
        "text_style": {"type": "string", "enum": TEXT_STYLES, "description": "文字样式预设"},
        "position": {
            "type": "object",
            "properties": {
                "x": {"type": "string", "description": "水平位置，如 '50%', '100px', 'center', 'left', 'right'"},
                "y": {"type": "string", "description": "垂直位置，如 '50%', '100px', 'center', 'top', 'bottom'"},
            },
        },
        "size": {
            "type": "object",
            "properties": {
                "width": {"type": "string", "description": "宽度，如 '100%', '400px'"},
                "height": {"type": "string", "description": "高度，如 '100%', '300px'"},
            },
        },
        "animation": {
            "type": "string",
            "enum": ELEMENT_ANIMATIONS,
            "description": "元素动画类型。大部分元素用预设动画，特殊主角元素可用 'custom' 配合 keyframes",
        },
        "animation_delay": {
            "type": "number",
            "description": "动画延迟（秒），相对于场景开始时间",
        },
        "animation_duration": {
            "type": "number",
            "description": "动画持续时间（秒）",
        },
        "animation_from": {
            "type": "string",
            "enum": ANIMATION_DIRECTIONS,
            "description": "动画入场方向（可选，覆盖预设默认方向）",
        },
        "animation_intensity": {
            "type": "number",
            "description": "动画强度，1.0 为标准，>1 更夸张，<1 更柔和",
        },
        "animation_overshoot": {
            "type": "boolean",
            "description": "是否有回弹/过冲效果（适用于 pop、bounce、slide 等）",
        },
        "keyframes": {
            "type": "array",
            "items": KEYFRAME_SCHEMA,
            "description": "自定义关键帧数组（仅 animation='custom' 时使用）。每个关键帧定义一个时间点的元素状态",
        },
        "style": {
            "type": "object",
            "description": "自定义CSS样式覆盖，如 {\"color\": \"#c0392b\", \"fontSize\": \"72px\"}",
            "additionalProperties": {"type": "string"},
        },
    },
    "required": ["id", "type"],
}

IR_SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "scene_id": {"type": "integer"},
        "start_time": {"type": "number"},
        "end_time": {"type": "number"},
        "subtitle": {"type": "string", "description": "该场景的口播字幕"},
        "camera": {
            "type": "string",
            "enum": CAMERA_MOVEMENTS,
            "description": "镜头运动效果",
        },
        "transition_to_next": {
            "type": "string",
            "enum": SCENE_TRANSITIONS,
            "description": "切换到下一个场景的转场方式",
        },
        "elements": {
            "type": "array",
            "description": "场景内的元素列表，按层级从底到顶排列",
            "items": IR_ELEMENT_SCHEMA,
        },
    },
    "required": ["scene_id", "start_time", "end_time", "subtitle", "elements"],
}

IR_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "style": {
            "type": "object",
            "description": "全局视觉风格",
            "properties": {
                "palette": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "主色板，如 ['#f5f0e8', '#c0392b', '#d4a017']",
                },
                "font_family": {"type": "string", "description": "主字体"},
                "background_color": {"type": "string"},
                "mood": {"type": "string", "description": "整体氛围，如 '科技感', '温馨', '新闻调查'"},
            },
        },
        "total_duration": {"type": "number"},
        "audio_path": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": IR_SCENE_SCHEMA,
        },
    },
    "required": ["title", "total_duration", "scenes"],
}


# ── MG 风格示例 IR ──

IR_EXAMPLE = {
    "title": "小龙虾产业调查",
    "style": {
        "palette": ["#1a1a2e", "#e94560", "#f5a623", "#f0f0f0"],
        "font_family": "Microsoft YaHei",
        "background_color": "#1a1a2e",
        "mood": "活力科普",
    },
    "total_duration": 30,
    "audio_path": None,
    "scenes": [
        {
            "scene_id": 1,
            "start_time": 0,
            "end_time": 15,
            "subtitle": "小龙虾，这个夏天最火爆的美食，到底有多受欢迎？",
            "camera": "ken-burns",
            "transition_to_next": "crossfade",
            "elements": [
                {
                    "id": "bg_1", "type": "background",
                    "src": "/assets/dark_gradient_bg.png",
                    "position": {"x": "0", "y": "0"},
                    "size": {"width": "100%", "height": "100%"},
                    "animation": "fade-in", "animation_delay": 0, "animation_duration": 0.5,
                },
                {
                    "id": "lobster_1", "type": "image",
                    "src": "/assets/lobster.png",
                    "position": {"x": "60%", "y": "50%"},
                    "size": {"width": "420px", "height": "420px"},
                    "animation": "custom", "animation_delay": 0.2, "animation_duration": 1.0,
                    "keyframes": [
                        {"time": 0, "y": "130%", "scale": 0.3, "opacity": 0, "rotate": -15},
                        {"time": 0.4, "y": "35%", "scale": 1.2, "opacity": 1, "rotate": 5},
                        {"time": 0.6, "y": "50%", "scale": 0.95, "rotate": -3},
                        {"time": 0.8, "y": "50%", "scale": 1.0, "rotate": 0},
                    ],
                },
                {
                    "id": "title_1", "type": "text",
                    "content": "小龙虾有多火？", "text_style": "title",
                    "position": {"x": "12%", "y": "25%"},
                    "animation": "char-cascade", "animation_delay": 0.6, "animation_duration": 0.8,
                    "style": {"color": "#e94560", "fontSize": "56px"},
                },
                {
                    "id": "deco_1", "type": "shape",
                    "position": {"x": "8%", "y": "45%"},
                    "size": {"width": "6px", "height": "80px"},
                    "animation": "grow", "animation_from": "top",
                    "animation_delay": 0.4, "animation_duration": 0.5,
                    "style": {"backgroundColor": "#f5a623", "borderRadius": "3px"},
                },
            ],
        },
        {
            "scene_id": 2,
            "start_time": 15,
            "end_time": 30,
            "subtitle": "2024年全国小龙虾产量突破300万吨，产值超过4000亿元",
            "camera": "zoom-in-slow",
            "transition_to_next": "wipe-left",
            "elements": [
                {
                    "id": "bg_2", "type": "background",
                    "position": {"x": "0", "y": "0"},
                    "size": {"width": "100%", "height": "100%"},
                    "animation": "fade-in", "animation_delay": 0, "animation_duration": 0.3,
                    "style": {"backgroundColor": "#1a1a2e"},
                },
                {
                    "id": "number_tons", "type": "text",
                    "content": "300", "text_style": "number-highlight",
                    "position": {"x": "18%", "y": "30%"},
                    "animation": "count-up", "animation_delay": 0.3, "animation_duration": 1.5,
                    "style": {"color": "#f5a623", "fontSize": "108px"},
                },
                {
                    "id": "label_tons", "type": "text",
                    "content": "万吨产量", "text_style": "label",
                    "position": {"x": "18%", "y": "56%"},
                    "animation": "slide-up", "animation_delay": 0.8, "animation_duration": 0.4,
                    "style": {"color": "#f0f0f0", "fontSize": "24px"},
                },
                {
                    "id": "bar_chart", "type": "shape",
                    "position": {"x": "60%", "y": "70%"},
                    "size": {"width": "35%", "height": "120px"},
                    "animation": "grow", "animation_from": "left",
                    "animation_delay": 1.2, "animation_duration": 1.0,
                    "style": {"backgroundColor": "#e94560", "borderRadius": "4px"},
                },
                {
                    "id": "number_value", "type": "text",
                    "content": "4000亿", "text_style": "number-highlight",
                    "position": {"x": "62%", "y": "35%"},
                    "animation": "pop", "animation_delay": 1.5, "animation_duration": 0.5,
                    "animation_overshoot": True,
                    "style": {"color": "#e94560", "fontSize": "64px"},
                },
            ],
        },
    ],
}
