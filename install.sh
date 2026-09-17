#!/usr/bin/env bash
# install.sh — kingdee-knowledge-kit 一键安装(Linux/macOS/WSL)
#
# 用法: bash install.sh [--root DIR] [--no-path] [--no-skills] [--no-verify]
#                    [--harness workbuddy,zcode,opencode,pi,agents]
#
# 两条路径(工单 #24):
#   主推 —— pipx(有 Python 环境时):  pipx install kingdee-knowledge-kit
#           由 pyproject.toml 的 [project.scripts] 提供 `kd`,升级/卸载交给 pipx 管。
#   兜底 —— 本脚本(无 Python 3 / Windows 双击 / 离线 / 要连技能一起装):
#           把 src/kd 拷进 $ROOT/lib,在 $ROOT/bin 生成可执行的 kd 启动器。
#
# 效果: kd CLI 装到 ~/.kingdee-kit,bin 加入 shell rc,技能装到 ~/.agents/skills
#       (workbuddy/zcode/opencode/pi 用软链挂同一份),最后跑装机自检。
#
# 去服务化(工单 #20/#22):本脚本不再拷 service/、不再拉服务、不再验 /health
#       ——本地 HTTP 服务与十个端点已删除,kd 进程内直连 kd.core。
set -e
ROOT="${HOME}/.kingdee-kit"
NO_PATH=0; NO_SKILLS=0; NO_VERIFY=0; HARNESS="all"
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="$2"; shift 2;;
    --no-path) NO_PATH=1; shift;;
    --no-skills) NO_SKILLS=1; shift;;
    --no-verify) NO_VERIFY=1; shift;;
    --harness) HARNESS="$2"; shift 2;;
    *) echo "未知参数: $1"; exit 2;;
  esac
done
REPO="$(cd "$(dirname "$0")" && pwd)"
command -v python3 >/dev/null 2>&1 || { echo "需要 python3 (3.8+)"; exit 1; }
echo "[install] python3: $(command -v python3)"
echo "[install] 安装到 $ROOT"

# 1. 库体 + 启动器
mkdir -p "$ROOT/lib" "$ROOT/bin"
rm -rf "$ROOT/lib/kd"
cp -r "$REPO/src/kd" "$ROOT/lib/kd"
# 包内数据文件:拆解规则/预算/限速档。缺它内核会静默退回内置默认值
# ——装出来的行为与开发中的不是同一个东西(历史上漏拷过 query_routes.json)。
[ -f "$ROOT/lib/kd/query_routes.json" ] || { echo "[install] ✗ 缺 lib/kd/query_routes.json" >&2; exit 1; }

cat > "$ROOT/bin/kd.py" <<'PYEOF'
#!/usr/bin/env python3
"""kd CLI 启动器(安装脚本生成;库体在 ../lib/kd)。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from kd.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
PYEOF
cat > "$ROOT/bin/kd" <<'SHEOF'
#!/usr/bin/env bash
# cd 到脚本目录再用相对路径调 python:规避 MSYS/GitBash 下 POSIX 绝对路径
# 传给原生 Windows python 时被改写的问题;Linux/macOS/WSL 行为不变
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" && exec python3 ./kd.py "$@"
SHEOF
chmod +x "$ROOT/bin/kd" "$ROOT/bin/kd.py"

# 2. PATH(bin 加入 shell rc,幂等)
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

# 3. 技能:实体只装一份,放在通用兼容目录 ~/.agents/skills/kingdee-knowledge
#    (agentskills.io 标准,Codex/Claude Code/opencode 原生读取);WorkBuddy/ZCode/pi
#    的目录用符号链接挂到同一份——升级一处、全家生效。链接失败(权限受限/MSYS
#    降级为拷贝)自动回退为真实拷贝。
SKILL_SRC="$REPO/skills/kingdee-knowledge/skills/kingdee-knowledge"
CANON="$HOME/.agents/skills/kingdee-knowledge"
install_canonical() {
  if [ -f "$CANON/SKILL.md" ] && [ ! -L "$CANON" ]; then
    cp "$CANON/SKILL.md" "$CANON/SKILL.md.bak"
  fi
  rm -rf "$CANON"
  mkdir -p "$CANON"
  cp -r "$SKILL_SRC/." "$CANON/"
  echo "[install] 技能实体 → $CANON"
}
link_or_copy() {  # $1=harness 名 $2=该 harness 的用户级技能目录
  local dest="$2"
  rm -rf "$dest"
  mkdir -p "$(dirname "$dest")"
  # MSYS/GitBash 的 ln -s 可能静默降级为拷贝:成功且可读才算链接成功
  if ln -s "$CANON" "$dest" 2>/dev/null && [ -e "$dest/SKILL.md" ] && [ -L "$dest" ]; then
    echo "[install] $1 → $dest(链接 → $CANON)"
  else
    rm -rf "$dest"
    mkdir -p "$dest"
    cp -r "$SKILL_SRC/." "$dest/"
    echo "[install] $1 → $dest(拷贝;链接创建失败已回退)"
  fi
}
if [ "$NO_SKILLS" -eq 0 ]; then
  install_canonical
  case "$HARNESS" in
    all) TARGETS="workbuddy zcode opencode pi";;   # agents 即实体本体
    *) TARGETS="$(echo "$HARNESS" | tr ',' ' ' | tr 'A-Z' 'a-z')";;
  esac
  for h in $TARGETS; do
    case "$h" in
      workbuddy) link_or_copy workbuddy "$HOME/.workbuddy/skills/kingdee-knowledge";;
      zcode)     link_or_copy zcode     "$HOME/.zcode/skills/kingdee-knowledge";;
      opencode)  link_or_copy opencode  "$HOME/.config/opencode/skills/kingdee-knowledge";;
      pi)        link_or_copy pi        "$HOME/.pi/agent/skills/kingdee-knowledge";;
      agents)    echo "[install] agents 即实体本体($CANON),无需挂载";;
      *) echo "[install] 未知 harness: $h(可选 workbuddy/zcode/opencode/pi/agents/all)";;
    esac
  done
fi

# 4. 装机自检:验「kd 可执行且真能跑通一条命令」。
#    判据是 tests/kd_regression.py 的离线组(工单 #21;旧的 verify_ksearch.py 已随
#    去服务化删除)——它既不联网也不需要服务,10 项全绿才放行。历史上安装器漏拷过
#    query_routes.json 与 kd.py,装出「残废版」却毫无报错,这里就是那道闸。
#    回归脚本用 src/kd_run.py 跑仓库内核,故不覆盖「$ROOT/bin/kd 装坏了」这种情况,
#    另用一条 health 命令补验装出来的那个 kd。
if [ "$NO_VERIFY" -eq 0 ]; then
  echo "[install] 冒烟验证:$ROOT/bin/kd health"
  "$ROOT/bin/kd" health >/dev/null || { echo "[install] ✗ kd 装出来后无法执行,装机失败" >&2; exit 1; }
  echo "[install] 装机自检:tests/kd_regression.py(离线组,不联网)"
  python3 "$REPO/tests/kd_regression.py" || {
    echo "[install] ✗ 回归未全绿,检查上方 FAIL 项" >&2; exit 1; }
  echo "[install] ✓ kd 可执行且回归通过"
fi

echo ""
echo "主推安装方式(有 Python 环境时优先用它,升级卸载交给 pipx 管):"
echo "  pipx install kingdee-knowledge-kit && kd health"
echo ""
echo "完成!试一试:"
echo "  kd health                                # 内核自检(库模式:无服务、无端口)"
echo "  kd ask \"信用额度怎么控制\" --topk 4      # 资料包(带 synthesisBrief 召回信号),交给调用方 agent 合成"
echo "  kd search \"信用额度控制\" --product 93"
echo "  kd read <id> --kind answer               # 读全文,kind 照抄 search 结果的 type"
echo ""
echo "本套件不合成回答(ADR-0008,零模型依赖):拿到 kd ask 资料包后,由 agent 按"
echo "docs/ANSWER-SPEC.md 合成;子代理提示词模板见技能 SKILL.md。"
