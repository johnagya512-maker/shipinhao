# 配图预设：豆包 / gpt 两套各存各的，下拉一键切换

## Context（为什么做）
现在配置只有一条记录、覆盖式保存（见 [config.py:117-120](backend/app/api/config.py#L117-L120)）。
`image_model` / `image_base_url` / `image_unit_price` 是「传了就覆盖」，所以从豆包切到
gpt 会把豆包那几项盖掉，切回来得手动重填。用户要的是两套都存着、一键切换、互不覆盖。

已确认：gpt 与豆包**用同一个兔子 API key**，所以预设**不含 key**（key 仍是全局那一份，
切换时一个字不动）。这让设计大幅简化。

## 核心思路（后端核心逻辑零改动）
保留现有 `image_*` 字段当「**当前生效配置**」——编排层 [orchestrator.py:445-461](backend/app/services/orchestrator.py#L445-L461)
和成本层照旧读 `cfg.image_model` / `cfg.image_base_url` / `cfg.image_unit_price`，**完全不用改**。
另加一个 JSON 列存「**预设快照**」。
- **切换预设** = 把某预设的 3 个值拷进当前 `image_*` 字段
- **存预设** = 把当前 3 个值拷成一份快照存进 JSON

## 改动清单

### 1. 数据库（models.py + database.py）
- [models.py](backend/app/models.py) Config 加一列：
  `image_presets: Mapped[list | None] = mapped_column(JSON, nullable=True)`
  每个预设形如 `{"name":"豆包","model":"...","base_url":"...","unit_price":0.25}`
- [database.py](backend/app/core/database.py#L74) `_ensure_columns` 的 pending 列表加一行：
  `("configs", "image_presets", "JSON")`（老库平滑补列，沿用现有机制）

### 2. 配置 API（api/config.py + api/schemas.py）
- schemas 的 ConfigUpdate 加可选字段 `image_presets`；ConfigOut 回显 `image_presets`
- [config.py](backend/app/api/config.py) update 里加 `if body.image_presets is not None: cfg.image_presets = body.image_presets`
- 不新增专门的「切换」接口：切换在前台做（把预设值塞进 image_model/base_url/unit_price
  三个字段一起 PATCH），复用现有 updateConfig，后端无需新逻辑

### 3. 前台（ConfigPage.tsx + api/types.ts）
在「配图模型」卡片顶部加一行预设区：
- 下拉选择已存预设 → 选中即把该预设的 model/base_url/unit_price 三项一起 saveField，
  并刷新输入框显示
- 「存为预设」按钮 → 弹个名字输入，把当前三项打包 append 进 image_presets 保存
- 「删除」按钮 → 从 image_presets 移除
- types.ts 的 ConfigOut/ConfigUpdate 加 `image_presets?: ImagePreset[]`

### 4. 内置两个默认预设（可选，体验更好）
首次加载若 image_presets 为空，前台展示两个内置模板供「应用」：
- 豆包：model=doubao-seedream-4-5-251128, base_url=空/官方, unit_price=0.25
- gpt：model=gpt-image-2, base_url=兔子API地址, unit_price=0.058
（仅作填充模板，用户应用后才落库；不硬塞进数据库）

## 不改的部分（已确认）
- orchestrator.py / cost.py / image.py：读的还是 cfg.image_* 当前值，零改动
  （gpt 协议分支已在上一步加好）
- API key：全局一份，切换不动

## 验证
- 跑 `backend/tests/test_core.py` 确认配置读写没坏
- 前台：存一个「豆包」预设、存一个「gpt」预设，来回切，确认三项随之变、key 不变
- 切到 gpt 后建个任务出图，确认走的是 gpt 协议
