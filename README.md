# netease-metadata-enrich

网易云音乐下载文件的元数据补全流水线。

```
文件名解析 → artist + title 标签
         → 网易云歌单/API 匹配 → album + tracknumber + date 标签
```

> **免责声明**
>
> 本项目仅供个人学习和技术研究使用。请遵守相关法律法规和网易云音乐服务条款。使用本工具产生的任何后果由使用者自行承担。

## 环境依赖

- **Python 3.8+**
- Python 包：

```bash
pip install mutagen NetEaseMusicApi requests
```

- 需确保音乐文件已转换为 FLAC/MP3 等标准格式（本工具不提供格式转换功能）

## 脚本

```
scripts/
├── tag_from_filename.py       # 第二步：文件名 → artist + title
└── enrich_from_netease.py     # 第三步：网易云歌单/搜索 → album + tracknumber + date
```

### tag_from_filename.py

从文件名提取 artist 和 title 写入标签。

```bash
python scripts/tag_from_filename.py <音乐目录>
```

- 文件名格式要求：`艺术家 - 曲名.ext`
- 仅处理缺少 artist 或 title 的文件，已有标签的跳过
- 支持格式：FLAC、MP3、M4A、MP4、OGG
- 写入标签时会自动创建 `.bak` 备份，写入成功后清理

### enrich_from_netease.py

从网易云音乐元数据源匹配 album、tracknumber、date。

```bash
# 方式一：歌单匹配（推荐，准确性最高）
python scripts/enrich_from_netease.py <音乐目录> --playlist-id <歌单ID>

# 方式二：API 搜索（补充未匹配的）
python scripts/enrich_from_netease.py <音乐目录> --playlist-id <歌单ID> --search
```

- 歌单拉取有网络断线重试（3 次退避），单曲详情批次也有重试
- 已有完整标签的文件自动跳过
- 写入有 `.bak` 备份保护，写入失败可恢复

## 匹配策略

`enrich_from_netease.py` 的匹配按优先级从严格到宽松：

| 优先级 | 策略 | 说明 |
|--------|------|------|
| 1 | 精确匹配 | artist + title 归一化完全一致 |
| 2 | 专辑+轨号 | 同专辑同轨号匹配 |
| 3 | 艺术家子集 | 歌名一致 + 本地艺术家词 ⊆ 歌单艺术家词 |
| 4 | 歌名兜底 | 仅歌名匹配（常见歌名可能误配） |
| 5 | 标题前缀 | 前缀 4 词一致 + 至少一个艺术家词重叠 |

## 归一化处理

匹配前对 artist/title 做以下归一化：

- **NFKC** — 半角假名→全角、全角 ASCII→半角、兼容字符标准化
- **去重音** — `Céline` → `Celine`、`Über` → `Uber`
- **剥离 featuring** — `feat.`、`ft.`、`featuring`、`vs.`、`versus`、`with`、`pres.`
- **去括号及内容** — `(Live)`、`[Bonus Track]` 等
- **保留 CJK** — 中日韩文字原样保留

## 已知限制

- 网易云 v6 playlist API 的 tracks 分页参数 (`n`/`s`) 对列表不生效，实际通过 `trackIds` + `song/detail` 批量拉取
- Windows 下 `datetime.fromtimestamp` 不接受 1970 前的负数时间戳，已用 `timedelta` 替代
- 部分专辑 `publishTime` 为异常大值（如 `916070400007`），已用 1900~2100 范围过滤
- 古典乐、影视原声等多艺术家曲目可能需要额外模糊匹配
- 文件名含超 GBK 范围的 Unicode 字符时，Windows 系统可能产生 surrogate 编码，脚本已做检测和处理
