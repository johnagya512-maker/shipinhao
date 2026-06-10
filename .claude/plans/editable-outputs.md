# 产物可编辑（对齐竞品「编辑/重跑」双轨）

## 背景与差距
竞品 v0.8.0 核心设计：每个已完成步骤的产物**可直接编辑字段**，
「编辑」= 改字段、不调 AI、不扣费；「重跑」= 调 AI、重新生成。两条路分开，用户按需选。

我们现状：
- 能改：标题、分镜 cap/desc_prompt（画廊）
- 只读：清洗稿(A)、改写稿(B)、分句(F)、图书识别(D)、人物特征(CP) —— ProductPreview 纯展示

差距 = 最关键的口播正文(B)、清洗稿(A)、分句(F) 等文本产物都不能手动改字。

## 决策（已和用户确认）
- 范围：**所有文本产物**可编辑
- 下游：**编辑与重跑分开** —— 编辑只改当前文字、不调 AI、不动下游；要重算下游用已有的「从此步重跑」

## 后端

### 1. 通用产物编辑接口 `app/api/tasks.py`
`PATCH /tasks/{task_id}/modules/{module}/output`
- body: `{ fields: {...} }`，按模块白名单只接收可编辑字段，杜绝任意写入：
  - A → `cleaned_text` (str)
  - B → `script` (str)
  - F → `segments` (list[str] 或 list[{text}]) + 自动同步 `segment_count`
  - D → `title` / `author` / `category` (str)
  - CP → `profile` (str)
  - SB/P 已有 `PATCH /scenes`，不在此处重复
- 逻辑：取该 module 的 ModuleResult，merge 白名单字段进 output，`db.commit()`。
  不改 status、不触发任何下游、不调度任务。
- 校验：module 不在白名单 → 400；任务/产物不存在 → 404；字段类型不符 → 400。
- F 编辑要同步：`segment_count = len(segments)`；segments 统一存成 `[{text, ...}]` 结构（保留原有其它键）。

### 2. 复用安全
- 与单图重试的 per-task 锁同理：编辑是整段覆盖单个 module.output，
  非读-改-写数组，冲突面小；但仍包在 `_get_retry_lock(task_id)` 内 commit，
  避免和并发的单图重试/重跑同时写库。

## 前端

### 3. `ProductPreview.tsx` 每张卡片加「编辑」
- 卡片右上角（status 徽标旁）加「编辑」按钮（仅 status=success 且模块可编辑时显示）。
- 点「编辑」→ 当前卡片内容区切换为可编辑：
  - A/B/CP（长文本）→ `<textarea>` 占满，自动高度
  - D（图书）→ 书名/作者/分类 三个 `<input>`
  - F（分句）→ 每句一行 `<input>`，支持删行/加行（竞品同款逐句编辑）
- 底部出现「保存」「取消」。保存调接口 → 成功后 `onChanged()` 刷新，退出编辑态。
- 「编辑」与左栏 StepTimeline 的「重跑」文案上区分清楚：
  卡片里加一行浅色提示「直接改字 · 不重新生成 · 不计费」。

### 4. `client.ts` + 类型
- 加 `updateModuleOutput(taskId, module, fields)` → PATCH 接口。

### 5. 可编辑模块映射
前端维护 `EDITABLE = { A:'text', B:'text', F:'segments', D:'book', CP:'text' }`，
决定按钮是否出现、用哪种编辑 UI。

## 验证
- 后端：import 通过 + 路由注册；用脚本对 A/B/F 各 PATCH 一次，确认 output 改了、status 没变、下游 ModuleResult 没动。
- 前端：tsc -b 通过。
- 打包：后端 PyInstaller + 前端 build 同步 desktop + electron。

## 不做（避免过度）
- 不做「编辑后自动标记下游失效」——用户已选「编辑与重跑分开」，保持简单。
- 不动 SB/P（画廊已能编辑）。
- 不碰 G（成片）/E（图，画廊管）。
