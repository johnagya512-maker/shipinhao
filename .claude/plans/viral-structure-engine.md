# 爆款文案结构拆解二创引擎

## 用户需求
两个入口(抖音链接提取 / 粘贴文案)做二创,**重点是"拆解爆款文案结构"**:不是换个说法重写,而是学原文的骨架(钩子/铺垫/转折/高潮/收尾)再填新内容。
确认:**两种用法都要**——
- 同题材深度二创:拆原文结构 → 重写**同一题材**
- 拆A结构套B主题:把爆款当"结构模板",换题材批量仿写

## 现状诊断(已查证)
- 数据流:`A清洗 → B改写 → H合规 → F分段 → E配图 → G成片`(orchestrator.py:41-51)
- B改写(text_modules.py:62-91)按 track 选 3 套提示词(MODULE_B_CHARACTER/MODULE_B/MODULE_B_GENERAL),
  **每套是写死的固定套路**(开头钩子→中间→结尾),**不分析原文结构**。
- processing_mode: full_auto 才跑 B;semi_auto/direct 跳过改写。
- 入口:douyin_url→采集+ASR→A;transcript→A;keyword 可选参考。前端两 tab 已就绪。
- **缺口**:没有"先拆解原爆款结构、再按结构指导改写"这一层。

## 方案:在 A 和 B 之间插入「结构拆解」步骤(新模块 S2 / structure)

### 核心思路
1. **拆解**:新增一步,LLM 读清洗后的爆款原文,输出结构化骨架 JSON:
   - hook(开头钩子:类型+作用)、segments(中段几个叙事单元:每段的功能/情绪/节奏)、
     turn(转折点)、climax(高潮)、ending(收尾方式)、overall(整体节奏/时长/口吻特征)
2. **改写**:B 改写时,把这个骨架作为「结构指导」注入提示词 ——
   "请严格按以下爆款结构骨架来组织你的文案:{骨架}",从而让新文案复刻原爆款的节奏。
3. **两种用法靠一个参数 `creation_mode` 区分**:
   - `same_topic`(同题材二创):拆 transcript 的结构 → 用同一 transcript 内容按结构重写
   - `template_topic`(套模板):拆 transcript(爆款样板)的结构 → 改写时内容主题换成 keyword/新主题

### 后端改动

**1. prompts.py 新增两段提示词**
- `MODULE_STRUCTURE`:拆解爆款结构 → 输出 JSON 骨架(用 schema 约束字段)。
  强调"分析它为什么爆:钩子怎么抓人、节奏怎么铺、情绪怎么递进、怎么收尾留钩"。
- 改造现有 MODULE_B_*:加一个可选占位 `{structure_guide}`,非空时插入"按此结构骨架组织"。

**2. text_modules.py 新增 `run_structure(...)`**
- 调 LLM 拆解,返回 `{structure: {...骨架...}}`。用 _render + call_llm,模式同 run_clean。
- run_rewrite 增参 `structure_guide: dict|None`,非空时渲染进 B 提示词。

**3. orchestrator.py 插入拆解步骤**
- A 清洗后、B 改写前,若开启结构拆解则跑 run_structure,把骨架存进 ModuleResult(module="S2"),
  并传给 run_rewrite。
- 仅 full_auto(要改写)时才拆解;semi_auto/direct 跳过(它们本就不改写)。
- template_topic 模式:拆解的是 transcript(样板爆款),但改写的目标主题用 keyword/另一字段。
  → 需要一个独立字段存"样板爆款文案",见数据模型。

**4. 数据模型(models.py + database.py 补列)**
- Task 加 `creation_mode VARCHAR(16) DEFAULT 'same_topic'`(same_topic/template_topic/none)
- template_topic 模式需要"样板文案"和"新主题"两个输入:
  - 复用 transcript 存样板爆款文案;新主题用 keyword(已有)或加 `new_topic`。
  - 简化:transcript=要拆解的爆款,keyword/title=新主题方向。先不加多余字段,复用现有。
- 可选:把拆解出的结构骨架持久化到 ModuleResult(已有表),前端可展示"这条爆款的结构"。

**5. schemas.py / tasks.py**
- TaskCreate 加 `creation_mode`。create_task 存入。任务详情带出 structure 结果。

**6. API:结构预览(可选增强)**
- `POST /tasks/analyze-structure`:输入一段文案,同步返回结构骨架 JSON。
  让用户**在创建任务前**就能看到"这条爆款拆出来是什么结构",所见即所得。
  (本期可做,体验提升大;若想精简可二期。倾向做。)

### 前端改动

**7. 创建页**
- "粘贴文案"/"贴抖音链接"输入区下方,加「二创方式」选择:
  - 同题材深度二创(默认)
  - 套用爆款结构(此时提示:上方填爆款样板,关键词填你的新主题)
  - 直接改写(不拆结构,=现状)
- (可选)"分析结构"按钮:调 analyze-structure,把骨架以卡片展示(钩子/转折/高潮/收尾),
  让用户看到拆解结果,确认后再生成。这是"重点拆解结构"最直观的体现。
- types.ts + client.ts 加 creation_mode、analyzeStructure。

### 结构骨架 JSON schema(拆解输出)
```
{
  "why_viral": "一句话:这条为什么爆",
  "hook": {"type": "悬念/反差/疑问/数字", "text": "原文钩子句", "function": "作用"},
  "structure": [
    {"part": "铺垫/转折/高潮/收尾", "function": "这段干什么", "emotion": "情绪", "pace": "快/慢"}
  ],
  "ending": {"type": "互动引导/带货/开放问题", "text": "原文收尾"},
  "rhythm": "整体节奏特征",
  "duration_hint": "适合时长"
}
```

## 验证
- 后端:import;run_structure 拆解真实爆款返回合理骨架;run_rewrite 注入骨架后产出文案
  确实贴合骨架(对比 same_topic 有/无骨架的差异)。
- analyze-structure 接口返回结构 JSON。
- 前端 tsc。
- 端到端:粘贴一条真实爆款 → 看拆解结构 → 生成 → 对比新文案是否复刻了结构。
- 打包同步桌面端。

## 范围控制(不做)
- keyword→纯AI从零生成(那个"敬请期待"入口):本期只做"拆解已有爆款",不做无中生有。
- 多爆款融合(拆N条取共性):二期。
- 结构骨架的可视化编辑(用户手改骨架再生成):二期,先只读展示。

## 待确认(plan 内已决策的默认)
- template_topic 模式的"新主题"复用 keyword 字段(不加新字段),够用。
- analyze-structure 预览接口:做(体验核心)。
- 拆解默认开(same_topic),用户可切"直接改写"回退现状。
