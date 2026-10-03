#!/usr/bin/env bash
# install.sh — kingdee-knowledge-kit 一键安装(Linux/macOS/WSL)
#
# 用法: bash install.sh [--root DIR] [--no-path] [--no-skills] [--no-verify]
#                    [--harness workbuddy,zcode,opencode,pi,agents]
#        管道形式: curl -fsSL .../install.sh | bash [-s -- <同样的开关>]
#
# 两条路径(工单 #24):
#   主推 —— pipx(有 Python 环境时):  pipx install kingdee-knowledge-kit
#           由 pyproject.toml 的 [project.scripts] 提供 `kd`,升级/卸载交给 pipx 管。
#   兜底 —— 本脚本(无 Python 3 / Windows 双击 / 离线 / 要连技能一起装):
#           把 src/kd 拷进 $ROOT/lib,在 $ROOT/bin 生成可执行的 kd 启动器。
#           ⚠️ 本脚本**自身不含源码**:库体(src/kd)、技能(skills/)、验证闸门
#           (tests/kd_regression.py)都从**检出目录**取,故它先自己找检出目录
#           —— 见下方「仓库根发现」;管道形式没有"脚本所在目录",会先浅克隆。
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
REPO=""
REPO_URL="https://github.com/Drivpe/kingdee-knowledge-kit.git"
TMP_REPO=""
cleanup() { [ -n "$TMP_REPO" ] && rm -rf "$TMP_REPO"; return 0; }
trap cleanup EXIT INT TERM HUP

# 仓库根发现(工单 I1):原先这里只有一行 `REPO="$(cd "$(dirname "$0")" && pwd)"`,
# 它在管道形式(curl … | bash)下把 $0 解析成 "bash",dirname 得到当前目录 —— 于是
# REPO 指向 $PWD,第一步 `cp -r "$REPO/src/kd"` 直接报
# `cp: cannot stat '.../src/kd'` 并 exit 1。用户看到的是 cp 的抱怨,拿不到任何
# "该怎么装"的信息,而这段管道命令正是 README 列为一等公民的形式。
# 故改为三级探测,任一级命中都比"半截失败"好:
#   ① 脚本自身所在目录(在检出目录里原地运行 —— 稳态用法)
#   ② 当前目录(管道形式下若恰好在检出目录内,同样成立)
#   ③ 浅克隆到临时目录(管道形式的兜底;需要 git + 能访问 GitHub)
# 三级都落空 ⇒ 打印可行动的用法后以非 0 退出(绝不继续跑半截流程)。
_self="${BASH_SOURCE[0]:-$0}"
if [ -n "$_self" ] && [ -f "$_self" ]; then
  _d="$(cd "$(dirname "$_self")" 2>/dev/null && pwd)" || _d=""
  if [ -n "$_d" ] && [ -d "$_d/src/kd" ]; then REPO="$_d"; fi
fi
if [ -z "$REPO" ] && [ -d "$PWD/src/kd" ]; then REPO="$PWD"; fi
if [ -z "$REPO" ] && command -v git >/dev/null 2>&1; then
  echo "[install] 未在检出目录内运行,先把仓库浅克隆到临时目录(装完即删)"
  _tmp="$(mktemp -d 2>/dev/null)" || _tmp=""
  # 克隆必须**有界且不交互**:
  #   * GIT_TERMINAL_PROMPT=0 —— 仓库不可达/私有/需要凭据时,git 默认会弹用户名密码提示,
  #     在"一键脚本"里那就是一个永远等不到输入的挂起;关掉提示,失败得干脆。
  #   * timeout —— 实测本机到 GitHub 会间歇性挂住(同一条命令有时 0.3s 返回、有时 30s 无响应),
  #     而"逐字节复制一个不会动的终端"比报错更糟。macOS 自带的是 gtimeout(装了 coreutils 才有),
  #     两者都没有时才不限时 —— 此时仍受 GIT_TERMINAL_PROMPT=0 保护。
  if command -v timeout >/dev/null 2>&1; then _TO="timeout 90"
  elif command -v gtimeout >/dev/null 2>&1; then _TO="gtimeout 90"
  else _TO=""; fi
  if [ -n "$_tmp" ] && GIT_TERMINAL_PROMPT=0 $_TO git clone --depth 1 --quiet "$REPO_URL" "$_tmp"; then
    # 克隆到的东西也要过一遍"布局对不对"这道门:远程落后于本脚本的期望布局时
    # (实测存在:远程 HEAD 停在 v6.3,src/kd 下放的是 v6.6 已删除的 query_routes.json
    # 而不是 contract.json),收下它只会把失败推迟到下一步,变成"缺 lib/kd/contract.json"
    # 这种看起来像仓库坏了的报错。故这里直接判死并回落,由下面的用法块告诉用户该怎么做。
    if [ -d "$_tmp/src/kd" ] && [ -f "$_tmp/src/kd/contract.json" ]; then
      TMP_REPO="$_tmp"; REPO="$_tmp"
    else
      echo "[install] ✗ 克隆到的检出不含 src/kd/contract.json —— 该远程分支与本脚本期望的布局不一致(通常是远程未同步到本版本),不收下这棵树" >&2
      rm -rf "$_tmp"
    fi
  else
    [ -n "$_tmp" ] && rm -rf "$_tmp"
  fi
fi
if [ -z "$REPO" ]; then
  cat >&2 <<'USAGE_EOF'
[install] ✗ 找不到仓库检出目录:既不是从检出目录内运行,也没能克隆到仓库。

  本脚本自身不含源码 —— 库体在 <检出目录>/src/kd、技能在 <检出目录>/skills、
  「离线组全绿才放行」的验证闸门在 <检出目录>/tests/kd_regression.py。
  所以必须有一个可用的检出目录,管道形式也必须能把它取回来。

  正确用法(任选其一):
    1) 先克隆再原地运行(推荐;离线机器也适用):
         git clone --depth 1 https://github.com/Drivpe/kingdee-knowledge-kit.git
         cd kingdee-knowledge-kit && ./install.sh
    2) 在已有的检出目录内运行(路径按你本地实际位置替换):
         bash /path/to/kingdee-knowledge-kit/install.sh
    3) 管道形式(需要 git 且能访问 GitHub —— 脚本会自己浅克隆到临时目录):
         curl -fsSL https://raw.githubusercontent.com/Drivpe/kingdee-knowledge-kit/main/install.sh | bash
       ⚠️ 管道下发的是**远程那一版**脚本:远程未同步时拿到的是旧脚本,本脚本的探测
          逻辑根本不参与 —— 那种情况请走 1)。

  想跳过验证闸门不足以绕过本错误:闸门读的 tests/ 与被装的 src/kd 同属一个检出目录。
USAGE_EOF
  exit 1
fi
echo "[install] 检出目录: $REPO"

command -v python3 >/dev/null 2>&1 || { echo "需要 python3 (3.8+)"; exit 1; }
echo "[install] python3: $(command -v python3)"
echo "[install] 安装到 $ROOT"

# 1. 库体 + 启动器
mkdir -p "$ROOT/lib" "$ROOT/bin"
rm -rf "$ROOT/lib/kd"
cp -r "$REPO/src/kd" "$ROOT/lib/kd"
# 包内数据文件:对外契约声明(contract.json)。**v6.6 起它是唯一一个数据文件**
# ——原 query_routes.json(拆解规则/预算/限速档)随拆词器与预算机制一并删除,
# 其仍有效的两个值(路数上限、限速档)并入 contract.json 的 limits 段(ADR-0016)。
# 缺它则内核静默退回内置兜底键集与默认值 —— 装出来的行为与开发中的不是同一个东西
# (历史上漏拷过 query_routes.json)。故仍在此显式校验。
for f in contract.json; do
  [ -f "$ROOT/lib/kd/$f" ] || { echo "[install] ✗ 缺 lib/kd/$f" >&2; exit 1; }
done

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
#    去服务化删除)——它既不联网也不需要服务,全绿才放行。历史上安装器漏拷过
#    query_routes.json 与 kd.py,装出「残废版」却毫无报错,这里就是那道闸。
#    回归脚本用 src/kd_run.py 跑仓库内核,故不覆盖「$ROOT/bin/kd 装坏了」这种情况,
#    另用一条 health 命令补验装出来的那个 kd。
#    ⚠️ 公开面守卫 scripts/check_core_surface.py 已删除(决策 D2,2026-09-27):
#    判据 1/3/4/5/6 只用一次就再没开工过,而"公开面钩子函数"本身零流失率
#    (把它挂回 kd.core 要走 cheerleading 流程);真正防静默失效的三条
#    (版本单一真源 / health 依赖可解析 / kind 集合一致)已迁进回归离线组,
#    故这里只需跑回归——少一道闸,但闸后那条河已经不需要它了。
if [ "$NO_VERIFY" -eq 0 ]; then
  echo "[install] 冒烟验证:$ROOT/bin/kd health"
  "$ROOT/bin/kd" health >/dev/null || { echo "[install] ✗ kd 装出来后无法执行,装机失败" >&2; exit 1; }
  # 闸门路径取自上面探测到的 $REPO(不是 $0、也不是 $PWD),故原地运行与管道+浅克隆
  # 两种形态下都指向同一个检出目录。仍然显式校验存在性:缺 tests/ 时给一句人话,
  # 而不是让 python 抛 FileNotFoundError —— 闸门读不到就是"没放行",不可能是通过。
  REGRESSION="$REPO/tests/kd_regression.py"
  [ -f "$REGRESSION" ] || {
    echo "[install] ✗ 检出目录缺 $REGRESSION,无法执行「离线组全绿才放行」那道闸门" >&2; exit 1; }
  echo "[install] 装机自检:$REGRESSION(离线组,不联网)"
  python3 "$REGRESSION" || {
    echo "[install] ✗ 回归未全绿,检查上方 FAIL 项" >&2; exit 1; }
  echo "[install] ✓ kd 可执行且回归通过"
fi

echo ""
echo "主推安装方式(有 Python 环境时优先用它,升级卸载交给 pipx 管):"
echo "  pipx install kingdee-knowledge-kit && kd health"
echo ""
echo "完成!试一试:"
echo "  kd health                                # 内核自检(库模式:无服务、无端口)"
echo "  kd search --kw \"信用额度控制\" --product 93   # 出清单(标题级,带 hitRoutes/routes),你自己挑"
echo "  kd search --kw \"信用额度\" --kw \"应收单 信用\"   # 拆好词按序传入:每个 --kw 一路,内核不再自行拆词"
echo "  kd read <id> --kind question             # 取全文,kind 照抄 search 结果的 type"
echo ""
echo "本套件不合成回答(ADR-0008,零模型依赖):kd search 只出清单,挑中的条目用 kd read 取全文,"
echo "再由你按 docs/ANSWER-SPEC.md 自己合成。排序由上游综合排序决定,内核只去重、零评分。"
