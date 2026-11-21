# 洛克王国世界 · 6 对 6 伤害与范围计算器

一个本地运行的网页工具，用于洛克王国世界 60 级 PVP 的伤害计算和对方状态推断。

我方精灵的性格、天分、面板和生命是已知的；对方只输入精灵种类，工具会枚举所有可能的性格和天分组合，再根据战斗记录逐步缩小范围。

## 现在能做什么

- 双方各 6 个位置，点击位置切换当前出战。
- 我方每只精灵最多配置 4 个技能，技能下拉显示它能学的攻击/防御/状态技能；当前出战伤害表只计算攻击技能。
- 显示我方技能对对方的伤害范围、对方剩余生命百分比范围和能否击倒。
- 显示对方技能对我方的伤害范围、我方剩余具体生命和能否击倒。
- 对方每只精灵独立维护候选范围：默认 46,320 种（30 种性格 × 1,544 种天分配置）。
- 对局记录按回合保存双方行动、实际先手、是否应对、伤害/生命变化、天气、增减益、印记、状态和回合结束快照；可以添加、删除回合，并导出/导入 JSON。
- 对局记录会持续更新对方精灵的性格/天分范围和已见技能；上方 6v6 对位仍可自由选择。
- 当前出战双方显示速度，并按“先比先手值，再比速度”给出谁先行动的判断。
- 显示精灵头像和攻击技能图标；缺图时显示名字首字占位。
- 页面状态保存在浏览器本地，刷新后自动恢复。
- “当前出战”伤害表使用对局记录推断出的对方范围、当前生命、双方增减益、天气和星陨；对局中也可以点击任意我方/对方位置查看其他对位。未建模的印记/状态会在表上方提示。
- 队伍卡片只配置精灵、性格、天分和技能；生命、能量、增减益由对局记录提供，不再手动填写。

当前有 **243 个自动测试**，全部通过。

## 最快上手

### 1. 准备环境

需要 Python 3.9 或更新版本。核心计算器和网页服务**只使用 Python 标准库**，不需要 `pip install` 任何东西。可选 OCR 使用独立 Conda 环境，安装方式见下文。

Windows 上如果 `python` 命令不可用，可以改用 `py`。中文输出乱码时，在命令前加 `PYTHONIOENCODING=utf-8`。

### 2. 克隆并启动网页

```bash
git clone https://github.com/zusuyu/roco-calc.git
cd roco-calc
python server.py
```

浏览器打开：

```text
http://127.0.0.1:8000
```

默认页面应该能看到：我方水灵对龙息帕尔，气泡伤害 140～210，对方剩余生命 46.1%～68.4%。这说明数据、公式和网页都正常。

服务默认只监听本机。如果 8000 端口被占用：

```bash
python server.py --port 8001
```

### 3. 第一次克隆后补图片

仓库里**不包含游戏图片**，只包含图片名单 `data/assets.json`。直接启动网页时，头像和技能图标会显示成名字首字占位，这是正常的。

运行一次下载脚本：

```bash
python download_assets.py --dry-run
python download_assets.py
```

`--dry-run` 只统计，不联网；正式下载会一张一张地进行，默认间隔 0.6 秒，已存在的图片会跳过。下载完成后刷新网页即可看到图片。

图片保存在：

```text
web/assets/pets/     精灵头像
web/assets/skills/   攻击技能图标
```

这两个目录已被 `.gitignore` 忽略，不会上传到 GitHub。图片来自游戏/wiki，只保存在本机。

### 4. 跑测试

```bash
PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -v
```

应看到 `Ran 243 tests` 和 `OK`。

### Windows独立持续截图工具

`RocoCapture.exe` 只用MSS抓取游戏窗口，默认持续10分钟、目标20 FPS抓屏、每秒保存一张原始PNG，支持F9开始/停止、F8额外留图。exe不需要Python、Conda或OCR模型。下载、保存频率设置和构建方法见 [Windows截图工具说明](docs/windows-capture.md)。

### 5. 可选：安装本地 CPU OCR

如果本机有 Conda，可以用：

```bash
conda env create --prefix ./.conda/ocr --file environment-ocr.yml
conda activate ./.conda/ocr
python download_ocr.py
```

没有 Conda 时，用 Python 3.10 或更新版本建独立虚拟环境（本机已用 Python 3.14）：

```bash
python -m venv .venv-ocr314
# Windows:
.venv-ocr314\Scripts\python -m pip install -r requirements-ocr.txt
.venv-ocr314\Scripts\python download_ocr.py
# Linux/macOS:
.venv-ocr314/bin/python -m pip install -r requirements-ocr.txt
.venv-ocr314/bin/python download_ocr.py
```

主计算器仍然只使用 Python 标准库；OCR 环境放在 `.venv-ocr314`，不会影响网页计算。该环境采用 RapidOCR 3.10.0、CPU 版 ONNX Runtime 1.32.0，不需要独立显卡。

下载内容是 PP-OCRv4 中文 mobile 文字检测及识别 ONNX 模型，合计约 15 MB，保存在 `models/ocr/`；环境与模型均被 Git 忽略。脚本显式选择 v4 mobile，关闭方向分类及 GPU 后端，并生成 `.cache/ocr.yaml`（CPU 推理线程数为 2）。换电脑或移动仓库后重新运行脚本，更新配置里的绝对路径。依赖安装和首次模型下载需要联网；模型齐全后可以离线识别。

### 网页里的 OCR 操作帧

启动 `python server.py` 后，网页会多一个“OCR 操作帧”卡片。它会自动检查 `.venv-ocr314`、模型、布局和头像：

1. 选择布局（参考图 2094×1179，或 1920×1080 屏幕上的 1600×900 游戏窗口）。
2. 上传一张游戏操作截图。
3. 点“识别操作帧”。
4. 结果会显示双方显示名、确认身份、能量/生命读数、四个技能和头像候选。
5. 身份确认后，可以选择我方/对方第 1～6 只，点“填入队伍”。OCR 只填精灵和可学技能，不会自动写伤害或回合。

如果状态提示“OCR 环境未安装”，按上面的虚拟环境步骤安装后点“重新检查”。OCR 环境没装好时，主网页和伤害计算仍然正常。

识别整张图片，或识别已经裁成单行的文字图片：

```bash
python download_ocr.py --image "参考2.jpg"
python download_ocr.py --image "单行文字.png" --recognition-only
```

在 Python 中复用模型（进程启动时加载一次）：

```python
from rapidocr import RapidOCR
from download_ocr import prepare_config

engine = RapidOCR(config_path=str(prepare_config()))
result = engine("单行文字.png", use_det=False, use_cls=False)
print(result.txts, result.scores)
```

已支持使用配置区域读取静态操作帧、头像 Top 10 检索，以及固定区域的离线视频采样和人工复核记录。尚未实现自动区域定位、连续帧确认和全自动回合归属。已在当前 Linux 环境验证 CPU 离线推理；Windows 截屏与性能需在目标机器验证。

头像与四技能识别只用于静态操作帧；攻击动画帧不运行这两项。攻击帧可离线采样 HUD、提示和伤害浮字，自动伤害实例跟踪与事件归属仍待实现。

高清参考片的已确认结果和可复现采样入口见 [视频识别说明](docs/video-recognition.md)：

```bash
python review_video.py export --output .cache/video_review/reviewed
python review_video.py extract "参考视频-高清.mp4" --kind hud --time 80 --save-crops
```

第一条只用标准库导出仓库内已复核的27回合；第二条需要可选OCR环境、模型和本地原片，输出原始证据，不会自动猜测新视频的回合。参考片、裁剪图片和环境不进入Git；`data/battles/reference_hd.json` 保留已确认的数字和未知项，不含本机绝对路径。

参考视频已有本地离线 OCR 采样证据时，可以生成待复核的对战 JSON，并合并人工确认：

```bash
python export_battle_review.py --review configs/battle_reference_video_review.json
```

默认读取 `.cache/video_analysis/turn_text.jsonl` 和 `sampling_report.json`，输出同目录下的 `battle_review.json`；视频和采样结果需在本地准备。此入口整理已有证据，不负责视频采样。`candidate_turns` 只是候选分组，`turns` 在确认前为空。人工确认按视频文件名、阵营和首次观察时间关联，保留 OCR 原文；身份确认不会自动确认强制切换、回合边界、血量或能量。参考视频确认文件记录了骨龙携带借用、暂未入库的阿布，以及皮肤头像待同步的月牙雪熊。阿布可有原始名字而无数据库 ID；皮肤不会被伪造为另一只精灵。复写、借用、取念允许使用原生技能池之外的技能，不能仅因不在技能池中就判读数错误；静态 OCR 的技能池校对尚未接入这些动态机制。

伤害结算按用户确认规则整理：无连击效果的技能取同一伤害实例的最后一个读数，不把动画过程中的读数相加。白色字体表示普通系，不能据此判断伤害是否来自额外效果；其他属性的颜色映射尚未确认。冻结属于普通效果，参考视频中的化蝶由冻结按百分比生命斩杀，比例未知；技能伤害与冻结效果分列。当前这些规则用于离线人工复核记录，尚未实现自动字体颜色分类及伤害实例跟踪。

整张游戏截图会在文字检测前被缩小，技能小字容易漏检。先裁剪技能栏再识别：

```bash
python download_ocr.py --image "参考4.jpg" --crop 230 390 540 980
```

坐标依次为原图像素中的左、上、右、下；适用于这张参考图，其他窗口位置与尺寸需重新校准。`--scale 3` 可以在裁剪后放大。

技能名专用入口会保留原始文字与置信度，对低置信度文字进行倾斜校正、补边和二值化重读，再用技能池核对：

```bash
python ocr_skills.py "参考4.jpg" --crop 230 390 540 980
python ocr_skills.py "参考2.jpg" --crop 210 335 415 750 --scale 3 --pet 花衣蝶
```

`--pet` 必须是已经确认的原名或图鉴编号，不能使用玩家昵称；同名形态可另传 `--form`。指定精灵时从 `data/learnsets.csv` 加载完整技能池，包含攻击、防御、状态技能；省略时使用学习表中所有精灵的技能集合。识别结果与池内名称完全一致时标记 `exact`；低置信度且同字数、仅差一字、最近候选唯一时才标记 `corrected`；其他情况保留 `unresolved` 和候选，不强选。

JSON 输出保留 `raw_text`、`raw_confidence`、各次重读结果及最终状态。`base_power` 是数据库基础威力，画面上的威力数字保留在 `other_readings`，目前尚未自动归属到具体技能。

当前样例：图4四个技能名直接识别正确（倾泻、能量守恒、飞羽、风隐）；图2的虫结阵需重读，假寐需技能池校对。这是两个样例的结果，不代表完整对战准确率。鼠标遮挡时应等待后续无遮挡帧重读。

#### 下游使用的操作帧 JSON

```bash
python ocr_frame.py "参考4.jpg" --layout configs/ocr_reference4.json > result.json
```

标准输出只包含 JSON，OCR 日志走标准错误。布局内字段区域按游戏内容的原始像素配置；尺寸不一致会报错，避免读错区域。四个技能各有独立的名字、威力及能耗区域，漏识别时槽位保持不变，不会让后面的技能前移。`frame_type: "operation"` 表示本入口按操作帧处理，并非自动判别画面类型。

输出字段：

| 字段 | 含义 |
|---|---|
| `our` / `opponent` | 我方 / 敌方精灵 |
| `name` / `number` / `form` | 已确认的数据库原名、编号和形态；未确认时为 `null` |
| `pet_id` | 学习表中的精灵唯一 ID；未取得时为 `null`，不将图鉴编号当作唯一 ID |
| `identity_source` | `provided`、`avatar`、`avatar_and_name` 或 `unresolved` |
| `display_name` | 从画面读取的显示名，可能是玩家昵称 |
| `energy` | 从画面读取的当前能量；`0` 有效，读取失败是 `null` |
| `hp` / `max_hp` | 布局配置生命区域时读取当前生命和上限，不从伤害公式补值 |
| `hp_percent` | 布局配置百分比区域时读取0～100的显示百分比 |
| `hp` / `max_hp` | 配置了 `hp` 区域时输出当前和最大生命；读不到斜线或数值不合法时为 `null` |
| `hp_percent` | 配置了 `hp_percent` 区域时输出显示生命百分比，范围 0～100 |
| `avatar_match` | 头像 Top 10、视觉匹配分、显示名核对和待确认状态 |
| `skills` | 固定四条，`slot` 为 1～4 |
| `skills[].name` / `category` | 技能名 / 攻击、防御、状态 |
| `skills[].power` | 画面显示威力；防御、状态技能或读取失败均为 `null` |
| `skills[].base_power` | 数据库基础威力，与画面威力分开保存 |
| `skills[].energy_cost` | 技能卡片显示的能量消耗，不从数据库填充 |
| `skills[].name_status` | `exact`、`corrected` 或 `unresolved` |
| `skills[].power_status` | `recognized`、`not_applicable` 或 `unresolved` |

身份可以通过 `--our-pet 原名或编号` / `--opponent-pet 原名或编号` 传入，形态分别使用 `--our-form` / `--opponent-form`；我方确认身份同时用于限定技能池。**昵称不能作为已确认身份传入。** 配置包含 `avatar` 区域时默认执行头像检索，达到当前确认条件才填原名，未达到时仍为 `null`；`--no-avatar` 可跳过。`--debug` 可在 JSON 内附加各区域 OCR 原文、重读记录和置信度。

参考图4实测：双方显示名为“钥匙扣”和“上古战龙”，当前能量为 9 和 0；技能依次为倾泻（画面威力 58，能耗 3）、能量守恒（无威力，能耗 2）、飞羽（无威力，能耗 1）、风隐（无威力，能耗 1）。

#### 1920×1080 屏幕上的 1600×900 游戏窗口

参考图5的完整截图是 1920×1080，游戏**内容**区域为 `[160, 66, 1760, 966]`（1600×900，不含标题栏）。运行：

```bash
python ocr_frame.py "参考5.png" --layout configs/ocr_1600x900_on_1920x1080.json --debug > result.json
```

同一配置支持整张桌面截图和已经裁成 1600×900 的游戏图。布局的 `image_size` 是游戏内容大小，`screen_size` 是桌面大小，`viewport` 是游戏内容在桌面中的矩形；头像、血量、能量、技能区域都相对于游戏内容左上角。窗口移动时只需更新 `viewport`；游戏 UI 大小或缩放变化时需要重新校准内部区域。目前不会自动寻找 Windows 窗口。

参考图5实测：我方罗隐（显示名“罗大哥”），403/403 生命、能量 10；敌方护主犬，生命 100%、能量 10。四技能为贪婪（状态，能耗 2）、泥浆铠甲（状态，能耗 2）、鸣沙陷阱（攻击，画面威力 560，能耗 4）、困兽（攻击，画面威力 180，能耗 3）。困兽原始 OCR 读作“团兽/国兽”，使用罗隐技能池校对。完整桌面与裁图的业务字段一致，参考图4回归结果保持一致。

倾斜技能名称若漏检，会对已配置的单行区域旋转并补边重读；数字置信度低时也尝试检测徽标内的文字并校正倾斜，同时保留初次读取作为候选。确认失败仍输出 `null`。

#### 头像 Top 10 与显示名核对

单独检索头像（坐标只对应本张参考图）：

```bash
python avatar_match.py "参考2.jpg" --crop 51 0 108 57 --display-name 花衣蝶
```

运行时仅使用顶部头像。启动时加载 589 张独立头像的索引；颜色、轮廓与 SIFT 局部特征先筛选候选，再做少量位置、尺度偏移的模板精排。去掉透明留白，忽略头像左下方属性图标常见遮挡区域，并允许镜像。索引缓存在 `.cache/avatar_index.npz`（约 15 MB）；图片更新会重建索引，相同头像裁图的检索结果也会在进程内缓存。长期运行时应复用 `AvatarMatcher` 实例。

Top 10 按**独立头像**排名；`candidates[].pets` 保留共用此图的全部精灵条目及各自 `pet_id`。`score` 为模板相关性与局部几何证据的组合分，不是正确概率。输出另含 `template_score`、`feature_inliers` 等，便于检查。

显示名只与已经取得的视觉 Top 10 做精确核对，**不修改视觉排名**。每个候选的 `name_match` 及顶层 `name_match_ranks` 标明匹配位置；`name_check` 为 `matches_best`、`matches_other_candidate`、`not_in_top10` 或 `not_read`。即使昵称恰好是另一只精灵原名，也不会把弱视觉候选直接提升为确认身份。

当前初始规则：视觉最高分至少 0.90 且与第二名差距至少 0.08 时，可确认独立精灵身份；名字匹配第一名时，仍要求至少 5 个局部几何内点、分数至少 0.85 和相同分差。共用头像始终保留身份歧义。阈值尚需更多独立游戏样本校准，不能将单帧结果当作完整对战准确率。

`status` 说明：

| 状态 | 说明 |
|---|---|
| `visual_supported` | 视觉证据达到当前确认条件 |
| `visual_name_supported` | 第一名有足够局部视觉证据，并得到显示名辅助 |
| `name_supported_candidate` | Top 10 内有同名项，但视觉证据不足；原名仍为空 |
| `shared_avatar` | 多个精灵条目共用头像，不能仅凭名字消除歧义 |
| `needs_review` | 当前无法确认，可补游戏头像模板或后续评估 MobileNet |

样例：图2花衣蝶为第1名且同名；武者鸡在第2名但视觉证据不足，保留待确认。图4右上角显示“上古战龙”，头像指向迪莫，输出原名迪莫及昵称上古战龙；左上角第一候选为飞飞钥，但当前证据不足，原名仍为空。图1右上角可确认彩蝶鲨。当前 Linux 机器单个未缓存查询约 150～160 ms（不含首次索引加载），不是 Windows 性能保证；连续帧确认与变化触发仍待接入。当前没有下载或运行 MobileNet。

## 常用命令

### 网页

```bash
python server.py
```

### 单只精灵伤害计算

```bash
PYTHONIOENCODING=utf-8 python calc.py \
  --attacker 水灵 --attacker-nature 聪明 \
  --attacker-talents "魔攻=10" \
  --defender 龙息帕尔 --skill 气泡
```

### 单只精灵范围推断

```bash
PYTHONIOENCODING=utf-8 python analyze.py \
  --our 水灵 --nature 聪明 --talent 魔攻=10 \
  --opponent 龙息帕尔 --skill 气泡 \
  --weather 雨天 --observe 341 --observe hp:50:195
```

应看到 `可能组合：4,416 / 46,320`、`可能性格：12 种`。

### 6 对 6 队伍推断演示

```bash
PYTHONIOENCODING=utf-8 python team_demo.py
```

### 完整对局日志演示

```bash
PYTHONIOENCODING=utf-8 python battle_demo.py
```

应看到对方龙息帕尔剩 `366 / 46320` 种、6 种性格，生命 `194～197`、显示 `50%`；第二只奇丽花仍是 `46320 / 46320` 种。示例文件在 `data/battle_example.json`，也可以用 `python battle_demo.py 文件路径` 分析自己的对局。

### 网页里录入一回合

1. 在上方配置双方首发、我方性格/天分/技能、对方精灵和怀疑技能。
2. 在“对局记录 → 添加一回合”里选择双方行动（技能/聚能/切换/空）、谁先行动、是否应对。
3. 填本回合的伤害和打完后的生命；有其他回血/掉血就点“添加生命变化”。
4. 展开“回合结束状态”，只改有变化的全员生命/能量、增减益、状态、印记、天气和魔力值。
5. 点“添加本回合”。对方精灵的性格/天分范围和已见技能会更新。
6. “导出对局/导入对局”可以保存和恢复整场记录。

第一次添加回合后，初始队伍会锁定；要改队伍请先点“清空对局”。

### 重新抓取数据

`data/*.csv` 已经提交在仓库里，正常使用不需要重新抓。要更新数据时：

```bash
python crawl_pets.py
python crawl_type_chart.py
python crawl_skills.py
python crawl_natures.py
python crawl_learnsets.py
```

这些脚本会访问 BWiki。抓取脚本支持 `--cache`，可以复用本地 `.cache/` 里的 Lua 数据，不联网重跑。不要并发猛刷网站。

## 项目结构

| 路径 | 作用 |
|------|------|
| `crawl_pets.py` | 抓精灵基础数据和进化链 |
| `crawl_type_chart.py` | 抓属性克制/抵抗关系 |
| `crawl_skills.py` | 抓技能表 |
| `crawl_natures.py` | 抓性格表 |
| `crawl_learnsets.py` | 抓每只精灵的可学习技能 |
| `download_assets.py` | 限速下载精灵头像和技能图标，生成图片名单 |
| `data/*.csv` | 已生成并提交的游戏数据 |
| `data/assets.json` | 精灵/技能到本地图片的对应关系 |
| `roco_calc.py` | 核心库：面板值、属性倍率、伤害公式 |
| `roco_analysis.py` | 单只精灵的范围推断 |
| `roco_team.py` | 6 对 6 队伍推断、记录重放 |
| `roco_skill_rules.py` | 从技能描述读取先手、应对和防御减伤 |
| `roco_battle.py` | 完整对局日志校验、重放和范围推断 |
| `calc.py` | 单只精灵伤害命令行 |
| `analyze.py` | 单只精灵范围推断命令行 |
| `team_demo.py` | 6 对 6 固定例子演示 |
| `battle_demo.py` | 完整对局日志演示 |
| `data/battle_example.json` | 完整对局日志示例 |
| `server.py` | 本地网页服务和 JSON 接口 |
| `web/` | 网页 HTML、CSS、JavaScript |
| `tests/` | 自动测试（含 `test_roco_battle.py`） |
| `docs/` | 架构、进度、设计、待办和历史记录 |
| `DEVLOG.md` | 每一步做了什么、为什么、当前状态 |

## 数据流程

```text
BWiki Lua 数据模块
        │
        │ crawl_*.py
        ▼
data/*.csv
        │
        │ roco_calc.py
        ▼
面板值 / 属性倍率 / 伤害公式
        │
        │ roco_analysis.py / roco_team.py
        ▼
对方性格、天分、生命范围
        │
        │ server.py
        ▼
web/ 网页
```

图片是另一条线：

```text
BWiki 图片
    │ download_assets.py
    ▼
web/assets/ + data/assets.json
    │ /api/meta 提供路径，/assets/... 提供文件
    ▼
网页头像和技能图标
```

## 文档怎么读

建议按这个顺序：

1. `docs/progress.md`：当前已经做到哪一步，最适合先看。
2. `docs/architecture.md`：数据来源、面板公式、伤害公式、范围推断和接口细节。
3. `docs/frontend-design.md`：页面结构、交互和接口设计。
4. `docs/todo.md`：还没做的功能和已知缺口。
5. `DEVLOG.md`：按时间顺序的开发记录，包含每一步的验证结果。

注意：

- `instruction-20261121.md` 是最早的任务说明，只是历史材料，不代表当前状态。
- `docs/sessions/2026-11-21.md` 是早期阶段总结，部分内容已经过时。
- `docs/frontend-design.md` 的 1～7 节描述旧的单只精灵页面；现在的 6 对 6 页面和后续功能看第 8～10 节。

## 当前限制

- 特性、道具、场地、护盾和其他印记尚未接入。
- 目前只有雨天接入伤害计算；其他天气只显示文本。
- 特殊威力规则技能（如魔能爆、怨力打击）需要手动指定威力。
- 换人顺序和换人后的先手判断尚未实现。
- 不含成长星级/PVE 数值，默认 60 级 PVP。
- 目前是手动录入对局信息，还没有从游戏客户端自动读取。
- 伤害公式和取整以 tzrain 计算器及游戏内实测为准，和其他计算器可能有 ±1 差异。

## 协作约定

- 提交前先跑测试：`PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -v`。
- 每个可验证的小步骤，在 `DEVLOG.md` 末尾追加一条中文记录，写清“做了什么 / 为什么 / 当前状态”。
- 提交信息使用“前缀 + 中文描述”，前缀用 `feat`、`fix`、`docs`、`chore`，例如：
  `feat: 增加速度与先手判断`
- 新增数据或重新抓取数据时，请说明来源和生成命令。
- 不要把 `web/assets/pets/` 和 `web/assets/skills/` 里的图片提交到 Git。
- 大改动建议先开 issue 或分支讨论，避免直接改坏 `master`。

## 数据来源与参考

- 游戏数据：<https://wiki.biligame.com/nrc/> 的 Lua 数据模块。
- 公式和取整参考：<https://rocopvp.tzrain.wiki/battle>。
- 属性克制关系已和 tzrain 内置 18×18 表逐条比对。
- 部分伤害和天气规则用游戏内实测校准，具体记录见 `docs/architecture.md` 和 `DEVLOG.md`。

本项目是个人研究和计算工具，不是游戏官方项目。
