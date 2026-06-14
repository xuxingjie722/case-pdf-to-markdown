# 案卷 PDF → Markdown 转换工具

将刑事案卷 PDF 转换为结构化 Markdown，供大模型阅卷、分析使用。

## 启动方式

**双击** 工作区根目录下的 `案卷转换.command`，终端弹出菜单：

- 输入编号 → 转换对应案件的全部案卷 PDF
- 拖拽 PDF 文件到终端 → 转换指定 PDF
- 输入 `0` → 批量转换全部案件
- 输入 `q` → 退出

支持退格键删除、`Ctrl+U` 清空整行。

## 命令行

```bash
# 转换指定 PDF（一个或多个）
python3 scripts/convert_pdfs.py a.pdf b.pdf

# 转换指定案件的全部案卷 PDF
python3 scripts/convert_pdfs.py --case "陈文舒非法吸收公众存款案202604"

# 强制重新转换
python3 scripts/convert_pdfs.py --force a.pdf

# 试运行（仅预览，不实际转换）
python3 scripts/convert_pdfs.py --dry-run a.pdf
```

## 转换策略

| PDF 特征 | 选用工具 |
|----------|---------|
| 文字型、≤10MB、≤20页 | `mineru-open-api flash-extract`（免费、快速） |
| 文字型、较大/多页/扫描件 | `mineru-open-api extract`（高精度，支持 OCR） |
| 超大（>200页 或 >190MB） | 自动拆分为多卷 → 分别 extract → 合并 |
| MinerU 失败/超时 | 降级 → `markitdown` → `PyMuPDF` 本地提取 |

## 输出

```
{PDF所在目录}/
  {PDF文件名}md/
    {PDF文件名}.md              ← 转换结果
    .convert_state.json         ← 状态记录
```

转换后自动跳过已转换且源文件未变的 PDF，避免重复转换。

## 依赖

| 工具 | 说明 |
|------|------|
| `mineru-open-api` (v0.5.9+) | 主转换引擎，需 API 令牌 |
| `markitdown` (v0.1.6+) | 备选转换工具 |
| `PyMuPDF` (fitz) | 本地 PDF 分析、文本提取、拆分 |

## 文件说明

| 文件 | 说明 |
|------|------|
| `convert_pdfs.py` | 主脚本 — CLI、PDF 分类、转换调度、状态管理 |
| `pdf_utils.py` | 工具模块 — PDF 信息获取、扫描件检测、拆分、MD5 |
| `extract_text.py` | 备选脚本 — 纯文本提取与清洗（PyMuPDF 本地） |
