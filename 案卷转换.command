#!/bin/bash
# ───────────────────────────────────────────────────────────
#  案卷 PDF → Markdown 转换工具
#  双击启动，交互式运行
# ───────────────────────────────────────────────────────────

set -e

# 自动检测工作区：优先使用默认路径，否则用脚本所在目录
DEFAULT_WORKSPACE="/Users/jason/办公区/1.未结案件"
if [ -d "$DEFAULT_WORKSPACE" ]; then
    WORKSPACE="$DEFAULT_WORKSPACE"
else
    WORKSPACE="$(cd "$(dirname "$0")" && pwd)"
fi
SCRIPT="${WORKSPACE}/scripts/convert_pdfs.py"
# 如果 scripts/ 不存在，脚本就在当前目录
[ -f "$SCRIPT" ] || SCRIPT="${WORKSPACE}/convert_pdfs.py"
PYTHON="python3"

# ── 颜色 ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

clear
echo -e "${CYAN}╔════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     案卷 PDF → Markdown 转换工具          ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════╝${NC}"
echo ""

# ── 收集案件列表 ─────────────────────────────────────────
echo -e "${YELLOW}正在扫描案件文件夹...${NC}"
CASES=()
while IFS= read -r -d '' dir; do
    name=$(basename "$dir")
    # 跳过非案件文件夹
    [[ "$name" == ".claude" ]] && continue
    [[ "$name" == "Z其他材料" ]] && continue
    # 统计该案件下的 PDF 数量
    pdf_count=$(find "$dir" -name "*.pdf" -not -path "*md/*" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$pdf_count" -gt 0 ]; then
        CASES+=("$name|$pdf_count")
    fi
done < <(find "$WORKSPACE" -maxdepth 1 -type d -not -name ".*" -print0 2>/dev/null | sort -z)

if [ ${#CASES[@]} -eq 0 ]; then
    echo -e "${RED}未找到任何包含 PDF 的案件文件夹${NC}"
    exit 1
fi

# ── 显示菜单 ─────────────────────────────────────────────
echo -e "${GREEN}案件列表：${NC}"
echo ""
echo -e "  ${CYAN}[0]${NC} 全部案件"
echo ""

i=1
for entry in "${CASES[@]}"; do
    name="${entry%%|*}"
    count="${entry##*|}"
    printf "  ${CYAN}[%d]${NC} %-45s ${YELLOW}(%s 个 PDF)${NC}\n" "$i" "$name" "$count"
    ((i++))
done

echo ""
echo -e "${GREEN}────────────────────────────────────────────${NC}"
echo -e "  输入 ${CYAN}编号${NC} 转换对应案件"
echo -e "  或直接 ${CYAN}拖拽 PDF 文件${NC} 到此处"
echo -e "  输入 ${CYAN}q${NC} 退出"
echo -e "  支持 ${CYAN}退格键${NC} 删除 / ${CYAN}Ctrl+U${NC} 清空整行"
echo -e "${GREEN}────────────────────────────────────────────${NC}"
echo ""

# ── 输入循环（允许纠错重试）──────────────────────────────
while true; do
    # -e: 启用 readline（支持退格、方向键、Ctrl+U 清行等）
    # -r: 不转义反斜杠（文件路径兼容）
    read -e -r -p "> " INPUT || { echo ""; exit 0; }

    # 跳过空输入
    if [ -z "$INPUT" ]; then
        continue
    fi

    # 退出
    if [ "$INPUT" = "q" ] || [ "$INPUT" = "Q" ]; then
        echo "已退出"
        exit 0
    fi

    # 去掉首尾空格和引号（拖拽文件时 macOS 可能自动加引号）
    CLEAN=$(echo "$INPUT" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e "s/^['\"]//" -e "s/['\"]$//")
    # 去掉 Finder 拖拽时添加的转义反斜杠（如 [2026] → \[2026\]）
    # 去掉 Finder 拖拽时添加的转义反斜杠（如 [2026] → \[2026\]）
    CLEAN=$(echo "$CLEAN" | sed -e 's/\\\[/[/g' -e 's/\\\]/]/g' -e 's/\\(/(/g' -e 's/\\)/)/g' -e 's/\\{/{/g' -e 's/\\}/}/g')

    # ── 处理拖拽的 PDF 路径 ─────────────────────────────
    if [ -f "$CLEAN" ] && [[ "$CLEAN" == *.pdf ]]; then
        echo ""
        echo -e "${GREEN}转换 PDF: ${CLEAN}${NC}"
        echo ""
        cd "$WORKSPACE"
        $PYTHON "$SCRIPT" "$CLEAN"
        echo ""
        read -r -p "按回车键关闭..."
        exit 0
    fi

    # 处理多个 PDF（拖拽多个文件时，空格分隔）
    MULTI_PDFS=()
    for arg in $CLEAN; do
        arg=$(echo "$arg" | sed -e "s/^['\"]//" -e "s/['\"]$//")
        if [ -f "$arg" ] && [[ "$arg" == *.pdf ]]; then
            MULTI_PDFS+=("$arg")
        fi
    done

    if [ ${#MULTI_PDFS[@]} -gt 1 ]; then
        echo ""
        echo -e "${GREEN}转换 ${#MULTI_PDFS[@]} 个 PDF 文件${NC}"
        echo ""
        cd "$WORKSPACE"
        $PYTHON "$SCRIPT" "${MULTI_PDFS[@]}"
        echo ""
        read -r -p "按回车键关闭..."
        exit 0
    fi

    # ── 处理数字编号 ─────────────────────────────────────
    if [[ "$CLEAN" =~ ^[0-9]+$ ]]; then
        if [ "$CLEAN" -eq 0 ]; then
            # 全部案件
            echo ""
            echo -e "${GREEN}转换全部案件...${NC}"
            ALL_PDFS=()
            for entry in "${CASES[@]}"; do
                name="${entry%%|*}"
                case_dir="$WORKSPACE/$name"
                while IFS= read -r -d '' pdf; do
                    ALL_PDFS+=("$pdf")
                done < <(find "$case_dir" -name "*.pdf" -not -path "*md/*" -print0 2>/dev/null)
            done
            echo "共 ${#ALL_PDFS[@]} 个 PDF"
            echo ""
            cd "$WORKSPACE"
            $PYTHON "$SCRIPT" "${ALL_PDFS[@]}"
            echo ""
            read -r -p "按回车键关闭..."
            exit 0
        elif [ "$CLEAN" -ge 1 ] && [ "$CLEAN" -le ${#CASES[@]} ]; then
            idx=$((CLEAN - 1))
            entry="${CASES[$idx]}"
            name="${entry%%|*}"
            echo ""
            echo -e "${GREEN}转换案件: ${name}${NC}"
            echo ""
            cd "$WORKSPACE"
            $PYTHON "$SCRIPT" --case "$name"
            echo ""
            read -r -p "按回车键关闭..."
            exit 0
        else
            echo -e "${RED}✗ 无效编号: ${CLEAN}（请输入 0-${#CASES[@]}）${NC}"
            echo ""
            continue
        fi
    fi

    # ── 处理直接输入的案件名 ─────────────────────────────
    MATCH="$WORKSPACE/$CLEAN"
    if [ -d "$MATCH" ]; then
        echo ""
        echo -e "${GREEN}转换案件: ${CLEAN}${NC}"
        echo ""
        cd "$WORKSPACE"
        $PYTHON "$SCRIPT" --case "$CLEAN"
        echo ""
        read -r -p "按回车键关闭..."
        exit 0
    fi

    # 无匹配 — 提示重试
    echo -e "${RED}✗ 无法识别: ${CLEAN}${NC}"
    echo -e "${YELLOW}请确认 PDF 路径或案件编号正确，重新输入（q 退出）${NC}"
    echo ""
done
