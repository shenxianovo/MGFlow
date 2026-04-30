from core.node import Node, node


@node(
    name="render",
    depends_on=["motion_design", "sound_design"],
    tools=[],
    max_iterations=1,
    deterministic=True,
)
class Render(Node):
    system_prompt = ""
