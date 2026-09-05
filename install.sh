#!/usr/bin/env bash
# install.sh — kingdee-knowledge-kit 一键安装(Linux/macOS)
# 用法: bash install.sh [--root DIR] [--no-path] [--no-skills] [--no-start] [--port 4097]
# 效果: 服务+kd CLI 装到 ~/.kingdee-kit,bin 加入 shell rc,技能装到 ~/.agents/skills,
#       启动服务并自动跑回归验证
set -e
ROOT="${HOME}/.kingdee-kit"
NO_PATH=0; NO_SKILLS=0; NO_START=0; PORT=4097
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="$2"; shift 2;;
    --no-path) NO_PATH=1; shift;;
    --no-skills) NO_SKILLS=1; shift;;
    --no-start) NO_START=1; shift;;
    --port) PORT="$2"; shift 2;;
    *) echo "未知参数: $1"; exit 2;;
  esac
done
REPO="$(cd "$(dirname "$0")" && pwd)"
command -v python3 >/dev/null 2>&1 || { echo "需要 python3 (3.8+)"; exit 1; }
echo "[install] python3: $(command -v python3)"
echo "[install] 安装到 $ROOT"

mkdir -p "$ROOT/service" "$ROOT/bin" "$ROOT/logs"
cp "$REPO/service/kingdee-ksearch-service.py" "$ROOT/service/"
cp "$REPO/cli/kd.py" "$ROOT/bin/"
cp "$REPO/cli/kd" "$ROOT/bin/"
chmod +x "$ROOT/bin/kd"

if [ "$NO_PATH" -eq 0 ]; then
  case ":$PATH:" in
    *":$ROOT/bin:"*) echo "[install] PATH 已包含 $ROOT/bin(跳过)";;
    *)
      for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
        [ -f "$rc" ] || continue
        if ! grep -q "kingdee-kit" "$rc" 2>/dev/null; then
          printf '\nexport PATH="$PATH:%s/bin"\n' "$ROOT" >> "$rc"
          echo "[install] 已写入 $rc(新终端生效)"
        fi
      done
      ;;
  esac
fi

if [ "$NO_SKILLS" -eq 0 ]; then
  DEST="$HOME/.agents/skills/kingdee-knowledge"
  echo "[install] 技能 → $DEST"
  mkdir -p "$DEST"
  [ -f "$DEST/SKILL.md" ] && cp "$DEST/SKILL.md" "$DEST/SKILL.md.bak"
  cp "$REPO/skills/kingdee-knowledge/skills/kingdee-knowledge/SKILL.md" "$DEST/"
fi

if [ "$NO_START" -eq 0 ]; then
  echo "[install] 启动服务(:$PORT)"
  bash "$REPO/scripts/start-service.sh" "$PORT" "$ROOT"
  echo "[install] 回归验证"
  KD_PY="$ROOT/bin/kd.py" KSEARCH_URL="http://127.0.0.1:$PORT" python3 "$REPO/tests/verify_ksearch.py"
fi

echo ""
echo "完成!试一试:"
echo "  kd search \"信用额度控制\" --product 93"
echo "  kd read <id> --kind answer               # 读全文,kind 照抄 search 结果的 type"
echo "  kd ask \"信用额度怎么控制\" --topk 4      # 资料包,交给你的 AI 合成"
echo "  kd ai \"信用额度怎么控制\"                # 一步合成带引用回答(需模型通道,自动降级)"
echo "  kd manifest                              # 全部能力清单"
echo ""
echo "kd ai 模型通道(可选,任意 OpenAI 兼容端点):"
echo "  export KAI_BASE=http://127.0.0.1:4090    # 默认值,勿带 /v1"
echo "  export KAI_MODEL=glm-5.3-flash           # 默认值"
