# 草稿模板系统（动画/转场/字幕样式预设 + 镜头随机组合）

## 背景与差距
现状 [jianying.py](../../backend/app/modules/jianying.py)：
- 只有两个开关 `enable_subtitles` / `enable_animations`（布尔）。
- 动画写死：所有镜头都加同一个 `IntroType.放大`（`_try_add_zoom`）→ 单调。
- 无转场（TransitionType 没用）、字幕用默认样式（TextSegment 不传 style）。

剪映库实际支持：入场 153 / 出场 122 / 转场 451 种 + 文字样式（font/style/border/background/shadow）。能力全在，没用上。

用户诉求：要「完整草稿模板系统」——把动画+转场+字幕样式打包成几套预设，创建任务时选一套，整片按该风格出；每套内部对每镜头随机/轮换挑动画，避免雷同。

## 决策（已确认）
- 做完整模板系统，不是单纯随机。
- 模板内含「随机混搭」选项，覆盖纯随机需求。
- 现状（全用放大）保留为「经典」模板，"关闭"保留为无动画。

## 模板定义（已用 pyJianYingDraft 实测动画名存在性）

5 个预设（draft_templates.py，纯数据 + 选择函数）：

| key | 名称 | 入场池(随机轮换) | 转场 | 字幕样式 | 适用 |
|---|---|---|---|---|---|
| `none` | 关闭 | — | — | 默认 | 不要动效 |
| `classic` | 经典(现状) | 放大 | 无 | 默认 | 兼容老行为 |
| `narration` | 沉稳叙事 | 放大/缩小/轻微放大 | 叠化 | 白字+黑描边 | 讲书/解说 |
| `lively` | 活泼带货 | 放大/向上滑动/向下滑动/镜像翻转 | 闪黑/推近 | 大字+描边 | 带货/种草 |
| `cinematic` | 电影感 | 镜像翻转/旋转/轻微放大 | 叠化/拉远 | 居中 | 故事/情感 |
| `random` | 随机混搭 | 上述全池 | 随机 | 默认 | 要变化 |

> 实测可用：放大/缩小/轻微放大/向上滑动/向下滑动/镜像翻转/渐显/旋转；叠化/闪黑/推近/拉远/信号故障/色彩溶解。实现时若个别名对不上以库内实际枚举为准（用 try/except 跳过，不阻断——沿用 `_try_add_zoom` 容错风格）。

### 镜头序列稳定性（关键）
- 每镜头从模板入场池里选动画：用 **任务 id 派生的随机种子** → `random.Random(seed)`，
  序列确定可复现。重新生成草稿不会换一套动画。
- 轮换 vs 随机：池子按 `Random(seed).choice` 逐镜头选；同一 task 同一结果。

## 后端

### 1. 新文件 `app/modules/draft_templates.py`
- `TEMPLATES: dict[str, dict]`：每个含 `name / intro_pool(list[str]) / transition(list[str]|None) / subtitle_style(dict|None)`。
- `pick_intro(template_key, seed, index) -> str|None`：按种子+下标选入场动画名。
- `pick_transition(template_key, seed, index) -> str|None`。
- `subtitle_style(template_key) -> dict|None`：返回 TextStyle/clip 参数。
- 全部返回「名字字符串」，由 jianying.py 用 `getattr(IntroType, name)` 取枚举，取不到就跳过（容错）。

### 2. `jianying.py` 改造
- `_populate(...)` 和 `build_draft(...)` 新增参数 `template: str = "classic"`、`seed: int`（默认从 draft_name/task 派生）。
- 图片轨循环里：
  - `enable_animations` 为真且 template != none → 用 `draft_templates.pick_intro` 取动画名，`getattr` 取枚举加上去（替换写死的放大）。
  - 相邻片段之间按 `pick_transition` 加转场（剪映转场加在前一段的尾部，调 `seg.add_transition(TransitionType.x)`；实测 API 名以库为准，容错跳过）。
- 字幕轨：`subtitle_style` 非空时给 `TextSegment` 传 `style=TextStyle(...)`、描边 `border=`。空则维持默认。
- 保留 `_try_add_zoom` 作为 classic 的实现（或并入新逻辑）。

### 3. 数据流串接
- `app/models.py` Task 加列 `draft_template: str default 'classic'`。
- `app/core/database.py` `_ensure_columns` pending 加 `("tasks","draft_template","VARCHAR(20) DEFAULT 'classic'")`（旧库补列）。
- `app/api/schemas.py` TaskCreate 加 `draft_template: str = 'classic'`；TaskOut 同步带出。
- `app/api/tasks.py` create_task：把 `body.draft_template` 存进 Task。
- `app/services/orchestrator.py` 调 compose/jianying 时把 `task.draft_template` 和 `seed=hash(task.id)` 透传。
- `app/services/compose.py` `build_jianying(...)`、`compose_video(...)` 透传 template/seed 到 `jianying.build_draft`。
  - 注意 compose 还有 mp4 合成路径（moviepy），本期 **mp4 路径暂不做模板动画**（moviepy 实现动画成本高），仅剪映草稿路径生效；mp4 仍用现有简单效果。计划里 log/注释说明此边界。

### 4. 模板列表接口（前端渲染用）
- `app/api/config.py` 或 tasks.py 加 `GET /draft-templates` → 返回 `[{key,name,desc}]`。
  （也可前端写死，但后端给更一致；倾向后端给。）

## 前端

### 5. 创建页 `TaskCreatePage.tsx`
- 高级选项里「字幕动效」区块：把现在的「动效」单复选框升级为 **模板选择器**（一排卡片/下拉）。
  - 拉 `GET /draft-templates` 渲染 5 个选项（含关闭）。
  - 选中存 `form.draft_template`。
  - 「字幕」复选框保留（字幕开关独立于模板）。
- 兼容：`enable_animations` 语义保留——选「关闭」等价 animations=false；其余模板 animations=true。
  实现上可让前端选模板时同步设 `enable_animations`，后端以 template 为准。

### 6. 类型 `types.ts`
- TaskCreate 加 `draft_template?: string`。
- 新增 `DraftTemplate {key,name,desc}` 类型 + `client.getDraftTemplates()`。

## 验证
- 后端：import 通过；旧库补列；`GET /draft-templates` 返回 5 项；
  脚本生成一个草稿，断言不同 template 产出不同动画序列、同 template+同 seed 序列一致。
- 前端：tsc -b 通过。
- 端到端：实际跑一条任务到 G，打开剪映草稿确认动画/转场/字幕样式生效。
- 打包：后端 PyInstaller + 前端 build 同步 desktop + 重建安装包。

## 范围控制（不做）
- mp4(moviepy) 合成路径的模板动画——只做剪映草稿路径（草稿是主场景）。
- 出场动画(OutroType)、组合动画(GroupAnimationType)——本期只做入场+转场+字幕样式，够覆盖"模板感"；出场可二期。
- 自定义模板编辑器（用户自己拖动画）——本期只给预设，不做可视化编辑。
- BGM/音效模板——本期聚焦视觉。

## 待确认
- 字幕样式细节（字号/颜色/位置）按竞品截图风格定，实现时若 TextStyle 参数对不上以库为准。
- 模板数量：先 5 个（含关闭/经典）。后续可加。
