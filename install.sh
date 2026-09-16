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
cp "$REPO/service/docstore.py" "$ROOT/service/"
cp "$REPO/service/semantic_rerank.py" "$ROOT/service/"
# 多路检索规则文件(ADR-0005/0009):缺它则原句路/症状词路/实体规则/产品别名全部失效,
# 服务会静默退回内置默认值——装出来的行为与开发中的不是同一个东西。
cp "$REPO/service/query_routes.json" "$ROOT/service/"
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
else
  # 装机自检(--no-start 时也必须做):历史上安装器漏拷过 semantic_rerank.py 与
  # query_routes.json——服务要么起不来,要么静默退回默认值装出"残废版"却毫无报错。
  # 这里只验「文件齐 + 服务能起 + /health 报的配置是真配置」,不跑完整回归(那要消耗真实上游请求)。
  echo "[install] 装机自检"
  MISSING=0
  for f in service/kingdee-ksearch-service.py service/docstore.py service/semantic_rerank.py \
           service/query_routes.json bin/kd.py bin/kd; do
    [ -f "$ROOT/$f" ] || { echo "[install] ✗ 缺文件: $ROOT/$f" >&2; MISSING=1; }
  done
  [ "$MISSING" -eq 0 ] || { echo "[install] 装机自检失败:必要文件缺失" >&2; exit 1; }
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import json,sys;d=json.load(open('$ROOT/service/query_routes.json',encoding='utf-8'));sys.exit(0 if d.get('budget') else 1)" \
      || { echo "[install] ✗ query_routes.json 不含 budget 配置" >&2; exit 1; }
  fi
  echo "[install] ✓ 文件齐备(含 query_routes.json 规则文件)"
fi

echo ""
echo "完成!试一试:"
echo "  kd search \"信用额度控制\" --product 93"
echo "  kd read <id> --kind answer               # 读全文,kind 照抄 search 结果的 type"
echo "  kd ask \"信用额度怎么控制\" --topk 4      # 资料包(带 synthesisBrief 召回信号),交给调用方 agent 合成"
echo "  kd manifest                              # 全部能力清单"
echo ""
echo "本套件不合成回答(ADR-0008,零模型依赖):拿到 kd ask 资料包后,由 agent 按"
echo "docs/ANSWER-SPEC.md 合成;子代理提示词模板见技能 SKILL.md。"
