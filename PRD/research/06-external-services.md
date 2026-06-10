# 06 外部服务事实清单（LLM / TTS / Voices / ASR / Collect）

> 软件考古：逐行读完 5 个服务文件后的真实事实记录。每条结论附 `文件:行号` 证据。
> 仅记录代码中真实存在的内容，未实现/占位项已注明。

---

## 一、LLM 服务 `backend/app/services/llm.py`（共 107 行）

### 1.1 模块定位
- 文件 docstring 自述：「LLM 客户端。统一 OpenAI 兼容接口，支持 deepseek/openai/qwen/doubao。」(`llm.py:1`)
- 依赖：`httpx`、`dataclasses.dataclass`(`llm.py:2-3`)

### 1.2 支持的供应商与端点（`LLM_ENDPOINTS`，`llm.py:6-11`）
代码中**真实存在的供应商共 4 个**，全部走 OpenAI 兼容 `/chat/completions` 协议：

| provider | base_url 端点 | 证据 |
|----------|--------------|------|
| `deepseek` | `https://api.deepseek.com/v1/chat/completions` | `llm.py:7` |
| `openai` | `https://api.openai.com/v1/chat/completions` | `llm.py:8` |
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` | `llm.py:9` |
| `doubao` | `https://ark.cn-beijing.volces.com/api/v3/chat/completions`（火山方舟 ARK） | `llm.py:10` |

> 任务清单中提到的 claude / moonshot / glm / minimax 等供应商在本文件中**均不存在**。无 Claude 原生协议、无独立 base_url、无代理(proxy)相关代码。

### 1.3 数据结构与异常
- `LLMResult` 数据类：字段 `text: str`、`tokens_in: int`、`tokens_out: int`(`llm.py:14-18`)
- `LLMError(Exception)`：可重试错误，构造参数 `message`、`retryable: bool = True`，实例属性 `self.retryable`(`llm.py:21-25`)

### 1.4 文本调用 `call_llm(...)`（`llm.py:28-63`）
- 签名：`call_llm(provider, model, api_key, prompt, timeout=30.0)`(`llm.py:28-29`)
- 端点查表，未知供应商抛 `LLMError(f"未知 LLM 供应商: {provider}", retryable=False)`(`llm.py:31-33`)
- 请求头：`Authorization: Bearer {api_key}`、`Content-Type: application/json`(`llm.py:34`)
- 请求体：`model`、`messages=[{"role":"user","content":prompt}]`、`temperature: 0.7`(`llm.py:35-39`)
- 同步调用 `httpx.post(...)`(`llm.py:41`)
- 异常映射：
  - `TimeoutException` → `LLMError("LLM 超时", retryable=True)`(`llm.py:42-43`)
  - `RequestError` → `LLMError("LLM 请求错误", retryable=True)`(`llm.py:44-45`)
  - HTTP 401 → `LLMError("API Key 无效", retryable=False)`(`llm.py:47-48`)
  - HTTP 429 → `LLMError("触发限流", retryable=True)`(`llm.py:49-50`)
  - HTTP ≥500 → `LLMError("LLM 服务端错误 {code}", retryable=True)`(`llm.py:51-52`)
  - HTTP ≥400 → `LLMError("LLM 请求被拒 {code}: {text[:200]}", retryable=False)`(`llm.py:53-54`)
- **token 统计**：解析 `data["choices"][0]["message"]["content"]` 为文本；`usage.prompt_tokens` → `tokens_in`、`usage.completion_tokens` → `tokens_out`，缺省 0(`llm.py:56-63`)

### 1.5 视觉/多模态调用 `call_vision(...)`（`llm.py:66-106`）
- 签名：`call_vision(provider, model, api_key, prompt, image_data_uri, timeout=60.0)`(`llm.py:66-67`)
- docstring：走 OpenAI 兼容 `content` 数组格式（text + image_url）；「豆包视觉模型与绘图模型同在火山方舟，可复用 image_api_key」；`image_data_uri` 形如 `data:image/png;base64,xxx`(`llm.py:68-70`)
- 复用同一 `LLM_ENDPOINTS` 表(`llm.py:71-73`)
- 请求体 messages content 为数组：`{"type":"text","text":prompt}` + `{"type":"image_url","image_url":{"url":image_data_uri}}`，`temperature: 0.3`(`llm.py:75-82`)
- 异常映射与 `call_llm` 相同（超时/请求错误/401/429/≥500/≥400），文案前缀为「视觉模型」(`llm.py:85-97`)
- 返回同样解析 content 与 usage token(`llm.py:99-106`)

> 注：vision 调用同样靠 `provider` 走上述 4 个端点；并无独立 `vision_model` 常量，模型名由调用方传入 `model` 参数。

---

## 二、TTS 配音服务 `backend/app/services/tts.py`（共 220 行）

### 2.1 模块定位与支持引擎
- docstring 自述支持两种引擎(`tts.py:1-9`)：
  - `volcano`（火山引擎大模型语音合成 HTTP 非流式接口，**推荐**）(`tts.py:4`)
  - `siliconflow`（OpenAI 兼容 `/audio/speech`，**备用**）(`tts.py:5`)
- 未配置 Key → 抛 `TTSUnavailable`，编排降级为「用户手动上传音频」(`tts.py:7`)
- 长文案按段合成，再用 `imageio-ffmpeg` 拼接为单一音频，供 compose/jianying 消费(`tts.py:8-9`)
- 依赖：`base64`、`subprocess`、`tempfile`、`uuid`、`pathlib.Path`、`httpx`、`dataclass`(`tts.py:10-16`)

> 注：任务清单中的 MiniMax 引擎在本文件**不存在**；实际为 volcano + siliconflow。

### 2.2 端点与默认值常量
| 常量 | 值 | 证据 |
|------|-----|------|
| `VOLCANO_TTS_ENDPOINT` | `https://openspeech.bytedance.com/api/v1/tts` | `tts.py:19` |
| `VOLCANO_CLUSTER` | `volcano_tts` | `tts.py:20` |
| `VOLCANO_DEFAULT_VOICE` | `zh_male_M392_conversation_wvae_bigtts` | `tts.py:22` |
| `SILICONFLOW_TTS_ENDPOINT` | `https://api.siliconflow.cn/v1/audio/speech` | `tts.py:25` |
| `SILICONFLOW_DEFAULT_MODEL` | `IndexTeam/IndexTTS-2` | `tts.py:26` |
| `SILICONFLOW_DEFAULT_VOICE` | `speech:default` | `tts.py:27` |
- 注释说明 2.0 音色 `*_uranus_bigtts` 仅 v3 支持，默认音色用 v1 HTTP 可用音色(`tts.py:21`)

### 2.3 数据结构与异常
- `TTSResult`：`audio_path: str`、`duration: float = 0.0`、`segment_count: int = 0`(`tts.py:30-34`)
- `TTSUnavailable(Exception)`：未配置 Key，降级手动上传音频(`tts.py:37-38`)
- `TTSError(Exception)`：调用失败(`tts.py:41-42`)

### 2.4 火山合成 `_synth_volcano(...)`（`tts.py:45-81`）
- 签名：`(text, api_key, voice, appid, timeout, speed=1.0)`，返回 mp3 字节(`tts.py:45-46`)
- **鉴权特殊**：`Authorization` 头为 `Bearer;{api_key}`（Bearer 与 token 以**分号**分隔）(`tts.py:49,54`)
- **appid 必填**，为空抛 `TTSError("E6207: 火山 TTS 需配置 appid...")`(`tts.py:52-53`)
- docstring 说明：`app.token` 无实际鉴权作用，可传任意非空串，此处复用 access_token(`tts.py:50`)
- payload 结构(`tts.py:55-61`)：
  - `app`: `{appid, token=api_key, cluster=VOLCANO_CLUSTER}`
  - `user`: `{uid: "shipinhao"}`
  - `audio`: `{voice_type: voice or VOLCANO_DEFAULT_VOICE, encoding: "mp3", speed_ratio: _clamp_speed(speed)}`
  - `request`: `{reqid: uuid4().hex, text, operation: "query"}`
- 异常：RequestError → E6203；HTTP ≥400 → E6205(`tts.py:62-67`)
- 业务码判断：`code != 3000` 即失败(`tts.py:70-71`)
  - msg 含 `authenticate`/`grant not found` → `E6204` 鉴权失败(`tts.py:72-74`)
  - msg 含 `voice_type` 或 `code==3050` → `E6210` 音色不存在或无授权(`tts.py:75-76`)
  - 其他 → `E6208 合成失败 code={code}`(`tts.py:77`)
- `data` 空 → `E6209 返回空音频`；否则 `base64.b64decode(data)`(`tts.py:78-81`)

### 2.5 硅基流动合成 `_synth_siliconflow(...)`（`tts.py:84-104`）
- 签名：`(text, api_key, voice, model, timeout, speed=1.0)`(`tts.py:84-85`)
- 头：`Authorization: Bearer {api_key}`、`Content-Type: application/json`(`tts.py:87`)
- payload：`model`(默认 IndexTeam/IndexTTS-2)、`input=text`、`voice`(默认 speech:default)、`response_format: "mp3"`、`speed: _clamp_speed(speed)`(`tts.py:88-94`)
- 异常：RequestError→E6203；401→E6204 Key 无效；≥400→E6205(`tts.py:98-103`)
- 返回 `resp.content`（原始字节）(`tts.py:104`)

### 2.6 语速与分发
- `_clamp_speed(speed)`：限制在 **0.5~2.0**，非法值（TypeError/ValueError）回退 **1.0**(`tts.py:107-112`)
- `_synth_one(...)`：按 provider 分发，`volcano`→`_synth_volcano`、`siliconflow`→`_synth_siliconflow`，其他抛 `E6202 暂不支持的 TTS 供应商`(`tts.py:115-122`)

### 2.7 多段合成拼接 `synthesize(...)`（`tts.py:125-154`）
- 签名：`(segments, provider, api_key, out_dir, voice=None, appid=None, model=None, timeout=120.0, speed=1.0)`(`tts.py:125-127`)
- `segments` 形如 `[{"text":"..."}, ...]`（复用 F 模块产物）(`tts.py:130`)
- Key 为空 → `TTSUnavailable("未配置 TTS API Key")`(`tts.py:134-135`)
- 取各段非空 `text` 并 strip，无文本 → `E6201 无可合成的分段文本`(`tts.py:137-139`)
- 逐段合成写入 `out_dir/seg_{i:03d}.mp3`(`tts.py:144-148`)
- 拼接为 `out_dir/audio.mp3`，探测时长(`tts.py:150-152`)
- 返回 `TTSResult(audio_path, duration, segment_count=段数)`(`tts.py:153-154`)

### 2.8 探活/试听
- `test_connectivity(...)`：合成短句「测试配音，你好。」，**不落盘不拼接**，返回字节数(>0 即连通)；Key 空→TTSUnavailable，空音频→E6209(`tts.py:157-171`)
- `synth_preview(...)`：合成「你好，这是配音试听效果。」返回 mp3 字节供前端播放，不落盘(`tts.py:174-184`)

### 2.9 音频拼接底层
- `_ffmpeg_exe()`：`import imageio_ffmpeg; return imageio_ffmpeg.get_ffmpeg_exe()`(`tts.py:187-189`)
- `_concat_audio(parts, out_path)`：单段直接复制字节；多段用 ffmpeg **concat demuxer**（`-f concat -safe 0 -i list -c copy`），写临时清单文件，失败抛 `E6206 音频拼接失败`，finally 删临时文件(`tts.py:192-210`)
- `_probe_duration(path)`：复用 `app.modules.video_module.get_audio_duration`，round 到 2 位，异常回退 0.0(`tts.py:213-219`)

---

## 三、音色库 `backend/app/services/voices.py`（共 56 行）

### 3.1 模块定位
- 火山引擎（豆包）大模型 TTS 音色库；音色 ID 沿用火山大模型命名（`*_bigtts`）(`voices.py:1-3`)
- 注意：库为候选清单，实际可用性取决于用户火山账号授权；合成/试听返回 E6210 表示音色不存在或无授权(`voices.py:4-6`)

### 3.2 分类 `CATEGORIES`（6 类，保持顺序，`voices.py:9-16`）
| key | name | desc | 证据 |
|-----|------|------|------|
| `narration` | 视频配音 | 解说 / 旁白 / 纪实 | `voices.py:10` |
| `male` | 通用男声 | 叙事 / 讲书 | `voices.py:11` |
| `female` | 通用女声 | 亲切 / 治愈 | `voices.py:12` |
| `character` | 角色扮演 | 戏剧感强 | `voices.py:13` |
| `dialect` | 方言口音 | 地方特色 | `voices.py:14` |
| `emotion` | 多情感 | 可调情绪 | `voices.py:15` |

### 3.3 音色清单 `VOICE_LIBRARY`（共 24 个，`voices.py:19-55`）
字段：`id`=火山 voice_type，`name`=展示名，`tag`=描述标签，`category`=分类 key。

视频配音 narration（5 个）：
| id | name | tag | 行号 |
|----|------|-----|------|
| `zh_male_M392_conversation_wvae_bigtts` | 沉稳解说 | 叙事·讲书 | `voices.py:21` |
| `zh_male_jieshuonansheng_mars_bigtts` | 解说男声 | 纪实·旁白 | `voices.py:22` |
| `zh_female_jitangmeimei_moon_bigtts` | 鸡汤妹妹 | 情感·治愈解说 | `voices.py:23` |
| `zh_male_silang_moon_bigtts` | 磁性四郎 | 低沉·质感旁白 | `voices.py:24` |
| `zh_female_zhixingnvsheng_mars_bigtts` | 知性女声 | 知识·讲解 | `voices.py:25` |

通用男声 male（4 个）：
| id | name | tag | 行号 |
|----|------|-----|------|
| `zh_male_wennuanahu_moon_bigtts` | 温暖阿虎 | 温暖·亲和 | `voices.py:28` |
| `zh_male_shaonianzixin_moon_bigtts` | 少年自信 | 年轻·清亮 | `voices.py:29` |
| `zh_male_qingcang_moon_bigtts` | 擎苍 | 浑厚·大气 | `voices.py:30` |
| `zh_male_yangguangqingnian_moon_bigtts` | 阳光青年 | 活力·阳光 | `voices.py:31` |

通用女声 female（5 个）：
| id | name | tag | 行号 |
|----|------|-----|------|
| `zh_female_wanwanxiaohe_moon_bigtts` | 温柔小荷 | 亲切·治愈 | `voices.py:34` |
| `zh_female_qingxinnvsheng_mars_bigtts` | 清新女声 | 轻快·明亮 | `voices.py:35` |
| `zh_female_shuangkuaisisi_moon_bigtts` | 爽快思思 | 活泼·带货 | `voices.py:36` |
| `zh_female_tianmeixiaoyuan_moon_bigtts` | 甜美小源 | 甜美·邻家 | `voices.py:37` |
| `zh_female_wenrouxiaoya_moon_bigtts` | 温柔小雅 | 温柔·舒缓 | `voices.py:38` |

角色扮演 character（4 个）：
| id | name | tag | 行号 |
|----|------|-----|------|
| `zh_male_jingqiangkanye_moon_bigtts` | 京腔侃爷 | 幽默·接地气 | `voices.py:41` |
| `zh_female_gaolengyujie_moon_bigtts` | 高冷御姐 | 气场·御姐 | `voices.py:42` |
| `zh_male_aojiaobazong_moon_bigtts` | 傲娇霸总 | 戏剧·霸总 | `voices.py:43` |
| `zh_female_meilinvyou_moon_bigtts` | 魅力女友 | 撒娇·亲密 | `voices.py:44` |

方言口音 dialect（3 个）：
| id | name | tag | 行号 |
|----|------|-----|------|
| `zh_male_jingqiangkanye_moon_bigtts_dialect` | 北京话 | 京味·儿化 | `voices.py:47` |
| `zh_female_wankouxiaohe_moon_bigtts` | 湾区小何 | 台湾腔 | `voices.py:48` |
| `zh_male_yuangulaoyeye_moon_bigtts` | 粤语老爷 | 粤语·港味 | `voices.py:49` |

多情感 emotion（3 个）：
| id | name | tag | 行号 |
|----|------|-----|------|
| `zh_female_roumeinvyou_emo_v2_mars_bigtts` | 柔美女友 | 可调情绪 | `voices.py:52` |
| `zh_male_beijingxiaoye_emo_mars_bigtts` | 北京小爷 | 可调情绪 | `voices.py:53` |
| `zh_female_jiaohuanvsheng_emo_mars_bigtts` | 娇憨女声 | 可调情绪 | `voices.py:54` |

> 合计 5+4+5+4+3+3 = **24 个音色**。注意 `zh_male_jingqiangkanye_moon_bigtts` 出现在 character（行 41），方言版 `..._dialect`（行 47）为独立音色。

---

## 四、ASR 语音转写 `backend/app/services/asr.py`（共 69 行）

### 4.1 模块定位
- 视频/音频 → 原始逐字稿；第一版预留**硅基流动 SenseVoice** 接口位；未配 Key → 抛 `ASRUnavailable`，编排降级为「手贴逐字稿」(`asr.py:1-5`)
- 依赖 `httpx`、`dataclass`(`asr.py:6-7`)

### 4.2 端点与默认模型
| 常量 | 值 | 证据 |
|------|-----|------|
| `SILICONFLOW_ASR_ENDPOINT` | `https://api.siliconflow.cn/v1/audio/transcriptions`（OpenAI 兼容） | `asr.py:10` |
| `DEFAULT_ASR_MODEL` | `FunAudioLLM/SenseVoiceSmall` | `asr.py:11` |

### 4.3 数据结构与异常
- `ASRResult`：`text: str`、`duration: float = 0.0`(`asr.py:14-17`)
- `ASRUnavailable(Exception)`：未配 Key，降级手贴逐字稿(`asr.py:20-21`)
- `ASRError(Exception)`：调用失败(`asr.py:24-25`)

### 4.4 字节转写 `transcribe(...)`（`asr.py:28-55`）
- 签名：`(audio_bytes, provider, api_key, filename="audio.mp3", model=None, timeout=120.0)`(`asr.py:28-30`)
- Key 空 → `ASRUnavailable`(`asr.py:35-36`)
- **唯一支持供应商 `siliconflow`**；其他 → `E6102 暂不支持的 ASR 供应商`(`asr.py:37-38`)
- 头 `Authorization: Bearer {api_key}`；multipart files=file，data=`{model: model or DEFAULT_ASR_MODEL}`(`asr.py:40-42`)
- 异常：RequestError→E6103；401→E6104 Key 无效；≥400→E6105(`asr.py:44-52`)
- 返回 `ASRResult(text=body.get("text").strip())`(`asr.py:54-55`)

### 4.5 URL 转写 `transcribe_url(...)`（`asr.py:58-68`）
- 签名：`(video_url, provider, api_key, timeout=120.0)`(`asr.py:58-59`)
- Key 空 → `ASRUnavailable`(`asr.py:61-62`)
- 先 `httpx.get(video_url, follow_redirects=True)` 下载，失败 → `E6106 下载媒体失败`(`asr.py:63-67`)
- 再调用 `transcribe(...)`，filename 固定 `"source.mp4"`(`asr.py:68`)

---

## 五、抖音采集 `backend/app/services/collect.py`（共 92 行）

### 5.1 模块定位
- 贴链接 → 拿视频地址/标题/博主/播放量等元数据；第一版预留 **TikHub** 接口位；未配 Key → 抛 `CollectUnavailable`，编排降级「手填逐字稿/标题」，不阻断链路(`collect.py:1-5`)
- 依赖 `re`、`httpx`、`dataclass/field`(`collect.py:6-8`)

### 5.2 端点
| 常量 | 值 | 证据 |
|------|-----|------|
| `TIKHUB_ENDPOINT` | `https://api.tikhub.io/api/v1/douyin/web/fetch_one_video`（按量付费，占位） | `collect.py:11` |

### 5.3 数据结构与异常
- `CollectResult` 字段（`collect.py:14-21`）：
  - `title: str = ""`、`author: str = ""`、`play_count: int = 0`、`digg_count: int = 0`
  - `video_url: str = ""`（无水印视频地址，供下游 ASR 取音频）
  - `raw_meta: dict = field(default_factory=dict)`
- `CollectUnavailable(Exception)`：未配 Key 等，降级手填(`collect.py:24-25`)
- `CollectError(Exception)`：链接无效、接口报错等(`collect.py:28-29`)

### 5.4 URL 提取 `extract_url(...)`（`collect.py:33-41`）
- 正则 `_URL_RE = re.compile(r"https?://[^\s，。]+")`(`collect.py:33`)
- 从分享口令文本抠链接，无匹配 → `E6001 未在输入中识别到有效链接`；返回时 `rstrip("/")`(`collect.py:38-41`)

### 5.5 采集 `fetch_douyin(...)`（`collect.py:44-71`）
- 签名：`(url_or_share, provider, api_key, timeout=20.0)`(`collect.py:44-45`)
- Key 空 → `CollectUnavailable("未配置采集 API Key")`(`collect.py:51-52`)
- 先 `extract_url`(`collect.py:54`)
- **唯一支持供应商 `tikhub`**；其他 → `E6002 暂不支持的采集供应商`(`collect.py:56-57`)
- 头 `Authorization: Bearer {api_key}`；`httpx.get(TIKHUB_ENDPOINT, params={"url": url})`(`collect.py:59-62`)
- 异常：RequestError→E6003；401→E6004 Key 无效；≥400→E6005(`collect.py:63-69`)
- 返回 `_parse_tikhub(resp.json())`(`collect.py:71`)

### 5.6 解析 `_parse_tikhub(data)`（`collect.py:74-90`）
- 容错取值，字段路径以实际接入响应为准(`collect.py:75`)
- `aweme = data.data.aweme_detail || data.data || {}`(`collect.py:76`)
- 取 `statistics`、`author`、`video.play_addr.url_list`(`collect.py:77-82`)
- 返回字段映射(`collect.py:83-90`)：
  - `title = aweme.desc`
  - `author = author.nickname`
  - `play_count = int(stats.play_count)`
  - `digg_count = int(stats.digg_count)`
  - `video_url = url_list[0]`（无则空串，无水印地址）
  - `raw_meta = {"aweme_id": aweme.aweme_id, "stats": stats}`

---

## 六、降级（Unavailable）汇总
| 服务 | 异常 | 触发条件 | 编排降级行为 | 证据 |
|------|------|---------|-------------|------|
| TTS | `TTSUnavailable` | 未配 api_key | 用户手动上传音频 | `tts.py:37-38,134-135` |
| ASR | `ASRUnavailable` | 未配 api_key | 手贴逐字稿 | `asr.py:20-21,35-36,61-62` |
| Collect | `CollectUnavailable` | 未配 api_key | 手填逐字稿/标题，不阻断 | `collect.py:24-25,51-52` |
| LLM | （无 Unavailable）仅 `LLMError(retryable)` | — | 重试/失败 | `llm.py:21-25` |

