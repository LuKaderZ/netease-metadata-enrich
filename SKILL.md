---
name: netease-metadata-enrich
description: >
  网易云音乐下载文件的元数据补全流水线。文件名提取 artist/title → 网易云 API 匹配补全 album/track/date。
  触发时机：音乐文件缺少元数据标签、需要从网易云音乐歌单匹配元数据。
---

# 网易云音乐元数据补全

批量补全音乐文件的元数据标签，专为网易云音乐下载的来源文件优化。

## 前提

- Python 3，已安装 `mutagen`、`NetEaseMusicApi`、`requests`
- 音乐文件已转换为 FLAC/MP3 等标准格式
- `NetEaseMusicApi` 包（`pip install NetEaseMusicApi`），用于 `--search` 模式 API 搜索；歌单匹配走公开接口无需额外密钥

## 流程概览

```
文件名解析 → 写入 artist + title 标签
                  ↓
网易云歌单匹配 / API 搜索 → 写入 album + tracknumber + date
```

## 第一步：文件名提取 artist/title

转换后的 FLAC 通常无 Vorbis 标签。文件名格式统一为 `艺术家 - 曲名.flac`。

用 `mutagen` 遍历：
- `audio.tags` 为空 或 仅有 `description` 字段（残留 DRM 密钥）→ 解析文件名
- 用 `fname.split(' - ', 1)` 提取 artist 和 title
- 写入 FLAC Vorbis 标签：`flac['artist']`、`flac['title']`

对 MP3 同理，写 ID3 标签 `TPE1`、`TIT2`。

**注意**：已有完整标签的文件跳过，避免覆盖原始元数据。

## 第二步：网易云 API 元数据匹配

### 2a. 优先策略：BACKUPS 歌单

如果用户有包含完整曲库的网易云歌单，直接拉取作为元数据源，准确性最高。

**拉取歌单**：
- 先用 v6 API 获取 trackIds：`http://music.163.com/api/v6/playlist/detail?id=<ID>&n=10000`
- v6 API 的 tracks 有分页限制（仅返回 10 首），改用 trackIds + song/detail 批量拉取
- 每次请求 100 个 ID：`http://music.163.com/api/song/detail?ids=[id1,id2,...]`
- 间隔 150ms 防止限流

**匹配策略**（按优先级）：
1. 精确匹配：归一化后 `artist|title` 完全一致
2. 艺术家子集匹配：本地艺术家词集合 ⊆ 歌单艺术家词集合
3. 标题前缀匹配：标题前 3-5 个词一致 + 至少一个艺术家词重叠

**归一化**：
- Unicode NFKD 去重音（`Céline` → `Celine`、`μ's` → `u's`）
- 去除括号及内容、特殊字符、多余空格
- 全角转半角

### 2b. 补充策略：API 搜索

歌单未覆盖的曲目，用 `api.search.songs(artist + title)` 搜索：
- 优先匹配用户已有 album 标签相同的结果
- 取 publishTime → `datetime.fromtimestamp(ms/1000)` 转日期
- 注意 Windows 不支持负数时间戳（1970 前），需用 `timedelta` 替代

### 标签写入

从匹配结果提取后写入：
- FLAC (Vorbis): `album`、`tracknumber`、`date`
- MP3 (ID3): `TALB`、`TRCK`、`TDRC`

已有该字段则跳过，避免覆盖。

## 已知问题

- 网易云 v6 playlist API 的分页参数 (`n`/`s`) 对 tracks 列表不生效，必须用 trackIds + song/detail 方式
- Windows 下 `datetime.fromtimestamp` 不接受负数（1970 年前日期），需用 `timedelta` 替代
- 部分专辑 publishTime 为异常大值（如 916070400007），需加上限检查（如 4102444800000 = 2100年）
- 古典乐、影视原声等多艺术家曲目可能需额外模糊匹配

## 依赖

```bash
pip install mutagen NetEaseMusicApi requests
```

## 脚本

脚本位于 `scripts/` 目录下，需根据实际路径调整 `music_dir` 变量。
