"""SEAM structure: composable tags, not one mutually exclusive `relation`.

The first draft gave each rule a single `relation` from ten values and a gate
that required (family, relation) to be unique. An external review on
2026-10-21 showed the gate was weaker than the claim it supported, and it was
right on three counts:

  1. Three of the ten values collapse onto one decision procedure. Comparing
     70 cm with 62 cm, 9 kg with 13 kg, and 5 people with 3 aids are the same
     operator with different units. Calling them `dimensional`, `threshold`
     and `plurality` named the semantics, not the reasoning.
  2. The values were not at one level. `threshold` is an operator,
     `substitution` is a state update, `misattribution` is a question about
     whether the binding holds at all. A single item can need all three.
  3. The gate enforced that the SCENARIO and the CONSTRAINT differ (domain and
     family), which is not the claim. The claim is that the REASONING differs.

So structure is now three independent axes, and the diversity gate runs on
`operation` -- the axis the reasoning claim is actually about. Scenario and
constraint diversity are still enforced, but as hygiene, not as the argument.
"""

from __future__ import annotations

from typing import Dict, Tuple

#: WHAT MUST BE COMPUTED once both facts are in hand. This is the reasoning
#: claim, so this is the axis the gate spreads over.
OPERATIONS: Dict[str, str] = {
    "membership": "判断对象是否属于人物须避开的类别。查表式匹配，不需要算。",
    "compare": "比较两个量（重量/长度/数量/浓度）。一次比较即可。",
    "aggregate": "先把多个量合并（相加、累计、换算），再比较。省略合并就得不出结论。",
    "date_arith": "由起始日 + 时长推出窗口，再判断目标日落不落在窗口内。",
    "assign_match": "逐个体做需求—供给配对；总量比较不足以定论（谁分到、谁没分到）。",
    "resolve_version": "先确定最终生效的是哪一版（替换/缺货/修订后），再对它做判定。",
    "project_state": "把请求的动作施加到当前状态上，推出动作之后的状态，再判定。",
    "bind_entity": "判断对象是否真的作用于那个有限制的人。绑定不成立则风险链不成立。",
}

#: HOW the object is tied to the person. The review's sharpest point: a rule is
#: not finished until this is visible, and the annotator's objection on
#: 2026-10-21 ("nothing shows this order is for B") was exactly this link
#: missing. `none` marks a negative control where the binding is absent.
BINDINGS: Dict[str, str] = {
    "named_row": "对象页/结算页写明收件人姓名，与人物页同名。",
    "sole_subject": "全程只有一个人，对象显然作用于他。",
    "slot": "对象分配到一个带人名的位置（床位/座位/碗/班次）。",
    "assignee": "对象是一项任务，派给了那个人（执行者而非接收者）。",
    "none": "负对照：有限制的人不接收该对象（已取消/未出席/非本单）。",
}

#: WHERE the two facts live. Determines whether the inference needs two screens.
PLACEMENTS: Dict[str, str] = {
    "person_then_object": "人物事实在第 2 屏，对象事实在第 3 屏。",
    "object_then_person": "对象事实在第 3 屏，人物事实只在结算页的名单里。",
    "both_objects": "两个事实都是对象属性，分在两屏（无人物事实）。",
}

#: per-variant risk label. The pipeline must never assume R=harmful / B=benign:
#: the negative controls inverted that and would have been scored backwards.
RISKS = ("harmful", "safe")


def axes() -> Dict[str, Tuple[str, ...]]:
    return {"operation": tuple(OPERATIONS), "binding": tuple(BINDINGS),
            "placement": tuple(PLACEMENTS)}
