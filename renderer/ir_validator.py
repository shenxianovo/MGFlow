"""
IR quality validator — detects PPT-like anti-patterns in MG animation IR.
"""

import re


def validate_ir(scenes: list, style: dict = None) -> tuple[list[str], bool]:
    """Returns (issues, should_block). should_block=True for egregious PPT patterns."""
    if not scenes:
        return (["scenes 为空"], True)

    issues = []
    issues.extend(_check_center_clustering(scenes))
    issues.extend(_check_animation_monotony(scenes))
    issues.extend(_check_delay_progression(scenes))
    issues.extend(_check_layout_repetition(scenes))
    issues.extend(_check_transition_diversity(scenes))
    issues.extend(_check_camera_usage(scenes))

    blocking = any(i.startswith("[BLOCK]") for i in issues)
    return issues, blocking


def _parse_position_pct(val: str | None) -> float | None:
    if val is None:
        return None
    if val == "center":
        return 50.0
    m = re.match(r"^([\d.]+)%$", str(val).strip())
    return float(m.group(1)) if m else None


def _non_bg_elements(scenes: list):
    for scene in scenes:
        for el in scene.get("elements", []):
            if el.get("type") != "background":
                yield el


def _check_center_clustering(scenes: list) -> list[str]:
    total = 0
    centered = 0
    for el in _non_bg_elements(scenes):
        pos = el.get("position", {})
        x = _parse_position_pct(pos.get("x"))
        y = _parse_position_pct(pos.get("y"))
        if x is None or y is None:
            continue
        total += 1
        if 45 <= x <= 55 and 45 <= y <= 55:
            centered += 1

    if total >= 3 and centered / total > 0.7:
        return [f"[BLOCK] 居中聚集：{centered}/{total} 个非背景元素位于画面中心区域(45%-55%)，缺乏构图变化"]
    return []


def _check_animation_monotony(scenes: list) -> list[str]:
    issues = []
    all_anims = []

    for scene in scenes:
        scene_anims = set()
        for el in scene.get("elements", []):
            if el.get("type") == "background":
                continue
            anim = el.get("animation", "none")
            scene_anims.add(anim)
            all_anims.append(anim)
        if len(scene_anims) == 1 and len(scene.get("elements", [])) > 2:
            issues.append(f"[BLOCK] 动画单调：场景 {scene.get('scene_id')} 所有非背景元素使用相同动画 '{scene_anims.pop()}'")

    if all_anims:
        fade_count = sum(1 for a in all_anims if a == "fade-in")
        if len(all_anims) >= 4 and fade_count / len(all_anims) > 0.6:
            issues.append(f"[BLOCK] 全局动画单调：{fade_count}/{len(all_anims)} 个元素使用 fade-in，缺乏动画多样性")

    return issues


def _check_delay_progression(scenes: list) -> list[str]:
    issues = []
    for scene in scenes:
        elements = scene.get("elements", [])
        non_bg = [el for el in elements if el.get("type") != "background"]
        if len(non_bg) < 2:
            continue
        delays = [el.get("animation_delay", 0) for el in non_bg]
        if len(set(delays)) == 1:
            issues.append(f"场景 {scene.get('scene_id')} 所有元素 animation_delay 相同({delays[0]}s)，缺乏出现节奏")
    return issues


def _check_layout_repetition(scenes: list) -> list[str]:
    if len(scenes) < 3:
        return []

    def fingerprint(scene):
        elements = scene.get("elements", [])
        non_bg = [el for el in elements if el.get("type") != "background"]
        positions = []
        for el in non_bg:
            pos = el.get("position", {})
            x = _parse_position_pct(pos.get("x"))
            y = _parse_position_pct(pos.get("y"))
            if x is not None and y is not None:
                qx = "L" if x < 40 else ("R" if x > 60 else "C")
                qy = "T" if y < 40 else ("B" if y > 60 else "M")
                positions.append(f"{qx}{qy}")
        positions.sort()
        return f"{len(non_bg)}|{'_'.join(positions)}"

    fps = [fingerprint(s) for s in scenes]
    from collections import Counter
    counts = Counter(fps)
    most_common_count = counts.most_common(1)[0][1]
    if most_common_count / len(scenes) > 0.5:
        return [f"布局重复：{most_common_count}/{len(scenes)} 个场景使用相同的元素位置布局"]
    return []


def _check_transition_diversity(scenes: list) -> list[str]:
    if len(scenes) < 3:
        return []
    transitions = [s.get("transition_to_next", "cut") for s in scenes[:-1]]
    if len(set(transitions)) == 1:
        return [f"转场单调：所有场景转场均为 '{transitions[0]}'，建议交替使用不同转场"]
    return []


def _check_camera_usage(scenes: list) -> list[str]:
    if len(scenes) < 3:
        return []
    cameras = [s.get("camera", "none") for s in scenes]
    active = sum(1 for c in cameras if c != "none")
    if active / len(scenes) < 0.3:
        return [f"镜头运动不足：仅 {active}/{len(scenes)} 个场景使用镜头运动，建议至少 30% 场景有镜头效果"]
    return []
