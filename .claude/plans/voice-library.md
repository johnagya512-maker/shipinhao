# 音色试听 + 分类音色库 + 收藏（对齐竞品 Storybound）

## 背景与差距
竞品两层设计：
1. 创建任务页：配音员一排 chips（优先显示收藏），每个带 🔊 试听，末尾「更多音色…」
2. 「更多音色」弹窗：左侧分类（收藏/解说/男声/女声/角色/方言/多情感）+ 每行音色（名字+标签+voice_id+⭐收藏+▶试听+选用）+ 顶部搜索

我们现状：5 个写死 chips、创建页不能试听、试听藏在设置页要手填 ID、无分类/搜索/收藏。

关键利好：后端 `/config/preview-tts` 已支持传任意 `{voice, speed}` 试听；client `previewTts({voice})` 已就绪。**试听能力全部现成，只差前端用起来 + 音色库数据 + 收藏存储。**

## 决策（已确认）
- 完整对齐竞品：分类音色库 + 弹窗选择器 + 搜索 + 逐个试听
- 做「收藏音色」

## 后端

### 1. 音色库数据 `app/services/voices.py`（新建）
- 内置 ~25 个火山 bigtts 音色，按分类组织。每条：`{id, name, tag, category}`。
- 分类：`narration`(解说/旁白)、`male`(通用男声)、`female`(通用女声)、`character`(角色扮演)、`dialect`(方言)、`emotion`(多情感)。
- voice_id 用火山大模型音色真实 ID（沿用代码里已验证的 `*_moon_bigtts` / `*_mars_bigtts` / `*_bigtts` 命名）。
- 导出 `VOICE_LIBRARY: list[dict]`。
- ⚠️ 诚实标注：音色可用性取决于用户火山账号授权——库是"候选清单"，试听/合成时若 E6210 则提示未授权。

### 2. 音色库接口 `app/api/config.py`
- `GET /config/voices` → 返回 `VOICE_LIBRARY`（前端渲染分类）。

### 3. 收藏存储
- `Config` 加列 `tts_favorites: JSON`（list[str]，存收藏的 voice_id）。
- `app/core/database.py` 的 `_ensure_columns` pending 加 `("configs", "tts_favorites", "JSON")`（旧库自动补列）。
- `app/api/config.py`：
  - `GET /config` 响应里带 `tts_favorites`（默认 []）。
  - `PUT /config/favorites` body `{voice_id, action: 'add'|'remove'}` → 改收藏列表，返回最新 list。
- schemas.py：ConfigOut 加 `tts_favorites: list[str] = []`。

### 4. preview-tts 已支持，不动后端合成逻辑。

## 前端

### 5. client.ts + 类型
- `getVoices()` → GET /config/voices
- `toggleFavorite(voiceId, action)` → PUT /config/favorites
- types：ConfigOut 加 `tts_favorites?: string[]`；新增 `VoiceItem` 类型。

### 6. 复用试听 hook `src/hooks/useVoicePreview.ts`（新建）
- 封装：`preview(voiceId)` → 调 `api.previewTts({voice})` 播放，管理 `previewingId` 状态（哪个在试听）、错误。
- 同一时刻只播一个；E6210 错误翻译成"该音色未授权，换一个或检查火山账号"。

### 7. 音色选择器弹窗 `src/components/VoicePicker.tsx`（新建）
- Props: `{ value, favorites, onSelect, onToggleFav, onClose }`
- 布局对齐竞品图2：
  - 顶部搜索框（按 name/tag 过滤）
  - 左侧分类列表（我的收藏 + 6 个分类，显示每类数量）
  - 右侧音色行：名字 + tag + voice_id（小字灰色）+ ⭐收藏 + ▶试听 + 选用/✓已选
  - 底部提示："试听走火山 TTS · 短句成本极低 · 收藏后下次优先显示"
- 试听用 useVoicePreview。

### 8. 创建页 `TaskCreatePage.tsx` 改造配音员区
- chips 改成：**收藏的音色优先**（取 favorites ∩ 库）+ 当前选中的（若不在收藏也显示）+「更多音色…」按钮。
- 每个 chip 加 🔊（点击试听，不选中；点 chip 主体才选中）。
- 「更多音色…」打开 VoicePicker 弹窗。
- 删除写死的 VOICES 常量，改用 getVoices() 拉取（带 loading）。
- 保留底部"可用性取决于火山授权"提示。

### 9. 设置页 `ConfigPage.tsx`（顺手统一）
- 音色输入框旁的「▶试听」保留（手填 ID 仍可用）。
- 可选：加个「从音色库选」按钮也打开 VoicePicker。（低优先，先不做，避免铺太大）

## 验证
- 后端：import 通过 + 路由注册（/config/voices、/config/favorites）；旧库补列成功；favorites 增删返回正确。
- 前端：tsc -b 通过。
- 打包：后端 PyInstaller + 前端 build 同步 desktop + electron。

## 范围控制（不做）
- 不做 MiniMax 引擎接入 / 声音克隆（竞品有，但那是另一大块，本次只做火山音色库+试听+收藏）。
- 设置页「从库选」按钮本次不做（创建页是主场景）。
- 不真去火山拉取账号已授权音色列表（火山无简单的"列我的音色"接口；用内置候选库 + 试听验证的方式，和现状/竞品一致）。
